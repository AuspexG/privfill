---
status: proposed
---

# Subject aliases are resolved on the machine and never listed

Which people a person has stored details for, and how they are related (a wife, a former spouse, a child), is itself private. The caller cannot avoid hearing the words the person uses in the chat ("fill in my wife's name"), so hiding those words gains nothing. What is worth hiding is the whole map. We decided that subjects are kept under internal identifiers, that their aliases are encrypted in the vault, and that `privfill dims` never lists them: the caller writes `subject=wife` because the person said "wife", and privfill resolves it locally. An alias privfill does not know opens a local window where the person picks the subject it means and may keep it as a new alias; the caller learns only "resolved" or "cancelled". Other dimensions are visible by default, so the caller can reuse existing coordinates instead of inventing near-synonyms; the person can hide any of them.

## Considered Options

- **Neutral codes as subject names** (`家人1`). Rejected: the caller would still have to be told which code is the wife, and the person would have to remember the codes.
- **A relation graph** (`subject=wife.father`). Deferred: an alias already says how a subject relates to the person, and no current use needs a chain.
- **Every dimension hidden.** Rejected as the default: the caller could no longer find existing scenes and would create near-synonyms, and each unknown word would interrupt the person with a window.
