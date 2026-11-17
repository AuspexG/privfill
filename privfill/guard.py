"""The guard: tells a tool hook whether a call may go ahead.

An assistant tool runs `privfill.py guard` before each tool call, with the call as JSON on
standard input (format in docs/guard.md). The answer is the exit code: 0 allows, 2 denies. A
denial prints one line that names the kind of thing the call would touch, never a path or a value.

Exit codes other than these two never come from the guard itself: it answers before any store is opened.

Denied: a call that reads, changes, lists or searches the data directory or a registered output,
directly or through a shell command. Allowed: everything else, and this program's own commands.
Whatever the guard cannot understand is denied.

This is a check of what a call says, not a sandbox: it does not follow shell variables it cannot
see, scripts, encodings or programs that open files on their own. The limits are in docs/guard.md.
"""
import glob
import itertools
import json
import os
import re
import shlex
import sys

from .registry import RegistryError, content_hash

ALLOW, DENY = 0, 2

DATA = "it would touch the privfill data directory"
OUTPUT = "it would touch a filled output"
SWEEP = "it would list or search a place that holds a filled output or the privfill data directory"
SUBSTITUTION = "a privfill command may not contain a command substitution"
PAYLOAD = "the hook payload was not understood"
REGISTRY = "the output registry could not be read"

# Words in the key of a tool input (file_path, notebookPath, paths) that say its value is a path, or a shell command.
PATH_KEYS = {"path", "file", "filename", "filepath", "dir", "directory", "directories", "folder", "uri", "glob", "notebook"}
SHELL_KEYS = {"command", "cmd", "script"}
SHELLS = {"sh", "bash", "zsh", "dash", "ksh", "fish"}
PREFIXES = {"sudo", "env", "command", "exec", "nohup", "time", "xargs", "eval",
            "if", "then", "else", "elif", "do", "while", "until", "!", "{"}
SWEEPERS = {"find", "fd", "fdfind", "tree", "rg", "ag", "ack", "tar", "zip", "rsync"}  # always look below a directory
SWEEP_FLAGS = {"grep": "rR", "egrep": "rR", "fgrep": "rR", "zgrep": "rR", "ls": "R", "dir": "R",
               "cp": "rRa", "diff": "r", "scp": "r"}  # ... or do so with one of these short flags
LISTERS = {"ls", "dir"}  # with no operand, these act on the working directory
MAKERS = {"mkdir"}  # naming a directory to these shows nothing of what it holds
OPERATOR = re.compile(r"[();<>|&]+")
SPLIT = re.compile(r"[\s'\"()=,;:<>|&`]+")
MAX_MATCHES = 5000  # paths looked at per shell wildcard


def inside(path, directory):
    return path == directory or path.startswith(directory.rstrip(os.sep) + os.sep)


def resolve(text, cwd):
    """The real path a tool or shell would reach for this text."""
    if text.startswith("file://"):
        text = text[len("file://"):]
    return os.path.realpath(os.path.join(cwd, os.path.expanduser(text)))


def glob_regex(pattern):
    """A glob as a regular expression over a /-separated path: ** crosses directories, * and ? do not."""
    out, i = "", 0
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif pattern.startswith("**", i):
            out, i = out + ".*", i + 2
        elif c == "*":
            out, i = out + "[^/]*", i + 1
        elif c == "?":
            out, i = out + "[^/]", i + 1
        elif c == "[" and "]" in pattern[i + 2:]:
            end = pattern.index("]", i + 2)
            body = pattern[i + 1:end]
            body = "^" + body[1:] if body[0] == "!" else body
            out, i = out + "[" + body.replace("\\", "\\\\") + "]", end + 1
        elif c == "{" and "}" in pattern[i:]:
            end = pattern.index("}", i)
            out, i = out + "(?:" + "|".join(glob_regex(p) for p in pattern[i + 1:end].split(",")) + ")", end + 1
        else:
            out, i = out + re.escape(c), i + 1
    return out


class Guard:
    def __init__(self, data_dir, outputs, program):
        self.data_dir = os.path.realpath(data_dir)
        self.program = os.path.realpath(program)
        self.paths = {o["path"] for o in outputs}
        self.sizes = {o["size"] for o in outputs}
        self.hashes = {(o["size"], o["sha256"]) for o in outputs}
        self.variables = {}  # shell variables the command being checked has set so far

    # What one path means.

    def file(self, path):
        """Why a call may not touch this real path, or None."""
        if inside(path, self.data_dir):
            return DATA
        if path in self.paths and os.path.lexists(path):
            return OUTPUT
        try:  # an output that was moved or copied still has its registered content
            if os.path.isfile(path) and os.path.getsize(path) in self.sizes \
                    and (os.path.getsize(path), content_hash(path)) in self.hashes:
                return OUTPUT
        except OSError:
            return OUTPUT if os.path.isfile(path) else None  # unreadable to us, so unknown: refuse
        return None

    def held(self, directory, deep):
        """The protected files in this real directory (deep: at any depth), relative to it."""
        found = [p for p in self.paths if os.path.lexists(p) and inside(p, directory)]
        if deep and inside(self.data_dir, directory):
            found += [os.path.join(d, f) for d, _, files in os.walk(self.data_dir) for f in files]
        if not deep:
            found = [p for p in found if os.path.dirname(p) == directory]
        return [os.path.relpath(p, directory) for p in found]

    def directory(self, path, deep, matches=None):
        """Why a call may not list or search this real directory, or None."""
        if inside(path, self.data_dir):
            return DATA
        held = self.held(path, deep)
        if matches:
            held = [rel for rel in held if matches(rel)]
        return SWEEP if held else None

    def path(self, text, cwd, deep=True):
        """Why a call may not be given this path, whether it is a file or a directory, or None."""
        path = resolve(text, cwd)
        return self.file(path) or (self.directory(path, deep) if os.path.isdir(path) else None)

    # The tools.

    def decide(self, payload):
        """Why the call in this hook payload is denied, or None to allow it."""
        if not isinstance(payload, dict):
            return PAYLOAD
        tool, call, cwd = payload.get("tool_name"), payload.get("tool_input"), payload.get("cwd")
        if not isinstance(tool, str) or not isinstance(call, dict):
            return PAYLOAD
        if not isinstance(cwd, str) or not os.path.isabs(cwd):
            cwd = os.getcwd()
        self.variables = {}
        if tool == "Bash":
            return self.shell(call.get("command"), cwd)
        if tool == "Glob":
            return self.glob(call, cwd)
        if tool == "Grep":
            return self.grep(call, cwd)
        return self.other(call, cwd)

    def glob(self, call, cwd):
        pattern, root = call.get("pattern"), call.get("path")
        if not isinstance(pattern, str) or not isinstance(root, (str, type(None))):
            return PAYLOAD
        root = resolve(root or ".", cwd)
        if os.path.isabs(os.path.expanduser(pattern)):  # the pattern carries its own root
            pattern, root = os.path.relpath(os.path.realpath(os.path.expanduser(pattern)), "/"), "/"
        full = re.compile(glob_regex(os.path.normpath(pattern)))
        return self.file(root) or self.directory(root, True, full.fullmatch)

    def grep(self, call, cwd):
        root, only = call.get("path"), call.get("glob")
        if not isinstance(root, (str, type(None))) or not isinstance(only, (str, type(None))):
            return PAYLOAD
        root = resolve(root or ".", cwd)
        matches = None
        if only and not only.startswith("!"):  # the filter may apply at any depth, so try every tail of the path
            tail = re.compile(glob_regex(only.lstrip("/")))
            matches = lambda rel: any(tail.fullmatch(rel.split(os.sep, n)[-1]) for n in range(rel.count(os.sep) + 1))  # noqa: E731
        return self.file(root) or self.directory(root, True, matches)

    def other(self, value, cwd, key=""):
        """Any other tool: every input whose key says it is a path or a shell command is checked as one."""
        if isinstance(value, dict):
            return next(filter(None, (self.other(v, cwd, str(k)) for k, v in value.items())), None)
        if isinstance(value, list):
            return next(filter(None, (self.other(v, cwd, key) for v in value)), None)
        if not isinstance(value, str):
            return None
        words = re.findall(r"[a-z]+", re.sub(r"([A-Z])", r"_\1", key).lower())
        words = set(words) | {w[:-1] for w in words if w.endswith("s")}
        if words & SHELL_KEYS:
            return self.shell(value, cwd)
        if words & PATH_KEYS:
            return self.path(value, cwd)
        return None

    # Shell commands.

    def shell(self, command, cwd):
        if not isinstance(command, str):
            return PAYLOAD
        text = command.replace("`", " ; ").replace("\n", " ; ")
        try:
            lexer = shlex.shlex(text, posix=True, punctuation_chars=True)
            lexer.whitespace_split, lexer.commenters = True, ""
            tokens = list(lexer)
        except ValueError:  # quotes that do not close: check every word, as a command of no known kind
            return next(filter(None, (self.word(w, cwd, False) for w in SPLIT.split(command))), None)
        segment = []
        for token in tokens + [";"]:
            if not OPERATOR.fullmatch(token):
                segment.append(token)
                continue
            reason, cwd = self.segment(segment, cwd)
            if reason:
                return reason
            segment = []
        return None

    def segment(self, words, cwd):
        """One simple command: (why it is denied or None, the working directory after it)."""
        while words and (words[0] in PREFIXES or re.fullmatch(r"\w+=.*", words[0])):
            if words[0] not in PREFIXES:
                name, value = words[0].split("=", 1)
                self.variables[name] = self.variable(value)
            words = words[1:]
        if not words:
            return None, cwd
        name = os.path.basename(words[0])
        if self.own(words, cwd):
            return (SUBSTITUTION if any("$(" in w for w in words) else None), cwd
        operands = [w for w in words[1:] if not w.startswith("-")]
        if name in ("cd", "pushd"):
            cwd = resolve(self.variable(operands[0]) if operands else "~", cwd)
            return (DATA if inside(cwd, self.data_dir) else None), cwd
        if name in SHELLS | {"eval"}:
            for flag, script in zip(words, words[1:]):
                if name == "eval" or (flag.startswith("-") and not flag.startswith("--") and "c" in flag):
                    reason = self.shell(script, cwd)
                    if reason:
                        return reason, cwd
        flags = {c for w in words[1:] if w.startswith("-") and not w.startswith("--") for c in w[1:]}
        deep = name in SWEEPERS or "--recursive" in words or bool(flags & set(SWEEP_FLAGS.get(name, ""))) \
            or (name == "git" and "grep" in words[1:3])
        for word in words:
            reason = self.word(word, cwd, deep, name in MAKERS)
            if reason:
                return reason, cwd
        named = any(os.path.lexists(resolve(self.variable(w), cwd)) for w in operands)
        if (deep or name in LISTERS) and not named:  # no operand names a place: it acts on the working directory
            return self.directory(os.path.realpath(cwd), deep), cwd
        return None, cwd

    def own(self, words, cwd):
        """Whether this simple command runs this program: its launcher, or a copy of it, optionally through Python."""
        if re.fullmatch(r"python[\d.]*", os.path.basename(words[0])):
            words = list(itertools.dropwhile(lambda w: w.startswith("-") and w not in ("-c", "-m"), words[1:]))
        if not words:
            return False
        path = resolve(words[0], cwd)
        try:
            if path == self.program:
                return True
            if os.path.basename(path) != os.path.basename(self.program) or not os.path.isfile(path):
                return False
            with open(path, "rb") as a, open(self.program, "rb") as b:
                return a.read() == b.read()
        except OSError:
            return False

    def variable(self, text):
        """The text with $NAME and ${NAME} replaced, where the command or the environment gives a value."""
        def value(m):
            name = m.group(1) or m.group(2)
            return self.variables.get(name, os.environ.get(name, m.group(0)))

        return re.sub(r"\$(\w+)|\$\{(\w+)\}", value, text)

    def word(self, word, cwd, deep, maker=False):
        """Why one word of a command is denied, or None. A word can hold paths inside it: in a
        quoted script, after an option's = sign, in a list. Those parts count if they look like paths."""
        word = self.variable(word)
        parts = {word} | {p for p in SPLIT.split(word) if "/" in p or p.startswith("~")}
        for part in filter(None, parts):
            for text in self.expand(part, cwd):
                path = resolve(text, cwd)
                reason = self.file(path)
                if not reason and os.path.isdir(path) and not maker:
                    reason = self.directory(path, deep)
                if reason:
                    return reason
        return None

    @staticmethod
    def expand(text, cwd):
        """The text itself or, if it has shell braces or wildcards, what they stand for."""
        braces = re.search(r"\{([^{}]*,[^{}]*)\}", text)
        if braces:
            for choice in braces.group(1).split(","):
                yield from Guard.expand(text[:braces.start()] + choice + text[braces.end():], cwd)
            return
        yield text
        if any(c in text for c in "*?["):
            try:
                yield from itertools.islice(glob.iglob(os.path.join(cwd, os.path.expanduser(text))), MAX_MATCHES)
            except (OSError, re.error, ValueError):
                pass


def run(registry, program, stdin=None, stderr=None):
    """Answer one hook call read from stdin: the exit code, with a path-free reason on stderr if it denies."""
    stdin, stderr = stdin or sys.stdin, stderr or sys.stderr
    try:
        payload = json.load(stdin)
        reason = Guard(os.path.dirname(registry.path), registry.outputs(), program).decide(payload)
    except RegistryError:
        reason = REGISTRY
    except Exception:  # anything unforeseen must deny: a hook that crashes is treated as "go ahead"
        reason = PAYLOAD
    if reason is None:
        return ALLOW
    print(f"privfill guard: denied; {reason}.", file=stderr)
    return DENY
