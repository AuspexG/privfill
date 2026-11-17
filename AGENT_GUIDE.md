# Giving the rules to your AI tool

privfill only works if the assistant follows a few rules: write placeholders, never read the vault, never read filled outputs. The rules live in one file, [`skills/privfill/SKILL.md`](skills/privfill/SKILL.md), so there is a single text to maintain. It is written in English because it is read by the assistant, not by you.

## Claude Code

Install the skill:

```
./install.sh --skill
```

This copies the skill to `~/.claude/skills/privfill/` together with the program. Claude loads it when a task involves personal details. Then add the permission rules from [`docs/claude-code.md`](docs/claude-code.md) so every access asks you first.

## Any other tool

Copy the body of `skills/privfill/SKILL.md` (everything below the front matter) into whatever your tool reads as standing instructions:

| Tool | Where |
|---|---|
| Tools that read `AGENTS.md` | `AGENTS.md` in your home or project directory |
| Cursor, Windsurf and similar editors | the rules or instructions file |
| A chat model with tool use | the system prompt |

Then replace the program path in the copied text with the one on your machine. After `./install.sh` it is:

```
python3 ~/.privfill/privfill.py
```

The tool needs to be able to run shell commands on your machine. If it has a way to require approval for specific commands or paths, use it for `~/.privfill` and for commands that mention `privfill`.

## Checking that it works

Ask the assistant to run `privfill keys`. It should get a list of item names and nothing else. Then ask it to record something, for example "record my phone number": a form should appear on your screen, and the assistant should report only that the item was stored.
