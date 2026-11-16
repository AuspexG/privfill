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

## Limits of this layer

Claude Code enforces `ask` rules, and it matches shell commands by text, so this is not a sandbox. Its value is that every access passes in front of you. For a harder line, also deny direct reads of the vault, which holds item names in the clear and values encrypted:

```json
"deny": ["Read(~/.privfill/vault.json)"]
```

privfill itself is not affected: the program reads the file, not Claude's file tool.
