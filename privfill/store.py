"""Where items live. Every read and write of an item goes through a Store.

A Store is anything with the five methods and the label below; the commands know nothing else
about storage. The commands use the encrypted vault (VaultStore, in vault.py). FileStore is the
plain-text data file of earlier versions, kept so that its items can be moved into the vault.
"""
import os
import re

from .atomic import atomic_write
from .texts import HEADER_NAMES, ui

LEGACY_NAME = "个人信息.md"  # file name used by early versions
ROW = re.compile(r"^\|([^|]+)\|([^|]*)\|\s*$")


class StoreError(Exception):
    """A store could not do what was asked. The message is for the caller and never holds a value."""

    code = 1  # the exit code of a command that stops on this error


class Cancelled(StoreError):
    """The person closed the unlock dialog."""

    code = 3


class Locked(StoreError):
    """The store could not be unlocked: the unlock secret was wrong."""

    code = 5


class Damaged(StoreError):
    """The store is unreadable, or something in it was altered after it was written."""


class MissingLibrary(StoreError):
    """A library the store depends on is not installed."""


class Store:
    """The interface between the commands and wherever items are kept."""

    label = ""  # how the form names the store to the person
    directory = None  # the protected directory the store keeps its data in, if it has one; the activity log and the output registry live there

    def create(self):
        """Make the store exist, empty, if it does not. Returns True if it was created."""
        raise NotImplementedError

    def names(self):
        """The names of all stored items, in stored order."""
        raise NotImplementedError

    def entries(self):
        """(name, entry type) of every item, in stored order. A store that keeps no types reports "text"."""
        return [(n, "text") for n in self.names()]

    def read(self, name):
        """The value of one item, or None if there is no such item."""
        raise NotImplementedError

    def write(self, name, value, type="text"):
        """Store one item of the given entry type. Returns True if it replaced an existing entry, False if it added one."""
        raise NotImplementedError

    def delete(self, name):
        """Remove one item. Returns True if it was there."""
        raise NotImplementedError


def data_path(program_dir):
    """$PRIVFILL_DATA, else a data file next to the program (early installs), else ~/.privfill/profile.md."""
    env = os.environ.get("PRIVFILL_DATA")
    if env:
        return os.path.abspath(os.path.expanduser(env))
    for name in ("profile.md", LEGACY_NAME):
        if os.path.exists(os.path.join(program_dir, name)):
            return os.path.join(program_dir, name)
    return os.path.join(os.path.expanduser("~"), ".privfill", "profile.md")


def vault_path():
    """$PRIVFILL_VAULT, else ~/.privfill/vault.json."""
    env = os.environ.get("PRIVFILL_VAULT")
    if env:
        return os.path.abspath(os.path.expanduser(env))
    return os.path.join(os.path.expanduser("~"), ".privfill", "vault.json")


def default_store():
    """The vault, unlocked by a passphrase the person types into a local dialog."""
    from .form import PASSPHRASE_TEST, ask_passphrase
    from .vault import TEST_COST, PassphraseUnlock, VaultStore

    # A passphrase taken from the environment protects nothing, so such a vault skips the slow derivation.
    cost = {"cost": TEST_COST} if os.environ.get(PASSPHRASE_TEST) is not None else {}
    return VaultStore(vault_path(), PassphraseUnlock(ask_passphrase, **cost))


class FileStore(Store):
    """Items as rows of a Markdown table in one plain-text file (mode 600, directory 700)."""

    def __init__(self, path):
        self.path = path
        self.label = os.path.basename(path)
        self.directory = os.path.dirname(path)

    def create(self):
        created = self._ensure()
        os.chmod(self.path, 0o600)
        return created

    def names(self):
        return list(self._load())

    def read(self, name):
        return self._load().get(name)

    def write(self, name, value, type="text"):  # the data file has no place for the type
        self._ensure()
        lines = self._lines()
        row = f"| {name} | {value} |"
        hit, last = None, None
        for i, line in enumerate(lines):
            m = ROW.match(line)
            if m:
                last = i
                if m.group(1).strip() == name:
                    hit = i
        if hit is not None:
            lines[hit] = row
        elif last is not None:
            lines.insert(last + 1, row)
        else:
            lines += ui("file_head").rstrip("\n").split("\n")[-2:] + [row]
        self._replace(lines)
        return hit is not None

    def delete(self, name):
        if not os.path.exists(self.path):
            return False
        lines = self._lines()
        kept = [line for line in lines if not self._is_row_of(line, name)]
        if len(kept) == len(lines):
            return False
        self._replace(kept)
        return True

    @staticmethod
    def _is_row_of(line, name):
        m = ROW.match(line)
        return bool(m) and m.group(1).strip() == name

    def _ensure(self):
        if os.path.exists(self.path):
            return False
        os.makedirs(os.path.dirname(self.path), mode=0o700, exist_ok=True)
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf8") as f:
            f.write(ui("file_head"))
        return True

    def _lines(self):
        with open(self.path, encoding="utf8") as f:
            return f.read().split("\n")

    def _load(self):
        data = {}
        if not os.path.exists(self.path):
            return data
        for line in self._lines():
            m = ROW.match(line)
            if not m:
                continue
            k, v = m.group(1).strip(), m.group(2).strip()
            if not k or k in HEADER_NAMES or set(k) <= set("-: "):
                continue
            if v:
                data[k] = v
        return data

    def _replace(self, lines):
        def w(tmp):
            with open(tmp, "w", encoding="utf8") as f:
                f.write("\n".join(lines))

        atomic_write(self.path, w)
        os.chmod(self.path, 0o600)
