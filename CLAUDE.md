# privfill

Lets an AI assistant use a person's personal details without ever seeing them. Read `PROTOCOL.md` for the contract and `README.md` for the overview. The rule that matters most: no command output, test fixture, example, screenshot, issue or commit in this repository may contain a real personal value. Use fake data only, and scan before every push.

`privfill.py` is only a launcher; the code is the `privfill/` package (module table in `README.md`). Commands must reach items only through the `Store` interface in `privfill/store.py`.

## Agent skills

### Issue tracker

Issues live in GitHub Issues for this repo (`gh` CLI). See `docs/agents/issue-tracker.md`.

### Triage labels

Default five-label vocabulary (needs-triage, needs-info, ready-for-agent, ready-for-human, wontfix). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `GLOSSARY.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.
