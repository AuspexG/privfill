# The vault

Items are kept in an encrypted vault: one file, `~/.privfill/vault.json` unless `PRIVFILL_VAULT` names another path. This page describes the file format and states what the encryption does and does not protect. The code is `privfill/vault.py`.

## Format

The vault is a JSON file with mode 600 in a directory with mode 700.

```json
{
 "privfill_vault": 1,
 "id": "3f0c...  32 hex characters, random, the vault identity",
 "version": 7,
 "unlock": [
  {
   "method": "keyring",
   "nonce": "base64, 12 bytes",
   "wrapped_key": "base64, the data key encrypted under the secret kept in the desktop keyring"
  },
  {
   "method": "passphrase",
   "kdf": "scrypt", "n": 131072, "r": 8, "p": 1,
   "salt": "base64, 16 bytes",
   "nonce": "base64, 12 bytes",
   "wrapped_key": "base64, the data key encrypted under the passphrase-derived key"
  }
 ],
 "items": [
  {
   "name": "date of birth",
   "type": "date",
   "updated": "2026-01-02T03:04:05Z",
   "nonce": "base64, 12 bytes",
   "ciphertext": "base64, the value and its authentication tag"
  }
 ],
 "seal": {"nonce": "base64, 12 bytes", "tag": "base64, 16 bytes"}
}
```

| Part | How it is made |
|---|---|
| Data key | 32 random bytes, generated when the vault is created. It encrypts every item and never changes. |
| Item | The value as UTF-8, encrypted with AES-256-GCM under the data key and a fresh random nonce each time it is written. The associated data is the JSON array `["privfill", 1, "item", id, name, type, updated]`. |
| Keyring secret | 32 random bytes kept in the desktop keyring (Secret Service) under the attributes `application = privfill`, `vault = <id>`. It wraps the data key; the associated data is `["privfill", 1, "key", id, "keyring"]`. |
| Unlock key | Derived from the passphrase with scrypt (`n` = 2^17, `r` = 8, `p` = 1: about 128 MiB of memory per guess) and a random salt. |
| Version | Counts up by one on every save. |
| Seal | An AES-256-GCM tag under the data key over `["privfill", 1, "seal", id, version, unlock entry, [[name, type, updated, nonce, ciphertext], ...]]`. It is checked whenever the vault is unlocked. |
| Wrapped key | The data key encrypted with AES-256-GCM under the unlock key. The associated data is `["privfill", 1, "key", id, method, kdf, n, r, p, salt]`. |

Three things follow from this layout.

- **Each item stands alone.** A command decrypts only the items it uses. A render that fills one placeholder decrypts one item.
- **Nothing can be moved.** Because the vault identity, the name, the type and the update time are part of what is authenticated, changing any of them, or putting one item's ciphertext under another item or into another vault, makes decryption fail. The command stops with exit code 1 and names the item.
- **Unlock methods can change.** Each entry in `unlock` wraps the same data key, so adding, removing or changing a method rewrites `unlock` (in one atomic save, covered by the seal) and leaves every item as it is. A method is a class with `wrap` and `unwrap`; see `Unlock` in `privfill/vault.py` and `KeyringUnlock` in `privfill/keyring.py`.

- **The list is sealed.** The seal covers every item and the version together, so removing, adding or reordering an entry, or raising the version, makes every command that unlocks the vault stop with exit code 1.

Names, types and update times are stored readable, so `privfill keys` works without the passphrase and without the encryption library. They are checked when an item is decrypted, not when it is listed.

## Unlocking

A vault can be unlocked by the desktop keyring, by a passphrase, or by either.

**Keyring (the default).** A new vault uses the desktop keyring (GNOME Keyring, KWallet or another Secret Service) when one can be reached, and the person types nothing. The keyring itself is unlocked by logging in to the desktop. It needs the Python library `secretstorage` (`python-secretstorage` on Arch, `python3-secretstorage` on Debian, Ubuntu and Fedora). When no keyring can be reached, a new vault is protected by a passphrase instead and the command says so in a line starting with `Note:`.

**Passphrase.** Typed by the person into a local dialog, never on a command line and never by the assistant. A new passphrase is asked twice. A command that reads or writes a value asks once per run; three wrong answers end the command with exit code 5, and closing the dialog ends it with exit code 3. In both cases nothing is changed.

**Both.** A command tries the keyring first and the passphrase second. Enrolling both is the way to recover if the keyring entry is lost (a new desktop profile, a reinstalled system): the passphrase still opens the vault, and `unlock add keyring` puts a new entry in the keyring.

```
privfill.py unlock                     which methods the vault has; whether a keyring is reachable
privfill.py unlock add keyring         also unlock by the keyring
privfill.py unlock add passphrase      also unlock by a passphrase (asked twice)
privfill.py unlock change passphrase   replace the passphrase
privfill.py unlock remove keyring      stop using the keyring, and delete its entry
privfill.py unlock remove passphrase   stop using the passphrase
```

Each of these opens the vault with a method it already has, rewraps the data key and saves once; the last method cannot be removed. The keyring secret and the passphrase are never printed, logged or written outside the keyring and the dialog.

There is no other recovery. If every enrolled method is lost (the passphrase forgotten and the keyring entry gone), the values are lost and the details have to be entered again into a new vault.

**What the keyring trades.** Any program running as you while the desktop session is unlocked can ask the keyring for the secret, and with it decrypt the vault. That includes an assistant's shell commands. The guard denies the obvious ways (`secret-tool`, `secretstorage`, the Secret Service D-Bus name; see `docs/guard.md`), but it judges command text and cannot stop a program that hides what it does. If that is not acceptable, use a passphrase only: `unlock add passphrase`, then `unlock remove keyring`.

### Checking the keyring on a real desktop

The tests use a fake keyring. To check the real one once, with a throwaway vault:

1. `export PRIVFILL_VAULT=/tmp/privfill-check/vault.json`
2. `python3 privfill.py unlock`: it should say `Desktop keyring: available.` If not, the reason follows.
3. `python3 privfill.py add nickname` and type a made-up nickname. No passphrase dialog should appear.
4. Open the desktop's keyring manager (Seahorse, KWalletManager): an entry named `privfill vault unlock key` should be there.
5. `python3 privfill.py unlock add passphrase` (choose any passphrase), then delete the entry in the keyring manager, then fill a file with `{{nickname}}` through `render`: the passphrase dialog should now appear and the render should work.
6. `python3 privfill.py unlock remove passphrase` should refuse only if it is the last method; afterwards remove `/tmp/privfill-check`.

## Durability

| Risk | What the program does |
|---|---|
| Two commands write at once | Writers take an exclusive lock on `vault.json.lock` and read the vault again under it, so both entries are stored. Unlocking happens before the lock is taken, so a passphrase dialog never holds up another writer. |
| A crash or power loss in the middle of a write | The new vault is written to a temporary file in the same directory, flushed to disk, renamed over the old one, and the directory is flushed. A crash leaves the old vault or the new one, never a mix. A temporary file a crash left behind holds only ciphertext and is removed by the next write. |
| The vault is replaced by an older copy | `vault.state.json` beside the vault records the newest version saved on this machine. A vault with a lower version stops every command with exit code 1 and a message pointing to `restore`. The version cannot be raised without the data key, because the seal covers it. |
| The vault is damaged or a change must be undone | Before each save the file being replaced is copied to `backups/vault-<version>.json`. The newest five are kept. |

The backups are copies of the vault file: ciphertext, with the same readable names, types and update times. No file the program keeps holds a value in the clear.

```
privfill.py restore              list the backups: version, time saved, item count
privfill.py restore <version>    put that backup back
```

A restored backup becomes a new version (higher than any seen), so it is not mistaken for a rollback, and the vault it replaces becomes a backup in turn. Restoring works when the current vault is damaged or missing, using the backup's own unlock entry. It asks for the passphrase.

**Limits.** Rollback detection compares against a record on the same disk: someone who can replace both `vault.json` and `vault.state.json` with older copies is not detected. Deleting `vault.state.json` only switches the check off until the next save. The backups live in the same directory as the vault and protect against damage and mistakes, not against losing the disk: copy a manager export elsewhere for that.

## Exported backups

`privfill.py manage` can export every item to a new file in this same format, with the data key wrapped under a passphrase chosen for the backup (so a backup does not depend on the unlock method or the machine it came from) and import it again. The export refuses to overwrite a file and is written in one step. A backup holds the same things the vault file does, and the same limits apply: names, types, update times and value lengths are readable without its passphrase, and a forgotten backup passphrase cannot be recovered. Importing asks before replacing items that exist.

## Dependency

Encryption uses the Python library `cryptography`, installed with the system package manager: `python-cryptography` on Arch, `python3-cryptography` on Debian, Ubuntu and Fedora. Without it, every command that needs a value stops with exit code 1 and a message naming these packages.

## What the vault protects

Someone who obtains the vault file without the passphrase does not learn any value. That covers:

- another program running as you that reads the file,
- an assistant that reads the file against the rules,
- a backup, a synced copy, or the disk of a lost machine.

A changed item is noticed the next time it is used, so a value cannot be silently replaced or exchanged with another. Removing or adding an entry, or putting back an older copy of the vault, is noticed too (see Durability).

## What it does not protect

- **What the file shows without the passphrase**: how many items there are, their names, their types, when each was last changed, and the length of each value in bytes.
- **A weak passphrase.** The key derivation makes each guess slow and memory-hungry; it cannot make a short or common passphrase strong.
- **The machine while you use it.** A program running as you can watch the passphrase being typed, replace the privfill program, or read the memory of the running process. Encryption at rest does not help against that.
- **A caller that sets up its own vault.** The test hook `PRIVFILL_PASSPHRASE_TEST` takes the passphrase from the environment, for vaults of fake data. It refuses any vault inside `~/.privfill`, but a caller that ignores the rules could point `PRIVFILL_VAULT` elsewhere and create a vault whose passphrase it knows. The sign is that you were never asked to choose a passphrase.
- **Filled outputs.** They are ordinary files containing your details in plain text.
- **Rollback of both files at once.** Putting back older copies of both `vault.json` and `vault.state.json` is not detected (see Durability).
- **A plain-text data file from an earlier version** (`profile.md`). It is not read, not moved into the vault and not deleted. Until a migration command exists, enter the details again and remove the old file yourself.
