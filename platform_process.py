"""Process group and dispatch support for each platform."""
import contextlib
import os
import signal
import subprocess
import sys

# Windows has no SIGKILL; taskkill /F is already forceful, so SIGTERM stands in for it.
SIGKILL = getattr(signal, "SIGKILL", signal.SIGTERM)


def kill_tree(pgid, sig):
    if sys.platform == "win32":
        if sig == 0:
            # Signal 0 only asks whether the group leader is still alive.
            if not _windows_alive(pgid):
                raise ProcessLookupError(pgid)
            return None
        result = subprocess.run(["taskkill", "/PID", str(pgid), "/T", "/F"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # 128: no such process. The tree is already gone. Any other failure is read the way killpg
        # reports it, so callers' (ProcessLookupError, PermissionError) handling covers Windows too.
        if result.returncode == 128 or result.returncode and not _windows_alive(pgid):
            raise ProcessLookupError(pgid)
        if result.returncode:
            raise PermissionError(pgid)
        return result
    return os.killpg(pgid, sig)


def _windows_alive(pid):
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = ctypes.c_void_p
    handle = kernel32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code)):
            return False
        return code.value == 259  # STILL_ACTIVE
    finally:
        kernel32.CloseHandle(ctypes.c_void_p(handle))


def popen_kwargs(*, preexec_fn=None):
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    kwargs = {"start_new_session": True}
    if preexec_fn is not None:
        kwargs["preexec_fn"] = preexec_fn
    return kwargs


@contextlib.contextmanager
def signals_blocked():
    """Hold SIGINT/SIGTERM for a short critical section; Windows has no signal mask."""
    if sys.platform == "win32":
        yield
        return
    mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT, signal.SIGTERM})
    try:
        yield
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, mask)


def unsupported_on_windows(feature):
    """Report a feature Windows does not have yet; False elsewhere."""
    if sys.platform == "win32":
        print(f"Windows 暂不支持{feature}", file=sys.stderr)
        return True
    return False


def dispatch_unavailable():
    """Kept for callers that predate Windows dispatch: background jobs and flows."""
    return unsupported_on_windows("后台派单和流程")
