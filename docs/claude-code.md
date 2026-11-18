# Using privfill with Claude Code

Two steps: give Claude the rules, and make Claude Code ask you before every access to your details.

## 1. Rules

Install the skill:

```
./install.sh --skill
```

It copies `skills/privfill/SKILL.md` and the program (`privfill.py` and the `privfill/` package) to `~/.claude/skills/privfill/`. Claude loads the skill when a task involves personal details. Start a new session after installing.

## 2. Guard configuration

```
./install.sh --claude            # or --claude-sandbox, see below
./install.sh --claude-remove     # takes it out again
```

This merges the following into `~/.claude/settings.json`, keeping everything else in the file (a copy of the previous version is kept as `settings.json.privfill-backup`, and what was added is recorded in `settings.json.privfill.json` so that removal takes out exactly that):

| Setting | Effect |
|---|---|
| `permissions.deny`: `Read(~/.privfill/**)`, `Edit(~/.privfill/**)` | Claude's file tools never read or change the data directory. |
| `hooks.PreToolUse`, matcher `*`: `privfill.py guard` | Every tool call is judged by the guard first (`docs/guard.md`): reads, searches, listings and shell commands that reach the data directory or a filled output are denied, with a reason that names no path. |
| `permissions.allow`: `Bash(python3 ~/.privfill/privfill.py <command> *)` and the same for the skill's copy | The program's own commands run without a prompt. The guard still checks each one, for example for a command substitution hidden in its arguments. |
| `sandbox` (only with `--claude-sandbox`) | Turns on Claude Code's Bash sandbox, where the deny rules also stop shell reads at the operating-system level, and runs the program's own commands outside it so they can open the vault. The sandbox applies to every shell command, network access included, so it is a choice of its own. |

The `ask` rules of earlier versions (`Read(~/.privfill/**)`, `Edit(~/.privfill/**)`, `Bash(*privfill*)`) are removed: the guard decides deterministically instead of asking on every call.

Restart Claude Code, then follow [verify-guard.md](verify-guard.md) once to see it work in a real session.

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

- Claude Code matches shell commands by text, and the guard judges what a call says, so neither is a sandbox on its own. `--claude-sandbox` adds the operating-system layer for shell commands.
- Anything running as you can edit `~/.claude/settings.json`, so these rules hold against an assistant only while it does not rewrite them. [managed-settings.md](managed-settings.md) moves them into the root-owned managed settings file.
- None of this defends against malware running as your user.
