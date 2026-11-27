"""Windows entry point for bin/aiw.cmd: the same subcommand routing as the bash bin/aiw.

Only what has a Windows path is routed; the rest says so instead of being taken for a seat name.
"""
import os
from pathlib import Path
import runpy
import sys

HERE = Path(__file__).resolve().parent
SCRIPTS = {
    "capabilities": ("capabilities.py", []), "off": ("dispatch.py", ["off"]), "on": ("dispatch.py", ["on"]),
    "status": ("dispatch.py", ["status"]), "rate": ("rating.py", []),
    "doctor": ("ai_worker.py", ["doctor"]), "enable": ("ai_worker.py", ["enable"]),
    "gc": ("gc.py", []),
}
# Subcommands of bin/aiw with no Windows path yet.
UNSUPPORTED = {"slot", "note", "findings", "triage", "track", "errand", "freecheck", "handoff", "route", "panel", "map",
               "flow", "goal", "tui", "tui-status", "board", "statusline", "weekly", "snapshot", "sync", "bg"}


def run(script, argv):
    sys.argv = [str(HERE / script), *argv]
    sys.path.insert(0, str(HERE))
    runpy.run_path(str(HERE / script), run_name="__main__")
    return 0


def main(argv):
    if sys.version_info < (3, 11):
        print("当前 Python " + sys.version.split()[0] + "，需要 Python 3.11 及以上；可用 AIW_PYTHON 指定解释器", file=sys.stderr)
        return 1
    command = argv[0] if argv else "help"
    if command == "capabilities" and len(argv) != 1:
        print("capabilities 不接受其他参数", file=sys.stderr)
        return 64
    if command in SCRIPTS:
        script, prefix = SCRIPTS[command]
        return run(script, [*prefix, *argv[1:]])
    if command in UNSUPPORTED or "--background" in argv:
        print(f"Windows 暂不支持 aiw {'后台派单' if '--background' in argv else command}", file=sys.stderr)
        return 64
    return run("ai_worker.py", argv)


if __name__ == "__main__":
    # aiw, its tools and agent-use all speak UTF-8; Windows defaults to the console code page (GBK
    # etc.) for files, pipes and stdio. bin/aiw.cmd sets PYTHONUTF8; rerun in UTF-8 mode otherwise.
    if not sys.flags.utf8_mode:
        import subprocess
        raise SystemExit(subprocess.call([sys.executable, "-X", "utf8", __file__, *sys.argv[1:]]))
    try:
        raise SystemExit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        raise SystemExit(130)
