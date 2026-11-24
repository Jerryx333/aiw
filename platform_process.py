"""Process group and dispatch support for each platform."""
import os
import subprocess
import sys


def kill_tree(pgid, sig):
    if sys.platform == "win32":
        return subprocess.run(["taskkill", "/PID", str(pgid), "/T", "/F"], check=True)
    return os.killpg(pgid, sig)


def popen_kwargs(*, preexec_fn=None):
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    kwargs = {"start_new_session": True}
    if preexec_fn is not None:
        kwargs["preexec_fn"] = preexec_fn
    return kwargs


def dispatch_unavailable():
    if sys.platform == "win32":
        print("Windows 暂不支持派单", file=sys.stderr)
        return True
    return False
