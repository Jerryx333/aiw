"""Platform adapters and Windows dispatch boundary."""
import contextlib
import errno
import io
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import platform_lock
import platform_process


def platform_posix():
    import fcntl

    with patch.object(fcntl, "flock") as flock:
        platform_lock.acquire_lock(42)
        platform_lock.acquire_lock(42, blocking=False)
        platform_lock.release_lock(42)
        assert flock.call_args_list == [
            call(42, fcntl.LOCK_EX), call(42, fcntl.LOCK_EX | fcntl.LOCK_NB),
            call(42, fcntl.LOCK_UN),
        ]
        busy = BlockingIOError(errno.EAGAIN, "busy")
        flock.side_effect = busy
        try:
            platform_lock.acquire_lock(42, blocking=False)
        except BlockingIOError as error:
            assert error is busy
        else:
            raise AssertionError("POSIX contention must propagate")

    with patch.object(os, "killpg") as killpg:
        platform_process.kill_tree(42, signal.SIGTERM)
        platform_process.kill_tree(42, 0)
        assert killpg.call_args_list == [call(42, signal.SIGTERM), call(42, 0)]

    preexec = Mock()
    assert platform_process.popen_kwargs() == {"start_new_session": True}
    assert platform_process.popen_kwargs(preexec_fn=preexec) == {
        "start_new_session": True, "preexec_fn": preexec,
    }


def platform_windows(root):
    import ai_worker
    import background

    crt = SimpleNamespace(LK_NBLCK=2, LK_UNLCK=0, locking=Mock())
    with patch.object(sys, "platform", "win32"), patch.dict(sys.modules, {"msvcrt": crt}):
        with (root / "platform.lock").open("w+b", buffering=0) as lock:
            lock.write(b"abc")
            fd = lock.fileno()
            def verify_offset(*_):
                assert os.lseek(fd, 0, os.SEEK_CUR) == 0

            crt.locking.side_effect = verify_offset
            platform_lock.acquire_lock(lock, blocking=False)
            platform_lock.release_lock(fd)
            assert lock.tell() == 3
            assert crt.locking.call_args_list == [call(fd, 2, 1), call(fd, 0, 1)]
            crt.locking.side_effect = OSError(errno.EACCES, "locked")
            try:
                platform_lock.acquire_lock(fd, blocking=False)
            except BlockingIOError:
                assert lock.tell() == 3
            else:
                raise AssertionError("expected lock contention")
            crt.locking.side_effect = [OSError(errno.EACCES, "locked"), None]
            with patch.object(platform_lock.time, "sleep") as sleep:
                platform_lock.acquire_lock(fd)
                sleep.assert_called_once()

            failure = OSError(errno.EIO, "I/O error")
            crt.locking.side_effect = failure
            try:
                platform_lock.acquire_lock(fd, blocking=False)
            except OSError as error:
                assert error is failure
            else:
                raise AssertionError("expected the original I/O error")
            assert lock.tell() == 3

            denied = PermissionError(errno.EACCES, "access denied")
            denied.winerror = 5
            crt.locking.side_effect = denied
            try:
                platform_lock.acquire_lock(fd, blocking=False)
            except PermissionError as error:
                assert error is denied
            else:
                raise AssertionError("expected access denied")

        with patch.object(subprocess, "CREATE_NEW_PROCESS_GROUP", 512, create=True):
            assert platform_process.popen_kwargs(preexec_fn=Mock()) == {"creationflags": 512}
        with patch.object(subprocess, "run") as run:
            platform_process.kill_tree(42, signal.SIGTERM)
            run.assert_called_once_with(["taskkill", "/PID", "42", "/T", "/F"], check=True)

        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), patch.object(sys, "argv", ["aiw", "review"]):
            assert ai_worker.main() == 64
            assert ai_worker.dispatch(None, {}) == 64
            assert background.start(["review"]) == 64
        assert stderr.getvalue().count("Windows 暂不支持派单") == 3
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout), patch.object(sys, "argv", ["aiw", "--help"]):
            assert ai_worker.main() == 0
        assert "aiw <seat>" in stdout.getvalue()


def platform_windows_imports():
    repo = Path(__file__).resolve().parents[2]
    # Load host stdlib first, then hide POSIX-only APIs from project modules.
    script = """
import importlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest.mock

sys.platform = 'win32'
sys.modules['fcntl'] = None
for name in ('O_DIRECTORY', 'O_NOFOLLOW'):
    if hasattr(os, name):
        delattr(os, name)
for module in sorted(Path.cwd().glob('*.py')):
    importlib.import_module(module.stem)
"""
    subprocess.run([sys.executable, "-c", script], cwd=repo, check=True)


def platform_compat():
    platform_posix()
    with tempfile.TemporaryDirectory() as tmp:
        platform_windows(Path(tmp))
    platform_windows_imports()


CASES = {"platform_compat": platform_compat}
