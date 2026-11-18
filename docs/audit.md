# The leak audit

`privfill audit` answers one question: did any of my stored details end up somewhere they should not be? It reads files locally, compares them with the values in the vault, and reports which item was found how often and where, by number. It never prints a value, and it never prints a path, since a path can itself hold a value.

```
privfill.py audit ~/project               scan a directory
privfill.py audit --window ~/project      also show the person the paths, in a local window
privfill.py audit --min-length 6 notes.txt
```

| Exit code | Meaning |
|---|---|
| 0 | Nothing was found. |
| 6 | At least one value was found. |
| 1 | A usage error, or a path that does not exist (nothing was scanned). |

With `--json`: `found` (`{item: count}`), `locations` (`[{index, items}]`, numbered in scan order), `scanned`, `skipped`, `too_short`.

## What it recognises

Every file and every file name is looked at in three normalized forms:

| Form | Makes these equal |
|---|---|
| Unicode NFKC, case folded | full-width and half-width characters (`１３８` and `138`, `ｊａｎｅ＠` and `jane@`), upper and lower case (`JANE`, a final `x` or `X` of an identity number) |
| ... and whitespace runs collapsed | `Jane  Roe` and `Jane Roe` |
| ... and separators removed (spaces, `-`, `.`, `/`, brackets, `,`, `+`) | `+86 138-0013-8000`, `0086 13800138000`, `1101 0519 4912 3100 2x`, `1,750` |

Dates (items of type `date`) are matched by a pattern over their written forms instead: `2005-03-04`, `2005/3/4`, `2005年3月4日`, `4.3.2005`, `03/04/2005`, `20050304`, `March 4, 2005`, `4 Mar 2005`. Digits right before or after break a match, so `120050304` or `2005-03-14` are not reported.

Word, Excel and PowerPoint files are read through the text of their XML parts, so a value split across formatting runs is still found. Other files are read as UTF-8 (or UTF-16 with a byte-order mark). A file with a NUL byte near its start is treated as binary and skipped; so are files over 50 MB and the directories `.git`, `.hg`, `.svn`, `node_modules`, `__pycache__`, `.venv` and `venv`. The vault itself is never reported.

## Limits

- **Short values.** A value with fewer than 4 characters (after separators are removed) would match almost everywhere, so it is not searched, and its item is listed under `too_short`. Lower the threshold with `--min-length`, expecting false finds.
- **Other forms.** A value that was abbreviated, translated, transliterated, split across lines with other text in between, or encoded (base64, URL encoding, compressed archives, PDF, images) is not found.
- **False finds.** Removing separators can join neighbouring numbers into one that happens to equal a stored one. Check a find in the window before acting on it.
- **It tells you what already happened.** Finding a value in a file the assistant could read means the assistant may already have read it.
