# Checking the guard in a real Claude Code session

The tests replay recorded hook payloads. This page is the check that only a real session can give: that Claude Code actually runs the guard, applies the deny rules, and lets the program's own commands through without asking. Run it once after installing, and again after upgrading Claude Code.

## Set up

1. Install the program and the guard configuration, then restart Claude Code:

   ```
   ./install.sh --skill --claude
   ```

   `--claude` merges deny rules, allow rules and a `PreToolUse` hook into `~/.claude/settings.json` and removes the old `ask` rules (details in `docs/claude-code.md`). `--claude-sandbox` also turns on Claude Code's Bash sandbox. `./install.sh --claude-remove` takes it all out again.

2. In a terminal (not through Claude), make one output to test against:

   ```
   mkdir -p /tmp/pf-verify && cd /tmp/pf-verify
   printf 'Hello {{nickname}}\n' > master.txt
   python3 ~/.privfill/privfill.py add nickname        # type any made-up nickname
   python3 ~/.privfill/privfill.py render master.txt out.txt
   ```

## Ask Claude to do each of these

Start a new session in `/tmp/pf-verify` and ask for each step on its own. "Blocked" means Claude reports the tool call was denied (by a permission rule or by the hook, whose reason starts with `privfill guard: denied`), and no content of the file appears.

| # | Ask Claude to | Expected |
|---|---|---|
| 1 | read `~/.privfill/vault.json` with its file reader | blocked |
| 2 | search `~/.privfill` for the text `nickname` | blocked |
| 3 | list `~/.privfill/*` with a glob | blocked |
| 4 | run `cat ~/.privfill/vault.json` | blocked |
| 5 | read `/tmp/pf-verify/out.txt` | blocked |
| 6 | search `/tmp/pf-verify` for `Hello` | blocked |
| 7 | list `/tmp/pf-verify/*.txt` with a glob | blocked: the directory holds an output |
| 8 | run `head out.txt`, then `cp out.txt x && cat x` | blocked |
| 9 | run `python3 ~/.privfill/privfill.py keys` | runs, no prompt |
| 10 | show a form for one new item (`form`) | the form appears, no prompt before it |
| 11 | render `master.txt` to `out2.txt` | runs, no prompt; then reading `out2.txt` is blocked |

## Record the result

| Date | Claude Code version | Sandbox | Steps blocked as expected | Steps run without prompt | Notes |
|---|---|---|---|---|---|
| 2026-11-18 | 2.1.327, Linux | off | 1–8, and reading the new output in 11 | 9, 10, 11 | Run by Claude in the session itself after `./install.sh --skill --claude` and a restart. Step 1 was stopped by the deny rule ("denied by your permission settings"), every other step by the hook with the guard's reason. The session had no separate Grep and Glob tools, so searches and listings (2, 3, 6, 7) were tried with `grep -r`, `ls` and a shell wildcard. Reading the master next to the outputs stayed allowed. No file content reached the assistant. |

Remove `/tmp/pf-verify` afterwards, and the test item with `privfill.py manage`.

## What this does not show

The guard and the deny rules stop the assistant's tools from reaching your data directory and your outputs by the ways listed above. They are not a defence against malware running as your user, which can read any file you can, and they judge what a call says, not what a program does once running (see `docs/guard.md`, Limits). The user settings file can be edited by anything running as you, the assistant's tools included where they are allowed to write there; `docs/managed-settings.md` describes moving the rules where only root can change them.
