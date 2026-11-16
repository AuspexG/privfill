"""Where items live. Every read and write of an item goes through a Store.

A Store is anything with the five methods and the label below; the commands know nothing else
about storage. The only backend so far is FileStore, the plain-text data file.
"""
import os
import re

from .atomic import atomic_write
from .texts import HEADER_NAMES, ui

LEGACY_NAME = "个人信息.md"  # file name used by early versions
ROW = re.compile(r"^\|([^|]+)\|([^|]*)\|\s*$")


class Store:
    """The interface between the commands and wherever items are kept."""

    label = ""  # how the form names the store to the person
    directory = None  # a protected directory beside the items where the activity log may live, if any

    def create(self):
        """Make the store exist, empty, if it does not. Returns True if it was created."""
        raise NotImplementedError

    def names(self):
        """The names of all stored items, in stored order."""
        raise NotImplementedError

    def read(self, name):
        """The value of one item, or None if there is no such item."""
        raise NotImplementedError

    def write(self, name, value):
        """Store one item. Returns True if it replaced an existing entry, False if it added one."""
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


def default_store(program):
    """The data file, for the program started as the path `program`."""
    return FileStore(data_path(os.path.dirname(os.path.abspath(program))))


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

    def write(self, name, value):
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
