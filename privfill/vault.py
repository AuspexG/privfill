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
import json
import os
import time

from .atomic import atomic_write
from .store import Cancelled, Damaged, Locked, MissingLibrary, Store

FORMAT = 1
KEY_BYTES = 32
NONCE_BYTES = 12
COST = 2 ** 17  # scrypt N with r=8: 128 MiB of memory per passphrase guess
TEST_COST = 2 ** 10  # for vaults whose passphrase comes from a test hook
MAX_COST = 2 ** 20  # a vault file may not ask for more than 1 GiB
ATTEMPTS = 3  # passphrase tries per run
NOT_A_VAULT = "the vault file is not in the vault format"


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
    """Items in an encrypted vault file. Unlocks at most once, and only when a value is read or written."""

    def __init__(self, path, unlock):
        self.path = path
        self.label = os.path.basename(path)
        self.unlock = unlock
        self._data_key = None

    def create(self):
        if os.path.exists(self.path):
            return False
        vault_id = os.urandom(16).hex()
        key = os.urandom(KEY_BYTES)
        vault = {"privfill_vault": FORMAT, "id": vault_id, "unlock": self.unlock.wrap(key, vault_id), "items": []}
        os.makedirs(os.path.dirname(self.path), mode=0o700, exist_ok=True)
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf8") as f:
            f.write(self._dump(vault))
        self._data_key = key
        return True

    def names(self):
        return [e["name"] for e in self._load()["items"]]

    def read(self, name):
        vault = self._load()
        entry = self._entry(vault, name)
        if entry is None:
            return None
        AESGCM, _, InvalidTag = crypto()
        key = self._key(vault)
        try:
            return AESGCM(key).decrypt(unb64(entry["nonce"], NONCE_BYTES), unb64(entry["ciphertext"]),
                                       self._bound(vault, entry)).decode("utf8")
        except InvalidTag:
            raise Damaged(f"the item '{name}' cannot be decrypted: the vault file was altered or is damaged") from None

    def write(self, name, value, type="text"):
        self.create()
        AESGCM, _, _ = crypto()
        vault = self._load()
        key = self._key(vault)
        entry = {"name": name, "type": type, "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        nonce = os.urandom(NONCE_BYTES)
        entry.update(nonce=b64(nonce),
                     ciphertext=b64(AESGCM(key).encrypt(nonce, value.encode("utf8"), self._bound(vault, entry))))
        old = self._entry(vault, name)
        if old is None:
            vault["items"].append(entry)
        else:
            vault["items"][vault["items"].index(old)] = entry
        self._save(vault)
        return old is not None

    def delete(self, name):
        vault = self._load()
        entry = self._entry(vault, name)
        if entry is None:
            return False
        vault["items"].remove(entry)
        self._save(vault)
        return True

    def _key(self, vault):
        if self._data_key is None:
            self._data_key = self.unlock.unwrap(vault["unlock"], vault["id"])
        return self._data_key

    @staticmethod
    def _entry(vault, name):
        return next((e for e in vault["items"] if e["name"] == name), None)

    @staticmethod
    def _bound(vault, entry):
        return bound("item", vault["id"], entry["name"], entry["type"], entry["updated"])

    def _load(self):
        """The parsed vault file, checked for shape; an empty vault if there is no file."""
        if not os.path.exists(self.path):
            return {"items": []}
        try:
            with open(self.path, encoding="utf8") as f:
                vault = json.load(f)
            text = all(isinstance(e[k], str) for e in vault["items"]
                       for k in ("name", "type", "updated", "nonce", "ciphertext"))
            names = [e["name"] for e in vault["items"]]
            ok = vault["privfill_vault"] == FORMAT and isinstance(vault["id"], str) and text \
                and isinstance(vault["unlock"], dict) and len(set(names)) == len(names)
        except (ValueError, KeyError, TypeError):
            ok = False
        if not ok:
            raise Damaged(NOT_A_VAULT)
        if vault["unlock"].get("method") != self.unlock.method:
            raise Damaged("the vault uses an unlock method this version does not support")
        return vault

    @staticmethod
    def _dump(vault):
        return json.dumps(vault, ensure_ascii=False, indent=1) + "\n"

    def _save(self, vault):
        def w(tmp):
            with open(tmp, "w", encoding="utf8") as f:
                f.write(self._dump(vault))

        atomic_write(self.path, w)
        os.chmod(self.path, 0o600)
