"""Bundle files into one zip archive, inside this process: no other program ever receives a path."""
import os
import zipfile

from .atomic import atomic_write


class PackError(Exception):
    """The files cannot be packed. The message refers to files by index, never by name."""


def pack(archive, files):
    """Write a zip at archive holding each of files under its base name; the files are only read.

    The archive appears complete or not at all, and an archive already at that path is replaced.
    """
    names = {}
    for i, path in enumerate(files, 1):
        if not os.path.isfile(path):
            raise PackError(f"file {i} does not exist or is not a regular file")
        if os.path.realpath(path) == os.path.realpath(archive):
            raise PackError(f"file {i} is the archive itself")
        name = os.path.basename(path)
        if name in names:
            raise PackError(f"files {names[name]} and {i} have the same name")
        names[name] = i

    def w(tmp):
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            for path in files:
                z.write(path, os.path.basename(path))

    atomic_write(archive, w)
