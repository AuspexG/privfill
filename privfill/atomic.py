"""Write or replace a file in one step, so a failure or a crash never leaves it half written."""
import os
import shutil
import tempfile


def temp_prefix(path):
    """The name prefix of the temporary files atomic_write makes for path."""
    return "." + os.path.basename(path) + ".tmp-"


def fsync_dir(d):
    """Make a rename in directory d durable. Some file systems cannot sync a directory; that is not an error."""
    try:
        fd = os.open(d, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def atomic_write(path, writer):
    """writer(tmp) writes the new content to tmp; it is flushed to disk and renamed over path.

    A crash at any point leaves either the old file or the new one, never a mix. A temporary file
    left behind by a crash starts with temp_prefix(path).
    """
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=d, prefix=temp_prefix(path))
    os.close(fd)
    try:
        writer(tmp)
        if os.path.exists(path):
            shutil.copymode(path, tmp)  # a new file stays private (mode 600)
        with open(tmp, "rb+") as f:
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    fsync_dir(d)
