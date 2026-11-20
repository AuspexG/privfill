# Design: multi-dimensional items, list operations and derived values

Status: proposed, not implemented. Decisions and their reasons: [ADR 0001](../adr/0001-items-carry-coordinates-on-dimensions.md), [ADR 0002](../adr/0002-derived-values-are-computed-never-stored.md), [ADR 0003](../adr/0003-subject-aliases-stay-on-the-machine.md). Terms: [GLOSSARY.md](../../GLOSSARY.md).

## 1. What this adds

| Need | Today | After |
|---|---|---|
| The last four digits of the ID number | `{{身份证号\|后4位}}` (already works) | unchanged |
| The birthday or sex held in the ID number | must be entered again as separate items | `{{身份证号\|生日}}`, `{{身份证号\|性别}}` |
| Several values of one kind (two emails, a list of awards) | one item per name: `邮箱`, `学校邮箱` | several items of one attribute: `{{邮箱\|场景=学校}}`, `{{获奖\|join:、}}` |
| Details of other people | not possible without inventing item names | `{{姓名\|主体=妻子}}` |

Nothing here changes the principle of `PROTOCOL.md`: no command the caller sends and no output it receives contains a value.

## 2. The model

An **item** is one value with one **attribute** and a set of **coordinates**, one per **dimension** it has.

```
attribute   coordinates                        value (encrypted)
姓名        主体=本人                           …
姓名        主体=妻子                           …
邮箱        主体=本人  场景=学校   preferred    …
邮箱        主体=本人  场景=个人                …
获奖        主体=本人  年份=2023   order 1      …
获奖        主体=本人  年份=2024   order 2      …
```

Only items that exist are stored (sparse); storage grows with the number of items, not with the number of dimensions or derivations.

### 2.1 Dimensions

| Dimension | Kind | Matching | Notes |
|---|---|---|---|
| `主体` (`subject`) | built-in | through aliases (section 6) | every item has one; default `本人` (`self`) |
| `时间` (`time`) | built-in, phase 3 | interval contains the asked moment | an item's validity period, for example an old and a new address |
| `来源` (`source`) | built-in, kept by privfill | exact | `entered` or `derived`; never set by the caller |
| anything else (`场景`, `项目`, `年份`, …) | defined by the person | exact text | created when first used in an entry protocol |

An item need not have a coordinate on every dimension. Dimension names and coordinate texts may not contain `|`, `{`, `}`, `=` or a newline.

### 2.2 Order and the preferred item

Items of one attribute and subject keep the order they were entered in; the person can change it in the manager window. Among items that match the same placeholder, at most one is the **preferred item**. Only the person marks it, in the form or the manager window, never the caller.

## 3. Placeholders

```
{{attribute|step|step|…}}
```

The text before the first `|` is the attribute. Each following step is one of:

| Step | Recognized by | Example |
|---|---|---|
| a coordinate | contains `=` and does not start with `join:` or `连接:` | `主体=妻子`, `场景=工作` |
| a list operation | one of the names in section 4 | `join:、`, `count`, `第2个` |
| a derivation | one of the names in section 5 | `生日`, `性别`, `年龄` |
| a format | as in `PROTOCOL.md` 3.2 | `后4位`, `YYYY/MM/DD`, `年月日` |

All coordinates are applied first, wherever they are written; the other steps run left to right. Recommended order: coordinates, then a list operation or derivation, then formats.

```
{{姓名}}                               the person's own name
{{姓名|主体=妻子}}                      the wife's name
{{邮箱|场景=学校}}                      the person's school email
{{邮箱|主体=妻子|场景=工作|后4位}}       last four characters of the wife's work email
{{身份证号|生日|年月日}}                2005年3月4日
{{获奖|年份=2023|join:、}}              every 2023 award, joined with 、
{{手机号|第2个|后4位}}                  last four digits of the second phone number
```

### 3.1 Matching

1. Take the items of the attribute whose coordinates agree with every coordinate in the placeholder. A placeholder without `主体` asks for `主体=本人`.
2. If the next step is a list operation, it receives all of them, in order (section 4).
3. Otherwise exactly one is needed: if one matches, use it; if several match, use the preferred item among them.
4. If none matches, or several match and none of them is preferred: exit code 2. The message lists the dimensions and coordinates that tell the matching items apart, never values, and for a hidden dimension only that it differs.
5. If no item matches and the attribute name is in the fallback vocabulary of a derivation (section 5.2), the derived value is used instead.

## 4. List operations

| Step | Result |
|---|---|
| `join:SEP` or `连接:SEP` | all matching values, joined by SEP; in SEP, `\n` is a line break, `\t` a tab and `\\` a backslash |
| `count` or `个数` | how many items match, as a number; `0` when none |
| `第N个` or `nth:N` | the N-th matching item, counting from 1; exit code 2 if there are fewer |

After a list operation the value is text, and later steps (formats) apply to that text. `join` over no items gives an empty text, which is reported as `x0`.

## 5. Derived values

Computed at fill time, held in memory for that run only, never stored ([ADR 0002](../adr/0002-derived-values-are-computed-never-stored.md)).

| Derivation | Applies to | Result | Can be followed by |
|---|---|---|---|
| `生日` / `birthday` | `id_card` | characters 7 to 14 as a date, `2005-03-04` | any date format, `年龄` |
| `性别` / `sex` | `id_card` | the 17th digit: odd `男` / `male`, even `女` / `female`, in the language of the step's name | |
| `年龄` / `age` | `date`, `id_card` | whole years on the day of filling, local time | `first`/`last` formats |

A derivation on an item of another type, or an ID number whose embedded date is not a real date, ends with exit code 2. The report names derived uses: `Filled 性别 x1 (derived from 身份证号)`.

### 5.1 Sensitivity

A derived value has the sensitivity of the item it comes from, so `{{身份证号|性别}}` asks for the same local confirmation as `{{身份证号}}`.

### 5.2 Fallback

When no item matches a placeholder whose attribute is one of these names, and the same subject has exactly one `id_card` item (or a preferred one), the derivation is used:

| Attribute names | Derivation |
|---|---|
| `生日`, `出生日期`, `birthday`, `date of birth` | `生日` |
| `性别`, `sex`, `gender` | `性别` |
| `年龄`, `age` | `年龄` (from a birthday item first, else from the ID number) |

A stored item always wins over a fallback.

### 5.3 `privfill check`

Compares, for every subject, stored items with the values derivable from that subject's ID number (birthday, sex), locally. It reports `consistent`, `inconsistent` or `nothing to compare` per pair of attribute names and never prints a value. Exit code 0 when everything compared is consistent, and a new code, 8, when something is inconsistent (6 stays reserved for `audit`).

## 6. Visibility

| What | The caller can list it | How |
|---|---|---|
| attributes | yes | `privfill keys`, as today |
| visible dimensions and their coordinates | yes | `privfill dims` |
| hidden dimensions | only their names, marked hidden | `privfill dims` |
| subject aliases | no | `主体` is hidden by default |

A hidden coordinate is accepted only if it names something privfill knows. An unknown subject alias (`主体=老婆`) opens a local window: the person picks the subject it means, or creates a new subject, and may keep the word as a new alias. The caller learns only `resolved` or `cancelled` (exit code 3). Any dimension can be hidden or shown by the person in the manager window.

## 7. Entering items

The entry protocol gains three optional field keys; they still describe questions only.

| Key | Type | Meaning |
|---|---|---|
| `subject` | string | the alias of the subject the question is about; unknown aliases go through the window in section 6 |
| `coords` | object of strings | coordinates on other dimensions, for example `{"场景": "学校"}`; may create a new visible dimension |
| `many` | boolean | the person may enter several values, one per line; each becomes one item with these coordinates, in that order |

The form shows whose details are asked for, marks a field that already has items of the same attribute and subject, and offers a "preferred" choice there. The report names, per field, how many items were stored, updated, kept or left empty, and the placeholder that reaches them.

## 8. Vault format version 2

Changes to `docs/vault.md`:

- Each item entry gains `coords` (visible coordinates in plain text, hidden coordinates inside the encrypted part), `order` and `preferred`. All of them are covered by the item's associated data, so they cannot be moved between items unnoticed.
- A subjects table: an internal identifier per subject and its aliases, encrypted.
- A dimensions table: name, kind and whether it is hidden.
- Migration on first unlock by the new version: every item keeps its name as its attribute and gets `主体=本人`; a backup of the version 1 file is kept as for `restore`. Every existing placeholder keeps working.

## 9. Other parts that change

| Part | Change |
|---|---|
| `PROTOCOL.md` | terms (item, attribute, coordinate), placeholder grammar, matching, list operations, derivations, new field keys, `dims`, `check` |
| `audit` | searches every item of every subject, and the birthday derived from each ID number; reports attribute and subject alias only when the subject is visible |
| manager window | a grid of subjects × attributes, filters by dimension, order, preferred item, aliases, hidden dimensions |
| MCP server | the new field keys in the form tool's schema; `dims` and `check` as tools |
| skill and `AGENT_GUIDE.md` | run `dims` before inventing a coordinate; use the person's own words for subjects; never ask what an alias means |
| `test_no_value_escapes.py` | hostile-input scenarios for `dims`, `check` and every new step |

## 10. Phases

| Phase | Contents | Vault format |
|---|---|---|
| 1 | Chained steps; derivations `生日`, `性别`, `年龄`; fallback; `check`; derived birthday in `audit` | unchanged |
| 2 | Dimensions, subjects and aliases, list operations, preferred item, `many`, `dims`, manager grid, migration | version 2 |
| 3 | The `时间` dimension and asking by moment (`时间=2023`, `时间=现在`) | additive |

Each phase is one pull request with its tests and documentation, scanned with `privfill audit` before pushing.

## 11. Open questions

- **Time (phase 3):** how a period is entered, what `时间=2023` matches when two periods overlap, and whether `时间=现在` is implied when no time is given.
- **Formats on every item before a join:** `{{手机号|后4位|join:、}}` would need a step to apply to each item; deferred until a use needs it.
- **Region from the ID number:** characters 1 to 6 are a region code; turning it into a place name needs a code table that changes over time.
- **Sorting by a dimension:** `{{获奖|排序=年份|join:、}}` instead of entry order.
- **More derivations:** which other items contain checkable or derivable details worth adding to the fixed vocabulary.
