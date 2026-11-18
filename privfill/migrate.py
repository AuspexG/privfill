"""Move the items of a plain-text data file from an earlier version into the vault.

Reads every item of the old file, writes those the vault does not have, then reads every item back
from the vault file and compares it with the old file, locally. Only counts are printed. The old
file is left as it is unless every item matched and the person agrees, in a local dialog, to its
removal. Running it again changes nothing.
"""
import hashlib
import os

from .store import LEGACY_NAME, FileStore, data_path

OLD_NAMES = ("profile.md", LEGACY_NAME)
WIPE_LIMITS = ("Overwriting a file before deleting it does not reliably erase it on journaling or "
               "copy-on-write file systems (ext4 with data journaling, btrfs, ZFS) or on SSDs, and does "
               "nothing for backups, snapshots or synced copies. Full-disk encryption is what protects "
               "data that may remain.")


TEST_HOOKS = ("PRIVFILL_PASSPHRASE_TEST", "PRIVFILL_FORM_TEST", "PRIVFILL_KEYRING_TEST", "PRIVFILL_CONFIRM_TEST")


def real_privfill_dir():
    """~/.privfill of the account itself, from the user database, so a changed HOME cannot hide it."""
    import pwd

    return os.path.realpath(os.path.join(pwd.getpwuid(os.getuid()).pw_dir, ".privfill"))


def off_limits(path):
    """Whether a test run must not touch this file: the person's own data directory, under any test hook."""
    if not any(os.environ.get(h) is not None for h in TEST_HOOKS):
        return False
    return os.path.realpath(path).startswith(real_privfill_dir() + os.sep)


def find_old_file(program_dir):
    """The plain-text data file of an earlier version, or None."""
    candidates = [data_path(program_dir)]
    home = os.path.join(os.path.expanduser("~"), ".privfill")
    candidates += [os.path.join(d, n) for d in (program_dir, home) for n in OLD_NAMES]
    return next((c for c in candidates if os.path.isfile(c) and not off_limits(c)), None)


def digest(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def copies_of(path, program_dir):
    """Other files that look like copies of the old data file: same content, or its name with a suffix,
    in the directories privfill has used. Paths only; they hold no value of their own."""
    want = digest(path)
    dirs = {os.path.dirname(os.path.abspath(path)), program_dir,
            os.path.join(os.path.expanduser("~"), ".privfill"),
            os.path.join(os.path.expanduser("~"), ".claude", "skills", "privfill")}
    found = []
    for d in sorted(dirs):
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for n in names:
            p = os.path.join(d, n)
            if os.path.abspath(p) == os.path.abspath(path) or not os.path.isfile(p) or off_limits(p):
                continue
            named = any(n.startswith(o) for o in OLD_NAMES)
            try:
                same = os.path.getsize(p) == os.path.getsize(path) and digest(p) == want
            except OSError:
                same = False
            if named or same:
                found.append(p)
    return found


def wipe(path):
    """Overwrite the file with random bytes, flush, and delete it. Best effort; see WIPE_LIMITS."""
    size = os.path.getsize(path)
    with open(path, "r+b") as f:
        f.write(os.urandom(size))
        f.flush()
        os.fsync(f.fileno())
    os.unlink(path)


def migrate(old_path, vault):
    """Copy the old file's items into the vault and verify them. Returns a dict of counts and names:
    added, present (already there with the same value), conflicts (already there with another value),
    mismatched (did not read back the same). Values never leave this function."""
    old = FileStore(old_path)
    names = old.names()
    added, present, conflicts = [], [], []
    for n in names:
        value = old.read(n)
        have = vault.read(n)
        if have is None:
            vault.write(n, value, "text")
            added.append(n)
        elif have == value:
            present.append(n)
        else:
            conflicts.append(n)
    # Read back from the file as written, not from anything held in memory.
    mismatched = [n for n in names if n not in conflicts and vault.read(n) != old.read(n)]
    return {"total": len(names), "added": added, "present": present, "conflicts": conflicts, "mismatched": mismatched}
