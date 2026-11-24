#!/usr/bin/env python3
# 用途：登记本机盯梢命令及远端任务的进度；运行：aiw track [选项] 标签 -- 命令。
# 依赖：Python 3.11+ 标准库，POSIX flock 和进程组。
from __future__ import annotations

from platform_process import popen_kwargs, kill_tree

import argparse
import datetime as dt
from platform_lock import acquire_lock
import json
import math
import os
from env_compat import getenv, session_id
from pathlib import Path
import re
import selectors
import signal
import socket
import subprocess
import sys
import threading
import time
import unicodedata


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(64, f"aiw track: {message}\n")


def seconds(value):
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError("秒数必须是有限非负数")
    return number


def now():
    return dt.datetime.now().astimezone().isoformat()


def warn(error):
    print(f"[aiw track] 警告：{error}", file=sys.stderr, flush=True)


def acquire_liveness_lock(path):
    # 与 worker 一致：锁要么被持有，要么不存在；失败清理后重试一次。
    for _ in range(2):
        lock = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            lock = path.open("a")
            os.set_inheritable(lock.fileno(), False)
            acquire_lock(lock, blocking=False)
            return lock
        except OSError as error:
            failure = error
            if lock is not None:
                lock.close()
            try:
                path.unlink(missing_ok=True)
            except OSError as error:
                failure = error
    warn(f"获取锁失败：{failure}")
    return None


# 去除常见 ANSI CSI/OSC 序列，再去掉 Unicode 控制/格式字符。
ANSI = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]")


def progress_line(raw):
    text = ANSI.sub("", raw.decode("utf-8", errors="replace"))
    text = "".join(c for c in text if not unicodedata.category(c).startswith("C"))
    return text[:80] if text.strip() else ""


class Progress:
    def __init__(self, path, data, interval):
        self.path, self.data, self.interval = path, data, interval
        self.condition = threading.Condition()
        self.latest = self.saved = ""
        self.stopped = False
        self.last_write = time.monotonic()
        self.writes = 0
        self.thread = threading.Thread(target=self.run, name="track-progress")

    def write(self):
        tmp = self.path.with_suffix(".json.tmp")
        try:
            tmp.write_text(json.dumps(self.data, ensure_ascii=True) + "\n", encoding="utf-8")
            tmp.replace(self.path)
            self.writes += 1
            return True
        except OSError as error:
            warn(f"写状态失败：{error}")
            return False
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError as error:
                warn(f"删除临时状态失败：{error}")

    def start(self):
        if self.write():
            self.last_write = time.monotonic()
            self.thread.start()

    def update(self, line):
        if not line or not self.thread.is_alive():
            return
        with self.condition:
            if self.latest != line:
                self.latest = line
                self.condition.notify()

    def run(self):
        with self.condition:
            while not self.stopped:
                if self.latest == self.saved:
                    self.condition.wait()
                    continue
                remaining = self.interval - (time.monotonic() - self.last_write)
                if remaining > 0:
                    self.condition.wait(remaining)
                    continue
                self.data.update(progress=self.latest, updated=now())
                if not self.write():
                    return  # 状态不可写时停止发布，命令与输出继续。
                self.saved = self.latest
                self.last_write = time.monotonic()

    def stop(self):
        with self.condition:
            self.stopped = True
            self.condition.notify()
        if self.thread.ident is not None:
            self.thread.join()


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    parser = Parser(usage="aiw track [--host 名称] [--timeout 秒] 标签 -- 命令 [参数...]")
    parser.add_argument("--host", default=socket.gethostname().split(".")[0])
    parser.add_argument("--timeout", type=seconds)
    parser.add_argument("label")
    if "--" not in argv:
        parser.error("缺少 -- 和命令")
    split = argv.index("--")
    args = parser.parse_args(argv[:split])
    command = argv[split + 1:]
    if not command or not args.label:
        parser.error("标签和命令不能为空")
    try:
        interval = seconds(os.environ.get("AIW_TRACK_FLUSH_SECS", "5"))
    except ValueError:
        parser.error("AIW_TRACK_FLUSH_SECS 必须是有限非负秒数")
    state = Path(getenv("AIW_STATE", Path(__file__).resolve().parent / "state"))
    path = state / "tracks" / f"{os.getpid()}.json"
    lock_path = path.with_suffix(".lock")
    lock = progress = child = None
    pending = []

    def forward(signum, frame):
        if child is None:
            pending.append(signum)
        else:
            try:
                kill_tree(child.pid, signum)
            except ProcessLookupError:
                pass

    handlers = {sig: signal.signal(sig, forward) for sig in (signal.SIGINT, signal.SIGTERM)}
    # 唤醒管道：信号若被内核投给其它线程，靠它打断主线程的 select 去跑处理器。
    wake_r = wake_w = old_wakeup = None
    try:
        wake_r, wake_w = os.pipe()
        os.set_blocking(wake_w, False)
        old_wakeup = signal.set_wakeup_fd(wake_w, warn_on_full_buffer=False)
        lock = acquire_liveness_lock(lock_path)
        if lock is not None or not lock_path.exists():
            started = now()
            progress = Progress(path, dict(pid=os.getpid(), host=socket.gethostname(),
                target=args.host, session=session_id(),
                label=args.label, timeout=args.timeout, started=started, progress="", updated=started), interval)
            progress.start()
        else:
            warn("锁不可用且无法删除，本次不发布状态")
        try:
            child = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     **popen_kwargs(), close_fds=True)
        except OSError as error:
            warn(f"启动命令失败：{error}")
            return 127 if isinstance(error, FileNotFoundError) else 126
        for sig in pending:
            forward(sig, None)
        with child.stdout, selectors.DefaultSelector() as selector:
            # 只监听原始 fd，不混用 child.stdout 的缓冲读，避免吞掉字节。
            selector.register(child.stdout.fileno(), selectors.EVENT_READ)
            selector.register(wake_r, selectors.EVENT_READ)
            partial = b""
            while True:
                for key, _ in selector.select():
                    if key.fd == wake_r:
                        # 排空唤醒字节即可；处理器由解释器在主线程自动执行。
                        os.read(wake_r, 4096)
                        continue
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        if progress is not None and partial:
                            progress.update(progress_line(partial))
                        return child.wait()
                    sys.stdout.buffer.write(chunk)
                    sys.stdout.buffer.flush()
                    if progress is None:
                        continue
                    partial += chunk
                    lines = partial.split(b"\n")
                    partial = lines.pop()
                    if len(partial) > 4096:
                        partial = partial[:4096]  # 无换行的超长输出只留前缀。
                    latest = ""
                    for line in lines:
                        text = progress_line(line)
                        if text:
                            latest = text
                    if latest:
                        progress.update(latest)
    finally:
        if progress is not None:
            progress.stop()
        for item in (path, lock_path):
            try:
                item.unlink(missing_ok=True)
            except OSError as error:
                warn(f"删除状态失败：{error}")
        if lock is not None:
            lock.close()
        if old_wakeup is not None:
            signal.set_wakeup_fd(old_wakeup)
        for fd in (wake_r, wake_w):
            if fd is None:
                continue
            try:
                os.close(fd)
            except OSError:
                pass
        for sig, handler in handlers.items():
            signal.signal(sig, handler)
        if os.environ.get("AIW_TRACK_TEST_WRITE_COUNT") == "1":
            print(f"[aiw track] writes={progress.writes if progress else 0}", file=sys.stderr)


if __name__ == "__main__":
    rc = main()
    if rc < 0:
        # 保留被信号终止的 wait status，而非把 -SIG 转成模 256 退出码。
        if -rc not in (signal.SIGKILL, signal.SIGSTOP):
            signal.signal(-rc, signal.SIG_DFL)
        os.kill(os.getpid(), -rc)
    sys.exit(rc)
