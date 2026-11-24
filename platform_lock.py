"""Exclusive file locks for POSIX and Windows."""
import errno
import os
import sys
import time


def acquire_lock(file, *, blocking=True):
    if sys.platform != "win32":
        import fcntl

        flags = fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB
        return fcntl.flock(file, flags)

    import msvcrt

    while True:
        try:
            return _lock_byte(file, msvcrt.LK_NBLCK)
        except BlockingIOError:
            if not blocking:
                raise
        time.sleep(0.05)


def release_lock(file):
    if sys.platform != "win32":
        import fcntl

        return fcntl.flock(file, fcntl.LOCK_UN)

    import msvcrt

    return _lock_byte(file, msvcrt.LK_UNLCK)


def _lock_byte(file, mode):
    import msvcrt

    fd = file if isinstance(file, int) else file.fileno()
    offset = os.lseek(fd, 0, os.SEEK_CUR)
    os.lseek(fd, 0, os.SEEK_SET)
    try:
        # Windows permits locking a range beyond EOF, including empty files.
        return msvcrt.locking(fd, mode, 1)
    except OSError as error:
        # CRT contention is EACCES. Preserve other errors, including
        # access denied when the native error code is available.
        if (mode == msvcrt.LK_NBLCK and error.errno == errno.EACCES
                and getattr(error, "winerror", None) in (None, 33)):
            raise BlockingIOError(error.errno, error.strerror) from error
        raise
    finally:
        os.lseek(fd, offset, os.SEEK_SET)
