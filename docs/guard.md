# The output registry and the guard

The rules in `PROTOCOL.md` section 5 ask the assistant not to read the vault or a filled output. The guard lets the assistant's own tool enforce the two rules that can be checked mechanically: before each tool call, the tool asks `privfill guard` whether the call may go ahead.

Connecting the guard to a tool's hook settings, and checking it in a live session, is not covered here.

## The output registry

Every file the program fills with real values is recorded in `outputs.json` in the data directory (the directory of the vault, `~/.privfill` by default):

```json
{"privfill_outputs": 1,
 "outputs": [{"path": "/real/path/of/the/output", "sha256": "<hash of its content>", "size": 123}]}
```

| What is recorded | When |
|---|---|
| the output of `render` | always |
| a file given to `fill` | when at least one placeholder was filled |
| the archive written by `pack` | always |

The registry holds no value. A path can contain one, because an output may be named after an item, so the registry exists only in the data directory (mode 600, directory 700) and no command prints anything from it. It is replaced in one step, never written in place.

An output that cannot be registered is not kept: `render` and `pack` remove what they wrote and exit with code 1. `fill` works in place and cannot undo, so it reports the file as failed and says that the guard does not protect it.

Entries are never removed. Rendering to the same path again adds an entry for the new content and keeps the old one, so an earlier copy that was moved away is still recognised.

## The guard command

```
privfill.py guard < payload.json
```

| Exit code | Meaning |
|---|---|
| 0 | Allow. Nothing is printed. |
| 2 | Deny. One line on standard error says why. |

The reason is one of a fixed set of sentences. It names the kind of thing the call would touch and never contains a path, a file name or a value:

| Reason | Cause |
|---|---|
| it would touch the privfill data directory | the call names the data directory or something in it |
| it would touch a filled output | the call names a registered output, or a file with the content of one |
| it would list or search a place that holds a filled output or the privfill data directory | a listing, search or recursive command would cover one |
| a privfill command may not contain a command substitution | `$(...)` inside the arguments of a privfill command |
| the hook payload was not understood | the input is not the format below, or the guard itself failed |
| the output registry could not be read | `outputs.json` is damaged; every call is denied until it is repaired or removed |

Calls to the program's own MCP tools (`mcp__<server>__privfill_keys`, `_form`, `_render`, `_pack`, under whatever server name they were registered) are allowed like the program's own commands, so `privfill_pack` may bundle a filled output. Any other MCP tool is judged by its inputs like a file tool.

The guard never asks for the passphrase and does not need the `cryptography` library.

### Assumed payload format

The guard reads one JSON object, the format Claude Code passes to a `PreToolUse` hook:

```json
{
  "session_id": "...",
  "transcript_path": "...",
  "cwd": "/the/working/directory",
  "permission_mode": "default",
  "hook_event_name": "PreToolUse",
  "tool_name": "Bash",
  "tool_input": { "command": "ls src", "description": "..." },
  "tool_use_id": "..."
}
```

Only three keys are used:

| Key | Use |
|---|---|
| `tool_name` | a string; selects how `tool_input` is read |
| `tool_input` | an object; the arguments of the call |
| `cwd` | relative paths are resolved against it; when absent, the guard's own working directory |

Exit code 2 is what Claude Code treats as "block this call and show standard error to the assistant". Other non-zero codes do not block there, which is why the guard turns every internal failure into a denial.

How `tool_input` is read:

| `tool_name` | Keys read |
|---|---|
| `Bash` | `command`, a shell command (see below) |
| `Glob` | `pattern`, and `path` (default: `cwd`) as the directory searched |
| `Grep` | `path` (default: `cwd`) as the file or directory searched, and `glob` as a file filter |
| anything else | every string, at any depth, under a key with one of the words `path`, `file`, `filename`, `filepath`, `dir`, `directory`, `folder`, `uri`, `glob`, `notebook` (plural or part of a longer key such as `file_path` or `notebookPath`) is read as a path; under a key with `command`, `cmd` or `script`, as a shell command. Other keys, such as the `content` of a write, are not read. |

This covers `Read`, `Edit`, `Write` and `NotebookEdit` (`file_path`, `notebook_path`) and tools from other sources that name their inputs in the usual way.

The sample payloads in `tests/hook_payloads.json` were written to this format by hand; they were not captured from a running session. The tests replay each one and compare the decision. In the samples, `$WORK` is a project directory, `$DATA` the data directory, `$PROGRAM` the launcher and `$COPY` an installed copy of it.

### Decisions

A path is resolved first: `~` and `file://` are expanded, relative paths are joined to `cwd`, and symbolic links are followed.

- **A file** is denied if it is in the data directory, if it exists at a registered path, or if its size and content hash match a registered output. The last case is how an output that was moved, renamed or copied is still recognised when a call names it.
- **A directory given to a search or listing** (`Glob`, `Grep`, a path key of another tool) is denied if a registered output or a file of the data directory lies anywhere below it. A `Glob` pattern or a `Grep` `glob` filter is honoured: the call is allowed if no protected file matches it, so a search for `*.py` can still run from a project root whose `out/` holds outputs. A filter that starts with `!` is not analysed and does not help.
- **A shell command** is split into simple commands at `;`, `&&`, `||`, `|`, redirections, parentheses, backticks and line ends. In each one:
  - every word is checked as a file, including after `$HOME`-style variables, variables set earlier in the same command, `{a,b}` lists and wildcards are expanded;
  - a part of a word that looks like a path (it contains `/` or starts with `~`) is checked too, which catches paths inside a quoted script or after `--option=`;
  - a word that is a directory is denied if it directly holds an output, and, for commands that look below a directory (`find`, `tree`, `rg`, `tar`, `zip`, `rsync`, `git grep`, and `grep`, `ls`, `cp`, `diff`, `scp` with their recursive flags), if it holds one at any depth. `mkdir` is exempt;
  - `ls` and the recursive commands, given no existing path, are checked against the working directory;
  - `cd` changes the working directory for the rest of the command, and a `cd` into the data directory is denied;
  - the script after `sh -c`, `bash -c` and similar, and after `eval`, is checked as a command of its own.
- **The program's own commands are allowed.** A simple command is one of them if its first word, or the script after `python`, is this program's launcher or a file named `privfill.py` with the same content (an installed copy). Its arguments are not checked, since `render` and `pack` are given outputs by design; anything the program writes from them is registered in turn. Other simple commands on the same line are checked as usual.

### Limits

The guard judges what a call says. It is not a sandbox, and it does not make the caller unable to reach a value.

- It does not follow what it cannot see: a variable set outside the command, a script or program that opens files on its own, an encoded command, an alias.
- A moved output is recognised only when a call names it. A search from above does not hash every file it would cover, so a search that sweeps over a moved copy is not denied.
- A shell wildcard is expanded even when the shell would not expand it (inside quotes), and prose that mentions a protected path (in a commit message or a here-document) is treated as naming it. Both lead to denials of harmless commands; put such text in a file written with the file tool instead.
- A search from a directory that holds an output is denied unless a filter excludes the output. Keeping outputs outside the project, or at least in their own directory as rule 5 asks, keeps this rare.
- The copy check for the program's own commands compares the launcher only, not the package beside it.
- Tools whose inputs are not named as paths or commands pass unchecked.
