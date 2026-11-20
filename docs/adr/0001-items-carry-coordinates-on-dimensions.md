---
status: proposed
---

# Items carry coordinates on dimensions, stored sparsely

A person has details that share an attribute but differ in some other way: two email addresses, the names of themself and of their wife, an old and a new address. We decided that every item is one value with an attribute and a set of coordinates (`subject=wife`, `scene=work`), and that the vault stores only the items that exist, one row each. Subject and time are built-in dimensions with their own matching rules; every other dimension is defined by the person and matched exactly. Several values of one attribute are simply several items, so there is no separate list type: placeholders gather them with list operations (`join:`, `count`, `第N个`). This changes the vault format (version 2), which is why it is recorded.

## Considered Options

- **A list type** (one item holding an array). Rejected: the elements could not carry coordinates of their own, and it would be a second way to express "several values".
- **A fixed set of dimensions.** Rejected at the person's request: the meaningful ways to tell items apart (scene, project, language) differ from person to person.
- **A dense array over all dimensions.** Rejected: its size is the product of the dimension sizes (10 subjects × 50 attributes × 5 scenes × 20 periods is 50,000 cells, almost all empty), while a person has tens to hundreds of real items.
- **Embedding vectors of values.** Rejected: embeddings can be inverted back to the text, would need either an outside model (values leave the machine) or a heavy local one, and filling needs exact values, not similarity.

## Consequences

- A placeholder no longer names exactly one item. It names an attribute plus coordinates, and may match none, one or several items; the matching rules are in `docs/design/multi-dimensional-items.md`.
- Existing vaults are migrated: each item keeps its name as its attribute and gets `subject=self`, so every existing placeholder keeps working.
- `=` joins `|`, `{` and `}` as characters an attribute or dimension name cannot contain.
