# privfill protocol, version 1

This is the contract between privfill and the AI tool that calls it (the "caller"). It defines how personal details are entered, how they are used, and what the program reports back.

One principle runs through all of it: **values stay on the machine**. No command the caller sends and no output it receives contains the value of a personal detail, only item names.

## 1. Terms

| Term | Meaning |
|---|---|
| Item | One personal detail: a name and a value, for example the item named `date of birth`. |
| Data file | The local file holding all items. The caller must not read, edit or list it. |
| Entry protocol | The JSON the caller passes to `form`, describing the questions to ask the person. |
| Placeholder | A marker in a document that stands for an item, written `{{item name}}`. |
| Master | A document with placeholders and no real values. The caller works only on masters. |
| Output | A copy of a master with the real values filled in. The caller must not read it. |

## 2. Entry protocol

### 2.1 Structure

```json
{
  "protocol": 1,
  "title": "Form title",
  "note": "One or two sentences for the person",
  "fields": [
    { "name": "date of birth", "type": "date", "hint": "e.g. 2005-03-04" }
  ]
}
```

Top-level keys:

| Key | Required | Type | Meaning |
|---|---|---|---|
| `protocol` | no | integer | Protocol version. Treated as 1 when absent; anything else is rejected. |
| `title` | no | string | Form title. |
| `note` | no | string | Why the details are needed and where they will be used. |
| `fields` | yes | array | At least one field. |

Field keys:

| Key | Required | Type | Meaning |
|---|---|---|---|
| `name` | yes | string | Item name, and the name used in placeholders. Not empty; no `\|`, `{`, `}` or newline; not `Item` or `项目`. Unique within the form. |
| `label` | no | string | The question shown on the form. Defaults to `name`. |
| `hint` | no | string | How to fill it in. Text after "e.g." (or "例如") is shown as the grey example inside the input box. |
| `type` | no | string | See 2.2. Defaults to `text`. |
| `required` | no | boolean | Defaults to `false`. When false, an empty answer means skip. |
| `section` | no | string | Starts a new group before this field; the value is the group title. |
| `choices` | for `choice` | array of strings | The options. Not allowed on other types. |
| `pattern` | no | string | A regular expression the normalized value must fully match. |
| `pattern_hint` | no | string | Shown to the person when `pattern` does not match. |

**The protocol holds questions, never answers.** Any key not listed above, including `value`, `default` and `answer`, makes the program reject the whole protocol with exit code 4.

### 2.2 Types and normalization

The person may type in whatever way is natural; the program normalizes locally by fixed rules. The rules are pattern-based, not language understanding, so an answer such as "last spring" is not accepted for a date and the form asks for a rewrite.

| `type` | Accepted | Stored as | Check |
|---|---|---|---|
| `text` | any single line | runs of whitespace collapsed to one space | none |
| `date` | `2005-3-4`, `2005/03/04`, `2005.3.4`, `2005年3月4日`, `20050304` | `2005-03-04` | must be a real date |
| `phone` | `138 0013 8000`, `+86 138-0013-8000` | `13800138000` | 7 to 15 digits after removing spaces, dashes, brackets and a leading +86 |
| `id_card` | 18-character PRC resident ID, spaces and lower-case x allowed | no spaces, upper-case X | 18 characters and a correct check digit |
| `email` | an email address | trimmed | shaped like `a@b.c` |
| `number` | digits, with commas or full-width digits | plain number | integer or decimal |
| `choice` | picked from a list | the chosen option | must be one of `choices` |

Date parsing reads year, month, day in that order. All types except `text` first convert full-width characters to half-width. A `|` in a value is stored as the full-width `｜`.

### 2.3 Form behaviour

- Items already in the data file are pre-filled. They appear only on the person's screen.
- An item the person changes counts as updated; one left alone counts as unchanged; an empty optional field counts as skipped.
- If a check fails, the form stays open and states the reason under the field.
- If the person cancels or closes the window, nothing is saved and the exit code is 3.
- The form's own text is in English or Chinese, following the system locale or `PRIVFILL_LANG`. `title`, `note`, `label`, `hint` and `section` are shown as the caller wrote them.

## 3. Placeholders

### 3.1 Syntax

```
{{item name}}
{{item name|format}}
```

Whitespace just inside the braces is ignored. The name must match an item exactly. If it does not exist the command fails with exit code 2 and lists the available names; nothing is left blank or guessed.

### 3.2 Formats

| Format | Applies to | Example for `2005-03-04` or `13800138000` |
|---|---|---|
| a template using `YYYY`, `MM`, `DD`, `M`, `D` | `date` | `YYYY/MM/DD` gives `2005/03/04`; `YYYYMMDD` gives `20050304`; `D.M.YYYY` gives `4.3.2005` |
| `年月日` | `date` | `2005年3月4日` |
| `firstN` (or `前N位`) | any | `first3` gives `138` |
| `lastN` (or `后N位`) | any | `last4` gives `8000` |

A format that does not apply to the item fails with exit code 2.

### 3.3 Where substitution happens

| File | Handling |
|---|---|
| `.docx`, `.xlsx`, `.pptx` | Text of the `.xml` and `.rels` parts is substituted with XML escaping; images and other binary parts are untouched. A placeholder must sit inside a single text run. |
| `.ipynb`, `.json` | Parsed, every string substituted, written back. |
| anything else | Substituted as UTF-8 text. |
| file names | The output path of `render` and every path given to `pack` may contain placeholders. |

## 4. Commands

No command prints a value. `privfill` stands for however the program is invoked, for example `python3 ~/.privfill/privfill.py`.

| Command | Effect |
|---|---|
| `privfill init` | Create an empty data file (mode 600). No-op if it exists. |
| `privfill keys` | List the stored item names. |
| `privfill protocol` | Print a summary of the entry protocol. |
| `privfill form '<JSON>'` or `form <file>` | Show a form described by an entry protocol. |
| `privfill add <item>` | Enter one required `text` item. |
| `privfill render <master> <output>` | Copy the master to the output path and fill it; the master stays untouched. |
| `privfill fill <file>...` | Fill in place. This destroys the master, so use it only on throwaway copies. |
| `privfill pack <archive> <file>...` | Bundle the files into a zip archive at the archive path, each under its base name. Every path may contain placeholders, so outputs can be named without knowing a value. The files are only read. The archive appears complete or not at all; an archive already at that path is replaced, and a new one is created with mode 600. Directories, and two files with the same base name, are refused with exit code 1. |

### 4.1 Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | Usage or runtime error |
| 2 | A placeholder names an item that does not exist, or a format does not apply |
| 3 | The person cancelled the form |
| 4 | The entry protocol is invalid |

The program never starts another program, so no value is ever placed on a command line. There is no command that runs one with placeholders in its arguments; use `render` for naming and `pack` for bundling.

### 4.2 JSON output

With `--json` before the subcommand, the output is one JSON object:

```json
{ "ok": true, "code": 0, "message": "human-readable text", "...": "fields per command" }
```

| Command | Extra fields |
|---|---|
| `keys` | `items`: array of item names |
| `form`, `add` | `items`: one entry per field with `name`, `status`, `type`, `placeholder`, `stored` |
| `fill` | `files`: one entry per file with `index`, `ok`, `used` |
| `render` | `used`: `{item name: count}` |
| `pack` | `packed`: the number of files in the archive; `used`: `{item name: count}` |
| `init` | `created` |

`status` is one of `created`, `updated`, `unchanged`, `skipped`. `stored` says whether the item now exists in the data file.

`fill` and `pack` refer to files by index, not by name, because a file name may itself contain personal details.

### 4.3 Environment

| Variable | Meaning |
|---|---|
| `PRIVFILL_DATA` | Path of the data file. Default: `~/.privfill/profile.md`. |
| `PRIVFILL_LANG` | `en` or `zh` for the form text. Default: from the system locale. |

Command output is always English, since it is read by the caller.

## 5. Rules for the caller

1. Do not read, edit or list the data file or its directory.
2. Do not ask for values in conversation, and do not put values in a protocol or on a command line.
3. Write, review and revise only masters. To change an output, change the master and render again.
4. After rendering, do not read, compare or list the output or its directory. Judge the result by the reported counts and exit code, and leave the final check to the person.
5. Keep masters and outputs in different directories.
6. When an item is missing, ask the person with `form`. Do not invent a value or leave a blank.
7. Do not write values to memory or notes, and do not send them to any external service.

## 6. What this does and does not guarantee

It guarantees that, as long as the caller follows section 5, values do not appear in anything the caller sends or receives.

It does not guarantee:

- That the caller follows the rules. The program is not a sandbox: a caller that can read files can read the data file or an output. Combine it with the caller's own permission system (see `docs/claude-code.md`) so that every access needs the person's approval.
- Anything about details the person types into the conversation. Those have already been sent.
- Where outputs go. Outputs are plain text.
- Encryption at rest. The data file is plain text with mode 600, which keeps out other system users, not other programs running as the same user.

## 7. Versioning

The protocol version is an integer. Adding optional keys or new types does not change it; changing the meaning of an existing key or removing one does. The program rejects versions it does not support.
