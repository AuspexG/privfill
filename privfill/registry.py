"""The output registry: which files this program filled with real values.

One JSON file in the protected data directory (mode 600, directory 700):

    {"privfill_outputs": 1,
     "outputs": [{"path": "<real path>", "sha256": "<content hash>", "size": <bytes>}, ...]}

It holds no value, but a path may contain one (an output can be named after an item), so the
registry is never written anywhere else and no command prints from it. The guard reads it to
recognise an output by its path, or by its content hash after it was moved or renamed.
"""
import hashlib
import json
import os

from .atomic import atomic_write

FORMAT = 1
FILE_NAME = "outputs.json"


class RegistryError(Exception):
    """The registry file is unreadable or not in the registry format."""


def content_hash(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


class Registry:
    def __init__(self, path):
        self.path = path

    def outputs(self):
        """Every recorded output as {"path", "sha256", "size"}; none if there is no registry yet."""
        if not os.path.exists(self.path):
            return []
        try:
            with open(self.path, encoding="utf8") as f:
                data = json.load(f)
            outputs = data["outputs"]
            ok = data["privfill_outputs"] == FORMAT and all(
                isinstance(o["path"], str) and isinstance(o["sha256"], str) and isinstance(o["size"], int)
                for o in outputs)
        except (OSError, ValueError, KeyError, TypeError):
            ok = False
        if not ok:
            raise RegistryError("the output registry is not in the registry format")
        return outputs

    def record(self, output):
        """Remember the file at this path, as it is now, as an output."""
        entry = {"path": os.path.realpath(output), "sha256": content_hash(output), "size": os.path.getsize(output)}
        outputs = self.outputs()
        if entry in outputs:
            return
        text = json.dumps({"privfill_outputs": FORMAT, "outputs": outputs + [entry]}, ensure_ascii=False, indent=1) + "\n"

        def w(tmp):
            with open(tmp, "w", encoding="utf8") as f:
                f.write(text)

        os.makedirs(os.path.dirname(self.path), mode=0o700, exist_ok=True)
        atomic_write(self.path, w)
        os.chmod(self.path, 0o600)
