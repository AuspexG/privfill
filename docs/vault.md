# The vault

Items are kept in an encrypted vault: one file, `~/.privfill/vault.json` unless `PRIVFILL_VAULT` names another path. This page describes the file format and states what the encryption does and does not protect. The code is `privfill/vault.py`.

## Format

The vault is a JSON file with mode 600 in a directory with mode 700.

```json
{
 "privfill_vault": 1,
 "id": "3f0c...  32 hex characters, random, the vault identity",
 "unlock": {
  "method": "passphrase",
  "kdf": "scrypt", "n": 131072, "r": 8, "p": 1,
  "salt": "base64, 16 bytes",
  "nonce": "base64, 12 bytes",
  "wrapped_key": "base64, the data key encrypted under the unlock key"
 },
 "items": [
  {
   "name": "date of birth",
   "type": "date",
   "updated": "2026-01-02T03:04:05Z",
   "nonce": "base64, 12 bytes",
   "ciphertext": "base64, the value and its authentication tag"
  }
 ]
}
```

| Part | How it is made |
|---|---|
| Data key | 32 random bytes, generated when the vault is created. It encrypts every item and never changes. |
| Item | The value as UTF-8, encrypted with AES-256-GCM under the data key and a fresh random nonce each time it is written. The associated data is the JSON array `["privfill", 1, "item", id, name, type, updated]`. |
| Unlock key | Derived from the passphrase with scrypt (`n` = 2^17, `r` = 8, `p` = 1: about 128 MiB of memory per guess) and a random salt. |
| Wrapped key | The data key encrypted with AES-256-GCM under the unlock key. The associated data is `["privfill", 1, "key", id, method, kdf, n, r, p, salt]`. |

Three things follow from this layout.

- **Each item stands alone.** A command decrypts only the items it uses. A render that fills one placeholder decrypts one item.
- **Nothing can be moved.** Because the vault identity, the name, the type and the update time are part of what is authenticated, changing any of them, or putting one item's ciphertext under another item or into another vault, makes decryption fail. The command stops with exit code 1 and names the item.
- **Unlock methods can change.** Only `unlock` depends on the passphrase. Another method (the desktop keyring is planned) wraps the same data key, so switching methods rewrites `unlock` and leaves every item as it is. A method is a class with `wrap` and `unwrap`; see `Unlock` in `privfill/vault.py`.

Names, types and update times are stored readable, so `privfill keys` works without the passphrase and without the encryption library. They are checked when an item is decrypted, not when it is listed.

## Unlocking

The passphrase is typed by the person into a local dialog, never on a command line and never by the assistant. A new vault asks for it twice. A command that reads or writes a value asks once per run; three wrong answers end the command with exit code 5, and closing the dialog ends it with exit code 3. In both cases nothing is changed.

There is no recovery. If the passphrase is forgotten the values are lost, and the details have to be entered again into a new vault.

## Backups

`privfill.py manage` can export every item to a new file in this same format, with the data key wrapped under a passphrase chosen for the backup (so a backup does not depend on the unlock method or the machine it came from) and import it again. The export refuses to overwrite a file and is written in one step. A backup holds the same things the vault file does, and the same limits apply: names, types, update times and value lengths are readable without its passphrase, and a forgotten backup passphrase cannot be recovered. Importing asks before replacing items that exist.

## Dependency

Encryption uses the Python library `cryptography`, installed with the system package manager: `python-cryptography` on Arch, `python3-cryptography` on Debian, Ubuntu and Fedora. Without it, every command that needs a value stops with exit code 1 and a message naming these packages.

## What the vault protects

Someone who obtains the vault file without the passphrase does not learn any value. That covers:

- another program running as you that reads the file,
- an assistant that reads the file against the rules,
- a backup, a synced copy, or the disk of a lost machine.

A changed item is noticed the next time it is used, so a value cannot be silently replaced or exchanged with another.

## What it does not protect

- **What the file shows without the passphrase**: how many items there are, their names, their types, when each was last changed, and the length of each value in bytes.
- **A weak passphrase.** The key derivation makes each guess slow and memory-hungry; it cannot make a short or common passphrase strong.
- **The machine while you use it.** A program running as you can watch the passphrase being typed, replace the privfill program, or read the memory of the running process. Encryption at rest does not help against that.
- **A caller that sets up its own vault.** The test hook `PRIVFILL_PASSPHRASE_TEST` takes the passphrase from the environment, for vaults of fake data. It refuses any vault inside `~/.privfill`, but a caller that ignores the rules could point `PRIVFILL_VAULT` elsewhere and create a vault whose passphrase it knows. The sign is that you were never asked to choose a passphrase.
- **Filled outputs.** They are ordinary files containing your details in plain text.
- **Removal and rollback.** Deleting an item from the file, or putting back an older copy of the whole vault or of one item, is not detected.
- **Concurrent use.** Two commands writing at the same moment can lose one of the updates.
- **A plain-text data file from an earlier version** (`profile.md`). It is not read, not moved into the vault and not deleted. Until a migration command exists, enter the details again and remove the old file yourself.
