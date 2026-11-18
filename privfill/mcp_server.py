"""A Model Context Protocol server over standard input and output.

Typed tools wrap the commands `keys`, `form`, `render` and `pack`, so the contract travels with the
tool definitions. Each tool runs the same code as its command-line counterpart (`--json` mode) and
returns that command's JSON object: item names, counts, statuses and the exit code, never a value.
No tool runs a command, takes a value or returns one. Messages are JSON-RPC 2.0, one per line.
Only the Python standard library is used.
"""
import json
import sys

from . import __version__
from .cli import main
from .normalize import NORMS
from .protocol import PROTOCOL_VERSION

SUPPORTED_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")

EXIT_CODES = ("Exit codes, given as `code` in the result: 0 ok, 1 usage or runtime error, 2 a placeholder names an "
              "item that does not exist or a format does not apply, 3 the person cancelled the form, "
              "4 the entry protocol is invalid, 5 the vault was not unlocked (wrong passphrase; ask the person "
              "before trying again). `isError` is true whenever `code` is not 0.")
PATH_NOTE = "May contain placeholders such as {{name}}, which are filled locally."

FIELD_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["name"],
    "description": "One question. The protocol holds questions, never answers: no other key is accepted.",
    "properties": {
        "name": {"type": "string", "minLength": 1, "pattern": r"^[^|{}\n]+$", "not": {"enum": ["Item", "项目"]},
                 "description": "Item name, also the name used in placeholders {{name}}. Unique within the form."},
        "label": {"type": "string", "description": "The question shown on the form. Defaults to name."},
        "hint": {"type": "string", "description": "How to fill it in. Text after 'e.g.' is shown as the grey example."},
        "type": {"type": "string", "enum": list(NORMS), "default": "text",
                 "description": "What the person may type and how it is normalized and checked."},
        "required": {"type": "boolean", "default": False,
                     "description": "When false, an empty answer means skip (an earlier value is kept)."},
        "section": {"type": "string", "description": "Starts a new group before this field; the value is the group title."},
        "choices": {"type": "array", "items": {"type": "string", "minLength": 1}, "minItems": 1,
                    "description": "The options. Required for type choice, not allowed for other types."},
        "pattern": {"type": "string", "description": "A regular expression the normalized value must fully match."},
        "pattern_hint": {"type": "string", "description": "Shown to the person when pattern does not match."},
    },
}

FORM_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["fields"],
    "properties": {
        "protocol": {"type": "integer", "enum": [PROTOCOL_VERSION], "default": PROTOCOL_VERSION,
                     "description": "Entry protocol version."},
        "title": {"type": "string", "description": "Form title."},
        "note": {"type": "string", "description": "Why the details are needed and where they will be used."},
        "fields": {"type": "array", "minItems": 1, "items": FIELD_SCHEMA},
    },
}

TOOLS = [
    {
        "name": "privfill_keys",
        "title": "List item names",
        "description": "List the names of the stored personal details. Never returns a value. Run it before "
                       "privfill_form to avoid duplicate or near-synonym names. Result: `items`, an array of names.",
        "inputSchema": {"type": "object", "additionalProperties": False, "properties": {}},
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "privfill_form",
        "title": "Show an entry form to the person",
        "description": "Show the person a local form that asks for the described details. The answers are typed "
                       "into the form and go straight into the encrypted vault; they are never returned, and this "
                       "tool takes no answers. Tell the person a form is about to appear. Existing items are "
                       "pre-filled on their screen only. Result: `items`, one entry per field with `name`, "
                       "`status` (created, updated, unchanged or skipped), `type`, `placeholder` and `stored`. "
                       + EXIT_CODES,
        "inputSchema": FORM_SCHEMA,
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
    },
    {
        "name": "privfill_render",
        "title": "Fill a master into an output",
        "description": "Copy a master (a document with {{placeholders}}) to the output path and fill it with the "
                       "stored values, locally. The master stays untouched. Do not read the output; judge it by the "
                       "result. Result: `used`, a map of item name to count. " + EXIT_CODES,
        "inputSchema": {
            "type": "object", "additionalProperties": False, "required": ["master", "output"],
            "properties": {
                "master": {"type": "string", "minLength": 1, "description": "Path of the master file."},
                "output": {"type": "string", "minLength": 1, "description": "Path of the output file. " + PATH_NOTE},
            },
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": True, "openWorldHint": False},
    },
    {
        "name": "privfill_pack",
        "title": "Bundle files into a zip archive",
        "description": "Bundle files into a zip archive, each under its base name. Every path may contain "
                       "placeholders, so outputs can be named without knowing a value. The files are only read; "
                       "an archive already at that path is replaced. Result: `packed`, the number of files, and "
                       "`used`, a map of item name to count. " + EXIT_CODES,
        "inputSchema": {
            "type": "object", "additionalProperties": False, "required": ["archive", "files"],
            "properties": {
                "archive": {"type": "string", "minLength": 1, "description": "Path of the archive. " + PATH_NOTE},
                "files": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1},
                          "description": "Paths of the files to bundle. " + PATH_NOTE},
            },
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": True, "openWorldHint": False},
    },
]


def command_args(name, args):
    """The command line a tool call stands for, or an error text if the arguments do not fit the tool."""
    def strings(*keys):
        return all(isinstance(args.get(k), str) and args[k] for k in keys)

    if name == "privfill_keys" and not args:
        return ["keys"], None
    if name == "privfill_form" and isinstance(args, dict) and args:
        return ["form", json.dumps(args, ensure_ascii=False)], None
    if name == "privfill_render" and set(args) == {"master", "output"} and strings("master", "output"):
        return ["render", args["master"], args["output"]], None
    if (name == "privfill_pack" and set(args) == {"archive", "files"} and strings("archive")
            and isinstance(args["files"], list) and args["files"]
            and all(isinstance(f, str) and f for f in args["files"])):
        return ["pack", args["archive"], *args["files"]], None
    return None, "the arguments do not match the tool's input schema"


def call_tool(name, args, store):
    """Run one tool as its command would run. Returns an MCP tool result."""
    if name not in {t["name"] for t in TOOLS}:
        return None
    cmd, err = command_args(name, args)
    if err:
        result = {"ok": False, "code": 1, "message": f"Failed: {err}."}
    else:
        import contextlib
        import io

        out = io.StringIO()
        with contextlib.redirect_stdout(out):  # stdout is the protocol channel; nothing else may write there
            main(["privfill.py", "--json", *cmd], store=store)
        try:
            result = json.loads(out.getvalue())
        except ValueError:
            result = {"ok": False, "code": 1, "message": "Failed: the command gave no result."}
    return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
            "structuredContent": result, "isError": not result["ok"]}


def reply(msg, store):
    """The JSON-RPC response to one request or notification (None when no response is due)."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
        if isinstance(msg, dict) and "method" not in msg:
            return None  # a response to a request we never sent
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid request"}}
    method, params, rid = msg["method"], msg.get("params") or {}, msg.get("id")
    if "id" not in msg:
        return None  # notification

    def ok(result):
        return {"jsonrpc": "2.0", "id": rid, "result": result}

    def fail(code, text):
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": text}}

    if method == "initialize":
        asked = params.get("protocolVersion")
        return ok({"protocolVersion": asked if asked in SUPPORTED_VERSIONS else SUPPORTED_VERSIONS[0],
                   "capabilities": {"tools": {"listChanged": False}},
                   "serverInfo": {"name": "privfill", "version": __version__},
                   "instructions": "Personal details stay on the person's machine. No tool returns or accepts a "
                                   "value; documents use {{item name}} placeholders that are filled locally."})
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": TOOLS})
    if method == "tools/call":
        args = params.get("arguments")
        if not isinstance(params.get("name"), str) or not (args is None or isinstance(args, dict)):
            return fail(-32602, "Invalid params")
        result = call_tool(params["name"], args or {}, store)
        return ok(result) if result else fail(-32602, f"Unknown tool: {params['name']}")
    return fail(-32601, f"Method not found: {method}")


def serve(infile, outfile, store=None):
    """Answer requests read from infile until it ends. The vault is opened on first use, not at start."""
    lazy = store is None
    for line in infile:
        if not line.strip():
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        else:
            if lazy and store is None and isinstance(msg, dict) and msg.get("method") == "tools/call":
                from .store import default_store
                store = default_store()
            resp = reply(msg, store)
        if resp is not None:
            outfile.write(json.dumps(resp, ensure_ascii=False) + "\n")
            outfile.flush()
    return 0


def run(argv, store=None):
    if len(argv) != 2:
        print("Usage: mcp (serves the Model Context Protocol on standard input and output)", file=sys.stderr)
        return 1
    try:
        return serve(sys.stdin, sys.stdout, store)
    except KeyboardInterrupt:
        return 0
    except Exception as e:  # a message or traceback could quote what it was handling
        print(f"Failed: unexpected error ({type(e).__name__}).", file=sys.stderr)
        return 1
