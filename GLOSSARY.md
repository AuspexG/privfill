# privfill

privfill lets an AI assistant use a person's details in documents without ever seeing them. This is the language of that domain: what is stored, how a document refers to it, and who may see what.

## People

**Person**:
The human whose details are stored and who alone may see them.
_Avoid_: user, owner

**Caller**:
The AI assistant that writes masters and runs privfill's commands, and must never receive a value.
_Avoid_: agent, client, model

## What is stored

**Item**:
One stored value together with its attribute and its coordinates, for example the work email of the person's wife.
_Avoid_: fact, entry, record, field

**Attribute**:
What kind of detail an item holds, such as phone or date of birth; several items may share one.
_Avoid_: key, item name, field

**Value**:
The detail itself, as the person entered it after normalization. It never leaves the machine.
_Avoid_: content, data

**Dimension**:
A named axis along which items of one attribute differ, such as subject, scene or time.
_Avoid_: tag, category, facet

**Coordinate**:
An item's position on one dimension, written `dimension=value`, such as `scene=work`.
_Avoid_: tag, label, filter value

**Subject**:
The person an item is about; the built-in dimension whose default is the person themself.
_Avoid_: owner, entity, member

**Alias**:
A word that names a subject, such as wife or 老婆; one subject may have several.
_Avoid_: role, relation, nickname

**Hidden dimension**:
A dimension whose coordinates privfill never lists to the caller; the caller can only use words the person has said.
_Avoid_: private dimension, secret tag

**Preferred item**:
The item used when a placeholder matches several items and asks for no list operation.
_Avoid_: default value, primary, main item

**Vault**:
The local encrypted store of all items.
_Avoid_: database, profile, data file

## How documents use items

**Master**:
A document with placeholders and no values; the only kind of document the caller reads or writes.
_Avoid_: template, draft

**Output**:
A copy of a master with the values filled in; the caller must not read it.
_Avoid_: result, rendered file, filled document

**Placeholder**:
A marker in a master that stands for one or more items, written `{{attribute|dimension=value|format}}`.
_Avoid_: variable, tag, token

**Format**:
A step in a placeholder that reshapes a value, such as `last4` or `年月日`; several are applied left to right.
_Avoid_: filter, modifier, pipe

**List operation**:
A format that turns all matching items into one text, such as `join:、`, `count` or `第1个`.
_Avoid_: aggregate, array function

**Derived value**:
A value computed when filling from another item, such as the birthday held in an ID number; never stored.
_Avoid_: computed item, calculated field, virtual item

**Entry protocol**:
The JSON the caller sends to describe the questions of a form; it holds questions, never answers.
_Avoid_: schema, form spec
