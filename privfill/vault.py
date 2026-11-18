"""The encrypted vault: a Store whose values are encrypted one item at a time.

The vault is one JSON file (mode 600, directory 700):

    {"privfill_vault": 1,
     "id": "<random vault identity>",
     "unlock": {"method": "passphrase", ...},      how the data key is wrapped
     "items": [{"name", "type", "updated", "nonce", "ciphertext"}, ...]}

A random 256-bit data key encrypts every item with AES-256-GCM under a fresh nonce. The associated
data of an item is the vault identity, the item's name, its type and its update time, so none of
them can be changed, and no ciphertext can be moved to another item or another vault, without the
decryption failing. The data key itself is stored wrapped by an unlock key, which an Unlock method
produces; changing the method rewraps the data key and leaves the items alone.

Names, types and update times are readable without unlocking. They are checked when the item is
decrypted, not when it is listed.
"""
import base64
import contextlib
import fcntl
import json
import os
import re
import shutil
import time

from .atomic import atomic_write, fsync_dir, temp_prefix
from .store import Cancelled, Damaged, Locked, MissingLibrary, Store, StoreError

FORMAT = 1
KEY_BYTES = 32
NONCE_BYTES = 12
COST = 2 ** 17  # scrypt N with r=8: 128 MiB of memory per passphrase guess
TEST_COST = 2 ** 10  # for vaults whose passphrase comes from a test hook
MAX_COST = 2 ** 20  # a vault file may not ask for more than 1 GiB
ATTEMPTS = 3  # passphrase tries per run
NOT_A_VAULT = "the vault file is not in the vault format"
STATE_FILE = "vault.state.json"  # the newest version of each vault saved here, for rollback detection
BACKUP_DIR = "backups"
BACKUP_NAME = re.compile(r"vault-(\d+)\.json")
KEEP_BACKUPS = 5


def crypto():
    """The parts of the `cryptography` library the vault uses, imported on first need."""
    try:
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    except ImportError:
        raise MissingLibrary(
            "the Python library `cryptography` is not installed. Install it with the system package manager: "
            "`python-cryptography` (Arch), `python3-cryptography` (Debian, Ubuntu, Fedora)") from None
    return AESGCM, Scrypt, InvalidTag


def b64(data):
    return base64.b64encode(data).decode("ascii")


def unb64(text, length=None):
    """The bytes of a base64 field of the vault file, which must have the given length if one is given."""
    try:
        data = base64.b64decode(text, validate=True)
    except (ValueError, TypeError):
        raise Damaged(NOT_A_VAULT) from None
    if length is not None and len(data) != length:
        raise Damaged(NOT_A_VAULT)
    return data


def bound(purpose, *parts):
    """Associated data: what a ciphertext is tied to, encoded so that no two lists of parts collide."""
    return json.dumps(["privfill", FORMAT, purpose, *parts], ensure_ascii=True, separators=(",", ":")).encode("ascii")


class Unlock:
    """One way of protecting the data key. A vault stores what wrap returns and hands it back to unwrap."""

    method = ""
    why_not = ""  # when not available: why, for the person

    def available(self):
        """Whether this method can be used on this machine now."""
        return True

    def forget(self, vault_id):
        """Drop anything kept outside the vault file for it, once the vault no longer uses this method."""

    def wrap(self, key, vault_id):
        """The unlock entry (a JSON object with "method") that protects key for this vault."""
        raise NotImplementedError

    def unwrap(self, entry, vault_id):
        """The data key from an unlock entry. Raises Locked if it cannot be recovered, Cancelled if the person gives up."""
        raise NotImplementedError


class PassphraseUnlock(Unlock):
    """The data key wrapped by a key derived from a passphrase with scrypt.

    ask(kind) asks the person and returns the passphrase, or None if they cancel. kind is "new"
    (choose one), "unlock" (enter it) or "retry" (the last one was wrong).
    """

    method = "passphrase"

    def __init__(self, ask, cost=COST):
        self.ask = ask
        self.cost = cost

    def wrap(self, key, vault_id):
        AESGCM, _, _ = crypto()
        passphrase = self.ask("new")
        if passphrase is None:
            raise Cancelled("the vault was not created")
        entry = {"method": self.method, "kdf": "scrypt", "n": self.cost, "r": 8, "p": 1,
                 "salt": b64(os.urandom(16)), "nonce": b64(os.urandom(NONCE_BYTES))}
        wrapped = AESGCM(self._derive(passphrase, entry)).encrypt(unb64(entry["nonce"]), key, self._bound(entry, vault_id))
        return dict(entry, wrapped_key=b64(wrapped))

    def unwrap(self, entry, vault_id):
        AESGCM, _, InvalidTag = crypto()
        try:
            nonce, wrapped = unb64(entry["nonce"], NONCE_BYTES), unb64(entry["wrapped_key"])
            aad = self._bound(entry, vault_id)
        except KeyError:
            raise Damaged(NOT_A_VAULT) from None
        for attempt in range(ATTEMPTS):
            passphrase = self.ask("retry" if attempt else "unlock")
            if passphrase is None:
                raise Cancelled("the vault was not unlocked")
            try:
                return AESGCM(self._derive(passphrase, entry)).decrypt(nonce, wrapped, aad)
            except InvalidTag:
                pass
        raise Locked("the vault was not unlocked: wrong passphrase")

    @staticmethod
    def _bound(entry, vault_id):
        return bound("key", vault_id, *(entry.get(k) for k in ("method", "kdf", "n", "r", "p", "salt")))

    @staticmethod
    def _derive(passphrase, entry):
        _, Scrypt, _ = crypto()
        n, r, p = entry.get("n"), entry.get("r"), entry.get("p")
        sane = entry.get("kdf") == "scrypt" and all(type(x) is int for x in (n, r, p)) \
            and 2 <= n <= MAX_COST and n & (n - 1) == 0 and (r, p) == (8, 1)
        if not sane:
            raise Damaged("the vault file names a key derivation this version does not support")
        return Scrypt(salt=unb64(entry.get("salt")), length=KEY_BYTES, n=n, r=r, p=p).derive(passphrase.encode("utf8"))


class VaultStore(Store):
    """Items in an encrypted vault file. Unlocks at most once, and only when a value is read or written.

    With history (the person's own vault), every save also keeps the file it replaces as an
    encrypted backup and records the vault's version outside the file, so that an older copy put
    in its place is noticed. Backups made by the manager are plain vault files without history.
    """

    def __init__(self, path, unlock, history=False, keep=KEEP_BACKUPS):
        """unlock is one Unlock method, or a list of them in order of preference: a new vault uses the
        first one available, and an existing vault is opened with the first enrolled one that works."""
        self.path = path
        self.label = os.path.basename(path)
        self.directory = os.path.dirname(path)
        self.unlocks = list(unlock) if isinstance(unlock, (list, tuple)) else [unlock]
        self.notice = None  # something the person should know about how the vault was set up
        self._holding = False  # whether this object holds the writers' lock
        self.history = history
        self.keep = keep
        self._data_key = None

    @property
    def unlock(self):
        """The preferred unlock method."""
        return self.unlocks[0]

    # Where things live.

    @property
    def state_path(self):
        return os.path.join(self.directory, STATE_FILE)

    @property
    def backup_dir(self):
        return os.path.join(self.directory, BACKUP_DIR)

    # The Store interface.

    def create(self):
        if os.path.exists(self.path):
            return False
        vault_id = os.urandom(16).hex()
        key = os.urandom(KEY_BYTES)
        usable = [u for u in self.unlocks if u.available()]
        if not usable:
            raise StoreError("no way to unlock a new vault is available: " + "; ".join(u.why_not for u in self.unlocks))
        if usable[0] is not self.unlocks[0]:
            self.notice = (f"{self.unlocks[0].why_not}, so the vault is protected by a {usable[0].method} instead; "
                           f"`privfill unlock add {self.unlocks[0].method}` switches later")
        vault = {"privfill_vault": FORMAT, "id": vault_id, "version": 1,
                 "unlock": [usable[0].wrap(key, vault_id)], "items": []}
        vault["seal"] = self._seal(vault, key)
        os.makedirs(self.directory or ".", mode=0o700, exist_ok=True)
        with self._locked():
            try:
                fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:  # another process created it first
                return False
            with os.fdopen(fd, "w", encoding="utf8") as f:
                f.write(self._dump(vault))
                f.flush()
                os.fsync(f.fileno())
            fsync_dir(self.directory or ".")
            self._remember(vault)
        self._data_key = key
        return True

    def names(self):
        return [e["name"] for e in self._load()["items"]]

    def entries(self):
        return [(e["name"], e["type"]) for e in self._load()["items"]]

    def read(self, name):
        vault = self._opened()
        entry = self._entry(vault, name)
        if entry is None:
            return None
        AESGCM, _, InvalidTag = crypto()
        try:
            return AESGCM(self._data_key).decrypt(unb64(entry["nonce"], NONCE_BYTES), unb64(entry["ciphertext"]),
                                                  self._bound(vault, entry)).decode("utf8")
        except InvalidTag:
            raise Damaged(f"the item '{name}' cannot be decrypted: the vault file was altered or is damaged") from None

    def write(self, name, value, type="text"):
        self.create()
        AESGCM, _, _ = crypto()
        self._opened()  # unlock before taking the lock: the person may take a while at the dialog
        with self._locked():
            vault = self._opened()  # again, under the lock: another process may have saved meanwhile
            entry = {"name": name, "type": type, "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
            nonce = os.urandom(NONCE_BYTES)
            entry.update(nonce=b64(nonce), ciphertext=b64(
                AESGCM(self._data_key).encrypt(nonce, value.encode("utf8"), self._bound(vault, entry))))
            old = self._entry(vault, name)
            if old is None:
                vault["items"].append(entry)
            else:
                vault["items"][vault["items"].index(old)] = entry
            self._save(vault)
        return old is not None

    def delete(self, name):
        if self._entry(self._load(), name) is None:
            return False
        self._opened()
        with self._locked():
            vault = self._opened()
            entry = self._entry(vault, name)
            if entry is None:
                return False
            vault["items"].remove(entry)
            self._save(vault)
        return True

    # Backups.

    def backups(self):
        """[(version, saved time, item count)] of the kept backups, newest first. Reads no value."""
        out = []
        for version, path in self._backup_files():
            try:
                with open(path, encoding="utf8") as f:
                    vault = json.load(f)
                count = len(vault["items"])
            except (OSError, ValueError, KeyError, TypeError):
                count = None
            out.append((version, time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(path))), count))
        return out

    def restore(self, version):
        """Put the backup of this version back in place, as a new version. Returns its item count.

        Works when the current vault is damaged or missing too: the backup's own unlock entry is used.
        """
        found = dict(self._backup_files()).get(version)
        if found is None:
            raise StoreError("there is no backup of that version")
        backup = self._parse(found)
        os.makedirs(self.directory or ".", mode=0o700, exist_ok=True)
        with self._locked():
            try:
                current = self._load()
            except Damaged:
                current = None
            if current and current.get("items") is not None and "id" in current and current["id"] != backup["id"]:
                raise Damaged("that backup belongs to another vault")
            if self._data_key is None:
                self._data_key = self._unwrap(backup)
            self._check_seal(backup)
            newest = max(self._seen(backup["id"]), current.get("version", 0) if current else 0)
            backup["version"] = newest  # _save counts it up
            self._save(backup)
        return len(backup["items"])

    # Inside.

    def _opened(self):
        """The vault, unlocked and with its seal checked."""
        vault = self._load()
        if self._data_key is None:
            if "unlock" not in vault:  # no vault yet: nothing to unlock
                return vault
            self._data_key = self._unwrap(vault)
        self._check_seal(vault)
        return vault

    # Unlock methods.

    @staticmethod
    def _unlock_entries(vault):
        """The vault's unlock entries; a vault from before several methods existed has one, unlisted."""
        return vault["unlock"] if isinstance(vault["unlock"], list) else [vault["unlock"]]

    def _unwrap(self, vault):
        """The data key, from the first enrolled method that works here. Cancelling stops at once."""
        entries = {e.get("method"): e for e in self._unlock_entries(vault)}
        failure = None
        for u in self.unlocks:
            if u.method not in entries:
                continue
            if not u.available():
                failure = failure or Locked(f"the vault was not unlocked: {u.why_not}")
                continue
            try:
                return u.unwrap(entries[u.method], vault["id"])
            except Locked as e:
                if getattr(u, "method", "") == "passphrase":
                    raise
                failure = e  # e.g. the keyring lost its entry: another enrolled method may still work
        raise failure or Damaged("the vault uses an unlock method this version does not support")

    def methods(self):
        """The unlock methods enrolled in the vault, in stored order. Needs no unlock."""
        vault = self._load()
        return [e.get("method") for e in self._unlock_entries(vault)] if "unlock" in vault else []

    def enroll(self, unlock):
        """Add this unlock method to the vault, or replace its entry (a new passphrase). Items are not touched."""
        if not unlock.available():
            raise StoreError(f"cannot use the {unlock.method}: {unlock.why_not}")
        self._opened()  # unlock with a method already enrolled first
        with self._locked():
            vault = self._opened()
            entry = unlock.wrap(self._data_key, vault["id"])
            vault["unlock"] = [e for e in self._unlock_entries(vault) if e.get("method") != unlock.method] + [entry]
            self._save(vault)

    def withdraw(self, method):
        """Remove an unlock method from the vault. The last one cannot be removed."""
        self._opened()
        with self._locked():
            vault = self._opened()
            kept = [e for e in self._unlock_entries(vault) if e.get("method") != method]
            if len(kept) == len(self._unlock_entries(vault)):
                raise StoreError(f"the vault has no {method} to remove")
            if not kept:
                raise StoreError("that is the vault's only way to unlock; add another one first")
            vault["unlock"] = kept
            self._save(vault)
        for u in self.unlocks:
            if u.method == method:
                u.forget(vault["id"])

    @contextlib.contextmanager
    def _locked(self):
        """Hold the writers' lock on the vault. Readers need none: the file is only ever replaced whole."""
        os.makedirs(self.directory or ".", mode=0o700, exist_ok=True)
        if self._holding:  # flock is not re-entrant across file descriptors
            yield
            return
        fd = os.open(self.path + ".lock", os.O_WRONLY | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            self._holding = True
            yield
        finally:
            self._holding = False
            os.close(fd)

    @staticmethod
    def _entry(vault, name):
        return next((e for e in vault["items"] if e["name"] == name), None)

    @staticmethod
    def _bound(vault, entry):
        return bound("item", vault["id"], entry["name"], entry["type"], entry["updated"])

    @staticmethod
    def _manifest(vault):
        """What the seal covers: the identity, the version, the unlock entry and every item as stored."""
        items = [[e[k] for k in ("name", "type", "updated", "nonce", "ciphertext")] for e in vault["items"]]
        unlock = json.dumps(vault["unlock"], sort_keys=True, ensure_ascii=True)
        return bound("seal", vault["id"], vault["version"], unlock, items)

    def _seal(self, vault, key):
        AESGCM, _, _ = crypto()
        nonce = os.urandom(NONCE_BYTES)
        return {"nonce": b64(nonce), "tag": b64(AESGCM(key).encrypt(nonce, b"", self._manifest(vault)))}

    def _check_seal(self, vault):
        """The seal ties the item list and the version together, so no item can be removed, added or
        rolled back on its own, and the version cannot be raised without the data key."""
        AESGCM, _, InvalidTag = crypto()
        seal = vault.get("seal")
        if seal is None and vault.get("version", 0) == 0:
            return  # written before seals existed; the next save seals it
        try:
            AESGCM(self._data_key).decrypt(unb64(seal["nonce"], NONCE_BYTES), unb64(seal["tag"]), self._manifest(vault))
        except (InvalidTag, KeyError, TypeError):
            raise Damaged("the vault file was altered: its item list or version does not match its seal") from None

    def _parse(self, path):
        try:
            with open(path, encoding="utf8") as f:
                vault = json.load(f)
            text = all(isinstance(e[k], str) for e in vault["items"]
                       for k in ("name", "type", "updated", "nonce", "ciphertext"))
            names = [e["name"] for e in vault["items"]]
            version = vault.setdefault("version", 0)
            ok = vault["privfill_vault"] == FORMAT and isinstance(vault["id"], str) and text \
                and isinstance(vault["unlock"], (dict, list)) and len(set(names)) == len(names) \
                and all(isinstance(e, dict) for e in self._unlock_entries(vault)) and self._unlock_entries(vault) \
                and type(version) is int and version >= 0
        except (ValueError, KeyError, TypeError, AttributeError):
            ok = False
        except OSError:
            raise Damaged("the vault file cannot be read") from None
        if not ok:
            raise Damaged(NOT_A_VAULT)
        if not {e.get("method") for e in self._unlock_entries(vault)} & {u.method for u in self.unlocks}:
            raise Damaged("the vault uses an unlock method this version does not support")
        return vault

    def _load(self):
        """The parsed vault file, checked for shape and against rollback; an empty vault if there is no file."""
        if not os.path.exists(self.path):
            return {"items": []}
        vault = self._parse(self.path)
        behind = self.history and vault["version"] < self._seen(vault["id"])
        if behind and not self._holding:
            # A writer may have saved between the two reads: read both again while no writer can.
            with self._locked():
                vault = self._parse(self.path)
                behind = vault["version"] < self._seen(vault["id"])
        if behind:
            raise Damaged("the vault is older than the newest one saved on this machine; it may have been "
                          "replaced by an older copy. `privfill restore` lists the backups")
        return vault

    def _seen(self, vault_id):
        """The newest version of this vault saved on this machine, or 0."""
        try:
            with open(self.state_path, encoding="utf8") as f:
                seen = json.load(f).get(vault_id, 0)
            return seen if type(seen) is int else 0
        except (OSError, ValueError, AttributeError):
            return 0

    def _remember(self, vault):
        if not self.history:
            return
        try:
            with open(self.state_path, encoding="utf8") as f:
                state = json.load(f)
            state = state if isinstance(state, dict) else {}
        except (OSError, ValueError):
            state = {}
        state[vault["id"]] = max(vault["version"], state.get(vault["id"], 0) if type(state.get(vault["id"])) is int else 0)

        def w(tmp):
            with open(tmp, "w", encoding="utf8") as f:
                json.dump(state, f)

        atomic_write(self.state_path, w)
        os.chmod(self.state_path, 0o600)

    def _backup_files(self):
        """[(version, path)] of the kept backups, newest first."""
        try:
            names = os.listdir(self.backup_dir)
        except OSError:
            return []
        found = [(int(m.group(1)), os.path.join(self.backup_dir, n)) for n in names if (m := BACKUP_NAME.fullmatch(n))]
        return sorted(found, reverse=True)

    def _keep_backup(self):
        """Copy the file about to be replaced into the backups, and drop the oldest beyond the limit."""
        if not self.history or self.keep <= 0 or not os.path.exists(self.path):
            return
        try:
            version = self._parse(self.path)["version"]
        except Damaged:
            return  # a damaged file is not worth keeping as a backup
        os.makedirs(self.backup_dir, mode=0o700, exist_ok=True)
        target = os.path.join(self.backup_dir, f"vault-{version}.json")

        def w(tmp):
            shutil.copyfile(self.path, tmp)

        atomic_write(target, w)
        os.chmod(target, 0o600)
        for _, old in self._backup_files()[self.keep:]:
            os.unlink(old)

    def _clean_temporaries(self):
        """Remove temporary files a crashed writer left (they hold only ciphertext). Called under the lock."""
        prefix = temp_prefix(self.path)
        for n in os.listdir(self.directory or "."):
            if n.startswith(prefix):
                try:
                    os.unlink(os.path.join(self.directory or ".", n))
                except OSError:
                    pass

    @staticmethod
    def _dump(vault):
        return json.dumps(vault, ensure_ascii=False, indent=1) + "\n"

    def _save(self, vault):
        """Write the vault as its next version. Called under the lock, with the data key known."""
        self._clean_temporaries()
        vault["version"] = vault.get("version", 0) + 1
        vault["seal"] = self._seal(vault, self._data_key)
        self._keep_backup()

        def w(tmp):
            with open(tmp, "w", encoding="utf8") as f:
                f.write(self._dump(vault))

        atomic_write(self.path, w)
        os.chmod(self.path, 0o600)
        self._remember(vault)
