# Using privfill with Claude Code

Two steps: give Claude the rules, and make Claude Code ask you before every access to your details.

## 1. Rules

Install the skill:

```
./install.sh --skill
```

It copies `skills/privfill/SKILL.md` and the program (`privfill.py` and the `privfill/` package) to `~/.claude/skills/privfill/`. Claude loads the skill when a task involves personal details. Start a new session after installing.

## 2. Permissions

Add `ask` rules to `permissions` in `~/.claude/settings.json`:

```json
{
  "permissions": {
    "ask": [
      "Read(~/.privfill/**)",
      "Edit(~/.privfill/**)",
      "Bash(*privfill*)"
    ]
  }
}
```

| Rule | Effect |
|---|---|
| `Read(~/.privfill/**)` | Claude must ask before reading anything in the data directory with its file tools. |
| `Edit(~/.privfill/**)` | Claude must ask before changing anything there. |
| `Bash(*privfill*)` | Any command that mentions privfill, including each call to the program, needs your approval. |

If you already have a `permissions` block, merge the `ask` array into it; do not replace the block. Restart Claude Code, then ask Claude to run `privfill keys` and confirm that you are prompted.

When you approve, glance at the command. It should be `privfill.py` followed by a subcommand. If it is `cat`, `grep` or a file read aimed at the data directory, deny it.

## 3. MCP server (optional)

Instead of running the program through the shell, Claude can call it as tools. Register the server once:

```
claude mcp add --scope user privfill -- python3 ~/.privfill/privfill.py mcp
```

Claude then sees four tools: `privfill_keys`, `privfill_form`, `privfill_render` and `privfill_pack`. Their input schemas carry the entry protocol, so Claude needs no side documentation to call them. Each returns the same JSON object as the command with `--json`: item names, counts, statuses and the exit code (`isError` is true whenever the code is not 0). No tool takes a value, returns one or runs a command. The vault is opened on the first tool call, and the passphrase dialog appears then.

To be asked before each call, add the tools to the `ask` rules:

```json
"ask": ["mcp__privfill__privfill_form", "mcp__privfill__privfill_render", "mcp__privfill__privfill_pack"]
```

**With the guard.** The guard allows the server's own tools, whatever name you registered the server under, just as it allows `privfill.py` on the command line; so `privfill_pack` may bundle a filled output. Every other tool, including tools from other MCP servers, is judged by its inputs, and is denied if one of them names the data directory or a filled output. The server and the guard share one data directory and one output registry, so outputs written through either path are protected alike.

## Limits of this layer

Claude Code enforces `ask` rules, and it matches shell commands by text, so this is not a sandbox. Its value is that every access passes in front of you. For a harder line, also deny direct reads of the vault, which holds item names in the clear and values encrypted:

```json
"deny": ["Read(~/.privfill/vault.json)"]
```

privfill itself is not affected: the program reads the file, not Claude's file tool.
