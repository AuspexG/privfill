---
name: privfill
description: Use the user's personal details (name, ID numbers, phone, address, date of birth and similar) in documents, forms and file names without ever reading them. Use this whenever a task needs such details, when the user wants to store or update personal information, or when a file that contains their personal details has to be created, reviewed or changed.
---

# privfill

The user's personal details are stored on this machine and managed by a local program. You can use them, but you must not see them: anything you read enters the conversation and leaves the machine. You write placeholders; the program fills in the real values locally and reports only counts and item names.

The program:

```
python3 ~/.claude/skills/privfill/privfill.py <subcommand>
```

Below it is written as `privfill`. If that path does not exist, ask the user where privfill is installed. Every call may prompt the user for approval; that is intended.

## Never

- Read, edit or list the privfill vault (`~/.privfill/` by default) or its directory.
- Ask the user for a personal detail in chat, or put one on a command line or in a protocol.
- Read, diff, grep or list a filled output file or its directory, not even to check the result.
- Store personal details in memory or notes, or send them to any external service.

## When a task needs personal details

1. Run `privfill keys`. It returns item names only.
2. Write the document as a **master**: put `{{item name}}` wherever a detail belongs, including in file names. Names must match `keys` exactly.
3. Do all writing, checking and revising on the master.
4. As the last step, generate the output into a different directory from the master:

   ```
   privfill render 'report/{{student id}}_report.docx' 'out/{{student id}}_report.docx'
   ```

5. Judge the result only by the counts and exit code the program reports. Ask the user to open the output for the final check.

To change a delivered file later, edit the master and render again. Keep the master.

Placeholders take an optional format, so the user never has to enter the same detail twice in different shapes:

| Placeholder | Result |
|---|---|
| `{{date of birth}}` | 2005-03-04 |
| `{{date of birth\|YYYY/MM/DD}}` | 2005/03/04 |
| `{{date of birth\|D.M.YYYY}}` | 4.3.2005 |
| `{{phone\|last4}}`, `{{phone\|first3}}` | last four / first three characters |

To bundle outputs into a zip archive whose name contains a detail:

```
privfill pack 'out/{{student id}}_homework.zip' 'out/{{student id}}_report.docx' homework.ipynb
```

Every path may contain placeholders; that is how you name an output you rendered without knowing its real name. Each file goes into the archive under its base name, so directories and two files with the same name are refused. The files are left untouched, and a failure writes nothing. Never put a placeholder on any other command line: privfill does not run commands, and nothing else can fill one in.

## When an item is missing, or the user wants to record something

1. Run `privfill keys` so you do not create a duplicate or a near-synonym.
2. Tell the user a form is about to appear on their screen.
3. Run `privfill form '<protocol>'`. The user fills in the form themself. Put related items in one form.

The protocol is JSON that describes questions only:

```json
{
  "protocol": 1,
  "title": "Contact details",
  "note": "Needed for the registration form.",
  "fields": [
    {"name": "phone", "label": "Mobile phone number", "hint": "e.g. 138 0013 8000", "type": "phone", "required": true},
    {"name": "date of birth", "hint": "e.g. 2005-03-04", "type": "date"}
  ]
}
```

- Write `label` and `hint` in plain words, in the user's language, with an example after "e.g.". The user does not need to know any format; the program normalizes locally.
- Choose the right `type`: `text`, `date`, `phone`, `id_card` (PRC resident ID), `email`, `number`, `choice` (needs `choices`).
- Group more than five fields with `section`.
- Optional keys: `required`, `pattern`, `pattern_hint`. Any other key, such as `value` or `default`, makes the program reject the whole protocol.

Run `privfill protocol` for the summary of the format.

The program reports each item's status (stored, updated, unchanged, left empty) and its placeholder. Exit code 3 means the user cancelled: do not show the form again; ask them first.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | Usage or runtime error |
| 2 | A placeholder names an item that does not exist, or a format does not apply. The message lists the available items. |
| 3 | The user cancelled the form |
| 4 | The protocol is invalid |
| 5 | The user entered a wrong vault passphrase. Ask them before trying again. |
| 6 | `audit` found a stored value (names and counts are in the output; never try to find out the value or path yourself). |
| 7 | The user declined, in a local window, the use of a high-sensitivity item. Nothing was written. Do not retry unless they ask. |

Commands that use a value unlock the vault through the desktop keyring when it has the key, and otherwise make the program ask the user for their vault passphrase in a local window. You never see either, never ask for them and never supply them; never query the keyring yourself (`secret-tool`, `secretstorage` and the like). If the user closes the passphrase window the exit code is 3. Pass on any `Note:` line to the user. To change how the vault is unlocked, the user runs `privfill.py unlock` (see `docs/vault.md`); suggest it, do not choose for them.

Put `--json` before the subcommand for machine-readable output.

## Other situations

- The user states a personal detail in chat: say once that it has already been sent, and suggest the form next time. Then carry on with the task.
- The user hands you an existing file that already contains their details: tell them before opening it and suggest turning it into a master first.
- Testing privfill itself: point it at a fake vault with `PRIVFILL_VAULT=<path>`; never test on the real vault.
