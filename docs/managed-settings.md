# Managed settings: rules the assistant cannot change

`./install.sh --claude` writes the guard rules into `~/.claude/settings.json`. That file belongs to you, so anything running as you can change it, including an assistant whose tools are allowed to write there. Claude Code also reads a *managed settings* file, `/etc/claude-code/managed-settings.json` on Linux, which takes precedence over user settings and which only root can change. Putting the rules there means changing them needs your sudo password.

## Install

Run this yourself, in a terminal. Do not let the assistant run it.

```
tools/managed-settings-wizard.sh
```

1. It works out the merged file in a temporary directory, using the same merge as `install.sh --claude`: everything already in the managed settings is kept.
2. It prints the exact change as a diff and asks before it does anything with sudo.
3. On yes, it keeps a root-owned copy of the previous file (or a marker that there was none), writes the new file owned by root with mode 644, and runs the check below.

Running it again shows the change needed to bring the rules up to date, or says there is nothing to do.

## Check

```
tools/managed-settings-wizard.sh --verify
```

It confirms that the file is owned by root, that neither it nor its directory is writable by you, that appending to it as you fails, and that the guard hook is in it. The last check is yours: restart Claude Code, ask Claude to add a line to `/etc/claude-code/managed-settings.json`, and see it fail with permission denied. `/status` in Claude Code lists the managed settings among the sources.

## Remove

```
tools/managed-settings-wizard.sh --remove
```

It shows what will be put back and asks first. The previous managed settings file is restored exactly, or deleted if there was none before.

## What this protects, and what it does not

- **Protected:** the guard hook, the deny rules on the data directory and the allow rules for the program's commands cannot be removed or weakened from your account without sudo. Settings from user and project files cannot override a managed deny rule.
- **Not protected:** the program the hook runs, `~/.privfill/privfill.py`, is still yours and could be replaced by something running as you; the managed file names it, it does not seal it. Copying the program to a root-owned location closes that, at the cost of updating it with sudo.
- **Not protected:** anything running as you that does not go through Claude Code (another program, a shell you opened) is not bound by Claude Code's settings at all.
- **Not protected:** malware running as root, or with your sudo password.
- Claude Code's own option `allowManagedHooksOnly` would also ignore every hook from user and project settings. The wizard does not set it, because it would switch off any hooks of your own; add it by hand if you want that.
