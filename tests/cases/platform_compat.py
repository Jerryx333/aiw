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
        with patch.object(subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run:
            platform_process.kill_tree(42, signal.SIGTERM)
            run.assert_called_once_with(["taskkill", "/PID", "42", "/T", "/F"],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # taskkill 128: the tree is already gone, the same as POSIX ESRCH.
        with patch.object(subprocess, "run", return_value=subprocess.CompletedProcess([], 128)):
            try:
                platform_process.kill_tree(42, signal.SIGTERM)
            except ProcessLookupError:
                pass
            else:
                raise AssertionError("a vanished tree must raise ProcessLookupError")
        # Any other failure reads as killpg would: gone if the leader is gone, otherwise denied.
        for alive, expected in ((False, ProcessLookupError), (True, PermissionError)):
            with patch.object(subprocess, "run", return_value=subprocess.CompletedProcess([], 1)), \
                    patch.object(platform_process, "_windows_alive", return_value=alive):
                try:
                    platform_process.kill_tree(42, signal.SIGTERM)
                except expected:
                    pass
                else:
                    raise AssertionError(f"taskkill 1 with alive={alive} must raise {expected.__name__}")
        # Signal 0 only asks; it must never run taskkill.
        with patch.object(subprocess, "run") as run, patch.object(platform_process, "_windows_alive", side_effect=[True, False]):
            platform_process.kill_tree(42, 0)
            try:
                platform_process.kill_tree(42, 0)
            except ProcessLookupError:
                pass
            else:
                raise AssertionError("a dead group leader must raise ProcessLookupError")
            run.assert_not_called()
        with patch.object(platform_process.signal, "pthread_sigmask", create=True) as mask:
            with platform_process.signals_blocked():
                pass
            mask.assert_not_called()

        # Scratch cleanup without dir_fd: keeps deliver/, removes the rest, never follows a link out.
        scratch, outside = root / "scratch", root / "outside"
        (scratch / "deliver").mkdir(parents=True)
        (scratch / "deliver" / "keep.txt").write_text("keep")
        (scratch / "sub").mkdir()
        (scratch / "sub" / "f.txt").write_text("x")
        (scratch / "tmp.txt").write_text("x")
        outside.mkdir()
        (outside / "target.txt").write_text("target")
        (scratch / "link").symlink_to(outside, target_is_directory=True)
        ai_worker.cleanup_scratch(scratch)
        assert sorted(p.name for p in scratch.iterdir()) == ["deliver"]
        assert (scratch / "deliver" / "keep.txt").exists() and (outside / "target.txt").exists()
        # A scratch that is a junction (any reparse point; Path.is_junction is 3.12+) is left alone.
        (scratch / "x.txt").write_text("x")
        with patch.object(ai_worker, "_windows_link", side_effect=lambda path: Path(path) == scratch):
            ai_worker.cleanup_scratch(scratch)
        assert (scratch / "x.txt").exists()
        import stat as stat_module, types
        reparse = types.SimpleNamespace(st_file_attributes=stat_module.FILE_ATTRIBUTE_REPARSE_POINT)
        with patch.object(ai_worker.os, "lstat", return_value=reparse):
            assert ai_worker._windows_link("junction")
        with patch.object(ai_worker.os, "lstat", return_value=types.SimpleNamespace(st_file_attributes=0)):
            assert not ai_worker._windows_link("folder")
        (scratch / "x.txt").unlink()
        environment = ai_worker.scratch_environment(scratch)
        assert environment["TEMP"] == environment["TMP"] == environment["TMPDIR"] == str(scratch)

        # Dispatch runs on Windows; background jobs and flows still say they are not supported.
        with patch.object(sys, "argv", ["aiw", "review", ".", "60", "task.md"]):
            assert ai_worker.windows_cli_exit() is None
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            assert background.start(["review"]) == 64
        assert "Windows 暂不支持后台派单和流程" in stderr.getvalue()
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout), patch.object(sys, "argv", ["aiw", "--help"]):
            assert ai_worker.main() == 0
        assert "aiw <seat>" in stdout.getvalue()

        import aiw_windows
        import capabilities
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), patch.object(aiw_windows, "run") as run:
            assert aiw_windows.main(["capabilities", "x"]) == 64
            assert aiw_windows.main(["panel", "list"]) == 64
            assert aiw_windows.main(["slot", "--", "x"]) == 64
            assert aiw_windows.main(["review", ".", "60", "t.md", "--background"]) == 64
            run.assert_not_called()
            aiw_windows.main(["capabilities"])
            run.assert_called_with("capabilities.py", [])
            aiw_windows.main(["status"])
            run.assert_called_with("dispatch.py", ["status"])
            aiw_windows.main(["review", ".", "60", "t.md", "--ro"])
            run.assert_called_with("ai_worker.py", ["review", ".", "60", "t.md", "--ro"])
        assert "Windows 暂不支持 aiw panel" in stderr.getvalue()
        assert "Windows 暂不支持 aiw 后台派单" in stderr.getvalue()
        windows = capabilities.platform_capabilities(platform="win32")
        assert "background-notify-v1" not in windows["capabilities"] and "agent-runtime-v1" in windows["capabilities"]
        assert "--background" not in windows["dispatch"]["flags"]
        assert not {"--deliver", "--verify"} & set(windows["dispatch"]["values"])
        try:
            ai_worker.split_argv(["review", ".", "60", "t.md", "--verify", "true"], {"--verify"}, values={})
        except ai_worker.AttachError as error:
            assert "--verify" in str(error)
        else:
            raise AssertionError("--verify must be refused on Windows")
    assert capabilities.platform_capabilities(platform="darwin") == capabilities.capabilities()
    if sys.platform != "win32":
        assert "TEMP" not in ai_worker.scratch_environment(root / "scratch")


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
