"""What the local manager window does, without the window: browse, reveal, edit, delete, back up, restore.

Every call goes through the Store, and edits go through the same normalization as the entry form.
Nothing here prints; errors are raised for the window to show the person.
"""
import os

from .normalize import Bad, normalize
from .store import StoreError
from .texts import ui
from .vault import VaultStore


class Manager:
    """backup_unlock() returns a fresh Unlock method (a passphrase the person chooses) for a backup file."""

    def __init__(self, store, backup_unlock):
        self.store = store
        self.backup_unlock = backup_unlock

    def items(self):
        """[(name, type)] without unlocking anything."""
        return self.store.entries()

    def reveal(self, name):
        value = self.store.read(name)
        if value is None:
            raise StoreError("there is no such item")
        return value

    def edit(self, name, raw):
        """Store the normalized form of raw as the item's new value, keeping its type. Raises Bad if it cannot be read."""
        kinds = dict(self.items())
        if name not in kinds:
            raise StoreError("there is no such item")
        if not raw.strip():
            raise Bad(ui("bad_required"))
        kind = kinds[name]
        # The list of choices and any pattern belong to the form that asked, and are not stored: a choice is checked as text.
        value = normalize({"type": "text" if kind == "choice" else kind}, raw.strip())
        self.store.write(name, value, kind)
        return value

    def delete(self, name):
        if not self.store.delete(name):
            raise StoreError("there is no such item")

    def export(self, path):
        """Write every item to a new backup file, encrypted under a passphrase chosen for it. Never overwrites."""
        if os.path.exists(path):
            raise StoreError("that file already exists; choose a new name")
        values = [(n, t, self.reveal(n)) for n, t in self.items()]
        part = path + ".part"
        try:
            backup = VaultStore(part, self.backup_unlock())
            backup.create()
            for n, t, v in values:
                backup.write(n, v, t)
            os.rename(part, path)
        finally:
            if os.path.exists(part):
                os.remove(part)
        return len(values)

    def import_(self, path, confirm_replace):
        """Restore a backup. confirm_replace(names) says whether items that already exist may be replaced.

        Returns (added, replaced). Nothing is written unless the backup could be read in full.
        """
        if not os.path.isfile(path):
            raise StoreError("there is no such backup file")
        backup = VaultStore(path, self.backup_unlock())
        wanted = [(n, t, backup.read(n)) for n, t in backup.entries()]
        have = set(self.store.names())
        clash = [n for n, _, _ in wanted if n in have]
        replace = bool(clash) and confirm_replace(clash)
        added = replaced = 0
        for n, t, v in wanted:
            if n in have and not replace:
                continue
            self.store.write(n, v, t)
            if n in have:
                replaced += 1
            else:
                added += 1
        return added, replaced
