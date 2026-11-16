"""Make the running process hard to inspect: it holds personal details in memory.

Best effort by design. Where a step is not available (no `resource` module on Windows, no prctl
outside Linux, a kernel that refuses) it is skipped silently and the program carries on: these are
extra barriers, and the program's guarantee about its output does not rest on them.
"""
import sys

PR_SET_DUMPABLE = 4


def harden():
    """Disable core dumps and, on Linux, mark the process non-dumpable.

    Non-dumpable also stops other processes of the same user from attaching a debugger or
    reading /proc/<pid>/mem. Both settings are reset by the kernel on exec, so every start of
    the program (including the restart under the system Python for the form) applies them again.
    """
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    except Exception:  # no such module or limit on this platform
        pass
    if sys.platform.startswith("linux"):
        try:
            import ctypes
            ctypes.CDLL(None).prctl(PR_SET_DUMPABLE, 0, 0, 0, 0)
        except Exception:
            pass
