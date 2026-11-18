"""The MCP server driven by a scripted client. Fake data only.

The store is an in-memory double, so the tools are shown to reach items only through the Store seam.
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from privfill.mcp_server import TOOLS, serve  # noqa: E402
from test_store_seam import MemoryStore  # noqa: E402

TOOL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "privfill.py")
VALUES = {"name": "Jane Roe", "phone": "13800138000", "birthday": "2005-03-04"}
NEW_VALUE = "Quokka Street 42"


class Client:
    """Speaks to serve() the way an MCP client does: one JSON message per line."""

    def __init__(self, store, vault):
        self.store, self.seen, self.n = store, [], 0
        self.vault = vault  # a fake vault path: the form's test hook runs only against one

    def request(self, method, params=None, **env):
        self.n += 1
        msg = {"jsonrpc": "2.0", "id": self.n, "method": method}
        if params is not None:
            msg["params"] = params
        out = io.StringIO()
        with mock.patch.dict(os.environ, dict({"PRIVFILL_LANG": "en", "PRIVFILL_VAULT": self.vault}, **env)):
            serve(io.StringIO(json.dumps(msg) + "\n"), out, self.store)
        self.seen.append(out.getvalue())
        resp = json.loads(out.getvalue())
        self.assertEqual(resp["id"], self.n)
        return resp

    def assertEqual(self, a, b):
        assert a == b, (a, b)

    def call(self, name, args=None, answers=None):
        env = {"PRIVFILL_FORM_TEST": json.dumps(answers)} if answers is not None else {}
        resp = self.request("tools/call", {"name": name, "arguments": args or {}}, **env)
        return resp["result"]


class McpCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.store = MemoryStore(VALUES)
        self.client = Client(self.store, os.path.join(self.dir, "store", "vault.json"))

    def tearDown(self):
        """The no-value invariant: nothing the server said holds a stored or typed value."""
        for out in self.client.seen:
            for secret in list(VALUES.values()) + [NEW_VALUE]:
                self.assertNotIn(secret, out)
        self.tmp.cleanup()

    def path(self, name):
        return os.path.join(self.dir, name)

    def write(self, name, text):
        with open(self.path(name), "w", encoding="utf8") as f:
            f.write(text)


class TestHandshakeAndSchemas(McpCase):
    def test_initialize_announces_tools(self):
        r = self.client.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                               "clientInfo": {"name": "t", "version": "0"}})["result"]
        self.assertEqual(r["protocolVersion"], "2025-06-18")
        self.assertIn("tools", r["capabilities"])
        self.assertEqual(r["serverInfo"]["name"], "privfill")

    def test_notifications_get_no_response_and_unknown_methods_an_error(self):
        out = io.StringIO()
        serve(io.StringIO('{"jsonrpc":"2.0","method":"notifications/initialized"}\n'), out, self.store)
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(self.client.request("nope")["error"]["code"], -32601)
        out = io.StringIO()
        serve(io.StringIO("not json\n"), out, self.store)
        self.assertEqual(json.loads(out.getvalue())["error"]["code"], -32700)

    def test_lists_the_four_tools(self):
        tools = self.client.request("tools/list")["result"]["tools"]
        self.assertEqual([t["name"] for t in tools], ["privfill_keys", "privfill_form", "privfill_render", "privfill_pack"])

    def test_every_tool_has_a_complete_closed_input_schema(self):
        for t in self.client.request("tools/list")["result"]["tools"]:
            s = t["inputSchema"]
            self.assertEqual(s["type"], "object", t["name"])
            self.assertIs(s["additionalProperties"], False, t["name"])
            self.assertTrue(t["description"])
            for k, p in s["properties"].items():
                self.assertTrue(p.get("description") or k in ("fields", "protocol", "title", "note"), (t["name"], k))
            self.assertTrue(set(s.get("required", [])) <= set(s["properties"]))

    def test_form_schema_carries_the_entry_protocol(self):
        form = next(t for t in TOOLS if t["name"] == "privfill_form")["inputSchema"]
        self.assertEqual(set(form["properties"]), {"protocol", "title", "note", "fields"})
        self.assertEqual(form["required"], ["fields"])
        field = form["properties"]["fields"]["items"]
        self.assertEqual(set(field["properties"]),
                         {"name", "label", "hint", "type", "required", "section", "choices", "pattern", "pattern_hint"})
        self.assertEqual(field["required"], ["name"])
        self.assertIs(field["additionalProperties"], False)
        self.assertEqual(field["properties"]["type"]["enum"],
                         ["text", "date", "phone", "id_card", "email", "number", "choice"])

    def test_form_schema_matches_the_validator(self):
        from privfill.protocol import FIELD_KEYS, SPEC_KEYS
        form = next(t for t in TOOLS if t["name"] == "privfill_form")["inputSchema"]
        self.assertEqual(set(form["properties"]), SPEC_KEYS)
        self.assertEqual(set(form["properties"]["fields"]["items"]["properties"]), FIELD_KEYS)

    def test_exit_codes_are_described(self):
        for t in TOOLS[1:]:
            self.assertIn("cancelled the form", t["description"])


class TestTools(McpCase):
    def test_keys(self):
        r = self.client.call("privfill_keys")
        self.assertEqual((r["isError"], r["structuredContent"]["items"]), (False, ["name", "phone", "birthday"]))
        self.assertEqual(json.loads(r["content"][0]["text"]), r["structuredContent"])

    def test_form_stores_and_reports_statuses_only(self):
        spec = {"title": "t", "fields": [{"name": "name"}, {"name": "address", "required": True},
                                         {"name": "email", "type": "email"}]}
        r = self.client.call("privfill_form", spec, answers={"name": VALUES["name"], "address": NEW_VALUE})
        self.assertFalse(r["isError"], r)
        self.assertEqual(r["structuredContent"]["code"], 0)
        self.assertEqual({i["name"]: i["status"] for i in r["structuredContent"]["items"]},
                         {"name": "unchanged", "address": "created", "email": "skipped"})
        self.assertEqual(self.store.items["address"], NEW_VALUE)

    def test_form_cancel_through_the_hook(self):
        with mock.patch.dict(os.environ, {"PRIVFILL_FORM_TEST": "null"}):
            r = self.client.call("privfill_form", {"fields": [{"name": "address"}]})
        self.assertEqual((r["isError"], r["structuredContent"]["code"]), (True, 3))
        self.assertNotIn("address", self.store.items)

    def test_form_with_an_answer_in_the_protocol_is_code_4(self):
        for extra in ({"value": NEW_VALUE}, {"default": NEW_VALUE}):
            r = self.client.call("privfill_form", {"fields": [dict({"name": "address"}, **extra)]})
            self.assertEqual((r["isError"], r["structuredContent"]["code"]), (True, 4))
        self.assertNotIn("address", self.store.items)

    def test_render(self):
        self.write("m.md", "{{name}} {{phone|last4}}")
        r = self.client.call("privfill_render", {"master": self.path("m.md"), "output": self.path("o_{{name}}.md")})
        self.assertEqual((r["isError"], r["structuredContent"]["used"]), (False, r["structuredContent"]["used"]))
        self.assertEqual(r["structuredContent"]["used"], {"name": 1, "phone": 1})  # as the command: uses in the content
        with open(self.path("o_Jane Roe.md"), encoding="utf8") as f:
            self.assertEqual(f.read(), "Jane Roe 8000")

    def test_render_missing_item_is_code_2(self):
        self.write("m.md", "{{passport}}")
        r = self.client.call("privfill_render", {"master": self.path("m.md"), "output": self.path("o.md")})
        self.assertEqual((r["isError"], r["structuredContent"]["code"]), (True, 2))
        self.assertIn("name", r["structuredContent"]["message"])
        self.assertFalse(os.path.exists(self.path("o.md")))

    def test_pack(self):
        self.write("a.md", "x")
        r = self.client.call("privfill_pack", {"archive": self.path("{{name}}.zip"), "files": [self.path("a.md")]})
        self.assertEqual((r["isError"], r["structuredContent"]["packed"], r["structuredContent"]["used"]),
                         (False, 1, {"name": 1}))
        with zipfile.ZipFile(self.path("Jane Roe.zip")) as z:
            self.assertEqual(z.namelist(), ["a.md"])

    def test_pack_refuses_a_directory_with_code_1(self):
        r = self.client.call("privfill_pack", {"archive": self.path("a.zip"), "files": [self.dir]})
        self.assertEqual((r["isError"], r["structuredContent"]["code"]), (True, 1))

    def test_tools_behave_like_the_command_line(self):
        from privfill.cli import main
        self.write("m.md", "{{name}}")
        cli = io.StringIO()
        with mock.patch("sys.stdout", cli):
            rc = main(["privfill.py", "--json", "render", self.path("m.md"), self.path("c.md")], store=self.store)
        r = self.client.call("privfill_render", {"master": self.path("m.md"), "output": self.path("c.md")})
        self.assertEqual(r["structuredContent"], json.loads(cli.getvalue()))
        self.assertEqual(r["structuredContent"]["code"], rc)

    def test_bad_arguments_are_a_failed_result_not_a_crash(self):
        for name, args in (("privfill_render", {"master": "a"}), ("privfill_render", {"master": "a", "output": "b", "x": "1"}),
                           ("privfill_pack", {"archive": "a", "files": []}), ("privfill_pack", {"archive": "a", "files": [3]}),
                           ("privfill_keys", {"value": "x"}), ("privfill_form", {})):
            r = self.client.call(name, args)
            self.assertEqual((r["isError"], r["structuredContent"]["code"]), (True, 1), (name, args))

    def test_unknown_tool_is_a_protocol_error(self):
        resp = self.client.request("tools/call", {"name": "run", "arguments": {"command": "ls"}})
        self.assertEqual(resp["error"]["code"], -32602)


class TestNoValueInvariant(McpCase):
    def test_no_tool_runs_a_command_or_takes_a_value(self):
        def keys(schema):
            if isinstance(schema, dict):
                for k, v in schema.items():
                    if k == "properties":
                        yield from v
                    yield from keys(v)
            elif isinstance(schema, list):
                for v in schema:
                    yield from keys(v)

        for t in TOOLS:
            self.assertFalse({"value", "values", "answer", "answers", "default", "command", "cmd", "args", "argv",
                              "shell", "passphrase"} & set(keys(t["inputSchema"])), t["name"])
            self.assertNotIn("run", t["name"])

    def test_no_stored_value_in_any_answer_even_when_reflected(self):
        self.write("m.md", "{{name}} {{phone}} {{birthday}}")
        self.client.call("privfill_keys")
        self.client.call("privfill_render", {"master": self.path("m.md"), "output": self.path("{{phone}}.md")})
        self.client.call("privfill_pack", {"archive": self.path("{{birthday}}.zip"), "files": [self.path("{{phone}}.md")]})
        self.client.call("privfill_render", {"master": self.path("m.md"), "output": self.path("{{phone}}.md")})
        self.client.call("privfill_form", {"fields": [{"name": "address", "required": True}]}, answers={"address": NEW_VALUE})
        self.assertTrue(len(self.client.seen) >= 5)  # tearDown scans every response

    def test_the_server_never_writes_anything_but_protocol_to_stdout(self):
        p = subprocess.run([sys.executable, TOOL, "mcp"], input=json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) + "\n", capture_output=True, text=True)
        lines = p.stdout.splitlines()
        self.assertEqual((p.returncode, len(lines)), (0, 1), p.stderr)
        self.assertEqual(len(json.loads(lines[0])["result"]["tools"]), 4)


if __name__ == "__main__":
    unittest.main()
