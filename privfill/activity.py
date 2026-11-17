"""A value-free, tamper-evident activity log.

One JSON record per line. Each record holds the command, item names with counts or statuses, the
SHA-256 of the master and the time; never a value and never a path. Each record carries the hash
of the one before it, so an edited, deleted or reordered record breaks the chain.
"""
import fcntl
import hashlib
import json
import os
import time

GENESIS = "0" * 64
MAX_BYTES = 1 << 20  # a log file is rotated once it reaches this size
KEEP = 5  # files kept in all: activity.log and activity.log.1 ... activity.log.4; older records are dropped


def seal(rec):
    """The hash that goes into a record: over the previous hash and the record's own fields."""
    body = json.dumps(rec, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf8")).hexdigest()


class ActivityLog:
    """The log at path. Rotation: a file that has reached max_bytes becomes path.1, path.1 becomes
    path.2, and so on; only keep files are kept, so the oldest records are dropped. The chain runs
    across the files, and verification starts from the oldest record that is still there."""

    def __init__(self, path, max_bytes=MAX_BYTES, keep=KEEP):
        self.path = path
        self.max_bytes = max_bytes
        self.keep = keep

    def _files(self):
        """Existing log files, oldest first."""
        names = [f"{self.path}.{n}" for n in range(self.keep - 1, 0, -1)] + [self.path]
        return [n for n in names if os.path.exists(n)]

    def _read(self, name):
        with open(name, encoding="utf8") as f:
            lines = f.read().splitlines()
        out = []
        for line in lines:
            try:
                rec = json.loads(line)
                if not isinstance(rec, dict) or not all(k in rec for k in ("hash", "prev", "seq")):
                    raise ValueError
            except ValueError:
                raise ValueError("a line is not a record")
            out.append(rec)
        return out

    def _records(self):
        """All records, oldest first. Raises ValueError on a line that is not a record."""
        return [r for name in self._files() for r in self._read(name)]

    def _last(self):
        """The newest record, read from the newest file that has one."""
        for name in reversed(self._files()):
            recs = self._read(name)
            if recs:
                return recs[-1]
        return None

    def _rotate(self):
        for n in range(self.keep - 1, 0, -1):
            src = self.path if n == 1 else f"{self.path}.{n - 1}"
            if os.path.exists(src):
                os.replace(src, f"{self.path}.{n}")

    def append(self, command, items=None, master=None):
        """Add one record, chained to the last."""
        d = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(d, mode=0o700, exist_ok=True)
        lock = os.open(self.path + ".lock", os.O_WRONLY | os.O_CREAT, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX)  # two commands at once must not fork the chain
            if os.path.exists(self.path) and os.path.getsize(self.path) >= self.max_bytes:
                self._rotate()
            last = self._last()
            rec = {
                "seq": last["seq"] + 1 if last else 1,
                "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "command": command,
                "items": items or {},
                "master": master,
                "prev": last["hash"] if last else GENESIS,
            }
            rec["hash"] = seal(rec)
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "w", encoding="utf8") as f:
                f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
                f.flush()
                os.fsync(f.fileno())
            return rec
        finally:
            os.close(lock)

    def verify(self):
        """(ok, number of records checked, message). The message names a record number, never content."""
        try:
            recs = self._records()
        except ValueError as e:
            return False, 0, f"the log is damaged: {e}"
        rotated = os.path.exists(f"{self.path}.1")
        # Once rotation has dropped records, the oldest one kept is the start; before that the log must begin at the beginning.
        prev, seq = (None, None) if rotated else (GENESIS, 0)
        for n, rec in enumerate(recs, 1):
            body = {k: v for k, v in rec.items() if k != "hash"}
            if seal(body) != rec["hash"] or (prev is not None and (rec["prev"] != prev or rec["seq"] != seq + 1)):
                return False, n - 1, f"the log was changed at or before record {n} (counting from the oldest kept)"
            prev, seq = rec["hash"], rec["seq"]
        return True, len(recs), f"the log is intact: {len(recs)} record(s)"
