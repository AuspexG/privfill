---
status: proposed
---

# Derived values are computed when filling, never stored

Some details are contained in others: a PRC resident ID number holds the birthday and the sex, and a birthday gives the age on any day. We decided to compute such derived values when filling and keep them only in memory for that one run. Storing them would multiply what is stored by the number of derivations, would let them drift from their source when the source changes, and could not hold a value that changes daily, such as age. Where the person has also stored the same detail as an item, the stored item wins; the derived value is used only when no item matches. Because one placeholder may then be filled from either source, the report names the source (`性别 x1, derived from 身份证号`) without the value, and `privfill check` compares stored and derived values locally and reports only whether they agree.

## Considered Options

- **The derived value always wins.** Rejected: an error in an official document could then never be corrected by the person.
- **Never fall back to a derived value.** Rejected by the person: they want `{{性别}}` to work without entering what the ID number already says.

## Consequences

- A derived value takes the sensitivity of its source: `{{身份证号|性别}}` asks for the same local confirmation as the ID number itself.
- An age filled today differs from the same master filled next year; the output, not the master, records the moment.
- A fallback has to know which attribute names mean the same derivation (`性别`, `sex`, `gender`). That vocabulary is fixed in the program, and an attribute named differently gets no fallback.
