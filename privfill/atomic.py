"""Write or replace a file in one step, so a failure never leaves it half written."""
import os
import shutil
import tempfile


def atomic_write(path, writer):
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".privfill-")
    os.close(fd)
    try:
        writer(tmp)
        if os.path.exists(path):
            shutil.copymode(path, tmp)  # a new file stays private (mode 600)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
