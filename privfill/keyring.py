"""The desktop keyring as a way to unlock the vault, so the person types nothing.

A random 256-bit unlock secret is kept in the desktop's Secret Service (GNOME Keyring, KWallet
and others on Linux), under the attributes {"application": "privfill", "vault": <vault id>}. It
wraps the vault's data key the same way a passphrase-derived key does; the vault file holds only
the wrapped key. The secret is never printed, logged or written anywhere else.

The real service is reached through the `secretstorage` library. Tests use a fake service, chosen
by the environment variable PRIVFILL_KEYRING_TEST (see fake_service); with any other test hook set
and no fake, the keyring counts as unavailable, so a test can never touch the person's keyring.
"""
import base64
import json
import os

from .store import Damaged, Locked
from .vault import KEY_BYTES, NONCE_BYTES, Unlock, b64, bound, crypto, unb64

KEYRING_TEST = "PRIVFILL_KEYRING_TEST"  # test hook: a JSON file standing in for the service, or "none"
LABEL = "privfill vault unlock key"


class KeyringMissing(Locked):
    """The keyring is reachable but holds no secret for this vault (or a wrong one)."""


class Service:
    """A place to keep one secret per vault. available() says whether it can be reached at all."""

    why_not = ""  # when not available: why, for the person (never holds a secret)

    def available(self):
        raise NotImplementedError

    def get(self, vault_id):
        """The stored secret (bytes) or None."""
        raise NotImplementedError

    def put(self, vault_id, secret):
        raise NotImplementedError

    def remove(self, vault_id):
        raise NotImplementedError


def attributes(vault_id):
    return {"application": "privfill", "vault": vault_id}


class SecretService(Service):
    """The desktop Secret Service over D-Bus, through `secretstorage`."""

    def __init__(self):
        self._collection = None

    def _open(self):
        if self._collection is None:
            import secretstorage  # noqa: PLC0415 - optional dependency
            conn = secretstorage.dbus_init()
            collection = secretstorage.get_default_collection(conn)
            if collection.is_locked():
                collection.unlock()  # the desktop asks the person, if it must
                if collection.is_locked():
                    raise Locked("the desktop keyring stayed locked")
            self._collection = collection
        return self._collection

    def available(self):
        try:
            import secretstorage  # noqa: F401, PLC0415
        except ImportError:
            self.why_not = ("the Python library `secretstorage` is not installed (`python-secretstorage` on Arch, "
                            "`python3-secretstorage` on Debian, Ubuntu and Fedora)")
            return False
        try:
            self._open()
        except Exception as e:  # no D-Bus session, no service running, or it refused
            self.why_not = f"no desktop keyring could be reached ({type(e).__name__})"
            return False
        return True

    def _items(self, vault_id):
        return list(self._open().search_items(attributes(vault_id)))

    def get(self, vault_id):
        for item in self._items(vault_id):
            if item.is_locked():
                item.unlock()
            return base64.b64decode(item.get_secret())
        return None

    def put(self, vault_id, secret):
        self._open().create_item(LABEL, attributes(vault_id), base64.b64encode(secret), replace=True)

    def remove(self, vault_id):
        for item in self._items(vault_id):
            item.delete()


class FakeService(Service):
    """A JSON file standing in for the keyring. For tests with fake data only."""

    def __init__(self, path):
        self.path = path

    def available(self):
        if self.path == "none":
            self.why_not = "no desktop keyring could be reached (test)"
            return False
        return True

    def _all(self):
        try:
            with open(self.path, encoding="utf8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _put_all(self, data):
        with open(self.path, "w", encoding="utf8") as f:
            json.dump(data, f)

    def get(self, vault_id):
        found = self._all().get(vault_id)
        return base64.b64decode(found) if found else None

    def put(self, vault_id, secret):
        self._put_all(dict(self._all(), **{vault_id: base64.b64encode(secret).decode("ascii")}))

    def remove(self, vault_id):
        data = self._all()
        data.pop(vault_id, None)
        self._put_all(data)


def default_service(testing):
    """The fake service if the test hook names one; none at all under any other test hook; else the real one."""
    fake = os.environ.get(KEYRING_TEST)
    if fake is not None:
        vault = os.environ.get("PRIVFILL_VAULT")
        home = os.path.realpath(os.path.join(os.path.expanduser("~"), ".privfill")) + os.sep
        if not vault or os.path.realpath(os.path.expanduser(vault)).startswith(home):
            raise RuntimeError(f"{KEYRING_TEST} requires PRIVFILL_VAULT to name a vault outside ~/.privfill")
        return FakeService(fake)
    if testing:
        return FakeService("none")
    return SecretService()


class KeyringUnlock(Unlock):
    """The data key wrapped by a random secret kept in the desktop keyring."""

    method = "keyring"

    def __init__(self, service):
        self.service = service

    def available(self):
        return self.service.available()

    @property
    def why_not(self):
        return self.service.why_not

    def wrap(self, key, vault_id):
        AESGCM, _, _ = crypto()
        secret = self.service.get(vault_id) or os.urandom(KEY_BYTES)
        if len(secret) != KEY_BYTES:
            secret = os.urandom(KEY_BYTES)
        self.service.put(vault_id, secret)
        nonce = os.urandom(NONCE_BYTES)
        wrapped = AESGCM(secret).encrypt(nonce, key, self._bound(vault_id))
        return {"method": self.method, "nonce": b64(nonce), "wrapped_key": b64(wrapped)}

    def unwrap(self, entry, vault_id):
        AESGCM, _, InvalidTag = crypto()
        try:
            nonce, wrapped = unb64(entry["nonce"], NONCE_BYTES), unb64(entry["wrapped_key"])
        except KeyError:
            raise Damaged("the vault file is not in the vault format") from None
        secret = self.service.get(vault_id)
        if not secret or len(secret) != KEY_BYTES:
            raise KeyringMissing("the desktop keyring holds no key for this vault")
        try:
            return AESGCM(secret).decrypt(nonce, wrapped, self._bound(vault_id))
        except InvalidTag:
            raise KeyringMissing("the key in the desktop keyring does not open this vault") from None

    def forget(self, vault_id):
        """Remove the secret from the keyring, after the vault stopped using it."""
        self.service.remove(vault_id)

    def _bound(self, vault_id):
        return bound("key", vault_id, self.method)
