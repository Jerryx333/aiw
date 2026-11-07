#!/usr/bin/env python3
# 用途：清理过期 runs/panels/maps/flows/results 子目录及 codex-home 日期会话目录；运行：python3 gc.py [天数] [--dry-run]
# 依赖：Python 3.11+ 标准库；状态目录由 AIW_STATE 指定。
import os
import datetime as dt
from env_compat import getenv
import shutil
import sys
import time
from pathlib import Path


def directory_size(path: Path) -> int:
    total = path.lstat().st_size
    for root, dirs, files in os.walk(path, followlinks=False):
        total += sum((Path(root) / name).lstat().st_size for name in dirs + files)
    return total


def main() -> int:
    args = sys.argv[1:]
    days = [a for a in args if a != "--dry-run"]
    if len(days) > 1 or (days and not days[0].isdigit()):
        print("用法：aiw gc [非负整数天数] [--dry-run]", file=sys.stderr)
        return 64
    cutoff = time.time() - int(days[0] if days else 30) * 86400
    dry = "--dry-run" in args
    state = Path(getenv("AIW_STATE", Path(__file__).resolve().parent / "state"))
    from ai_worker import sweep_worktrees
    sweep_worktrees(dry_run=dry, state=state, output=sys.stdout)
    count = size = 0
    candidates = []
    tui = state / "tui"
    if not tui.is_symlink() and tui.is_dir():
        for path in sorted(tui.iterdir()):
            if not path.is_symlink() and path.is_file() and path.stat().st_mtime < cutoff:
                candidates.append(path)
    archive = state / "worktrees" / "archive"
    if not archive.parent.is_symlink() and not archive.is_symlink() and archive.is_dir():
        for path in sorted(archive.glob("*.patch")):
            if not path.is_symlink() and path.is_file() and path.stat().st_mtime < cutoff:
                candidates.append(path)
        for path in sorted(archive.glob("*.files")):
            if not path.is_symlink() and path.is_dir() and path.stat().st_mtime < cutoff:
                candidates.append(path)
    for name in ("runs", "panels", "maps", "flows", "results"):
        parent = state / name
        if parent.is_symlink() or not parent.exists():
            continue
        for path in sorted(parent.iterdir()):
            if path.is_symlink() or not path.is_dir() or path.stat().st_mtime >= cutoff:
                continue
            candidates.append(path)
    sessions = state / "codex-home" / "sessions"
    cutoff_date = dt.datetime.fromtimestamp(cutoff).date()
    # Check every ancestor before iterating: glob would traverse linked years/months.
    if not sessions.parent.is_symlink() and not sessions.is_symlink() and sessions.is_dir():
        for year in sorted(sessions.iterdir()):
            if (year.is_symlink() or not year.is_dir() or len(year.name) != 4
                    or not year.name.isascii() or not year.name.isdigit()):
                continue
            for month in sorted(year.iterdir()):
                if (month.is_symlink() or not month.is_dir() or len(month.name) != 2
                        or not month.name.isascii() or not month.name.isdigit()):
                    continue
                for day in sorted(month.iterdir()):
                    if (day.is_symlink() or not day.is_dir() or len(day.name) != 2
                            or not day.name.isascii() or not day.name.isdigit()):
                        continue
                    try:
                        date = dt.date(int(year.name), int(month.name), int(day.name))
                    except ValueError:
                        continue
                    if date < cutoff_date:
                        candidates.append(day)
    for path in candidates:
        amount = directory_size(path) if path.is_dir() else path.stat().st_size
        print(f"{'将删除' if dry else '删除'} {path} ({amount} 字节)")
        if not dry:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
        count += 1
        size += amount
    label = '个目录/文件' if any(path.is_file() for path in candidates) else '个目录'
    print(f"{'预计删除' if dry else '已删除'} {count} {label}，{'预计释放' if dry else '释放'} {size} 字节")
    return 0


if __name__ == "__main__":
    sys.exit(main())
