#!/usr/bin/env python3
# 用途：全机测试名额、内存压力等待、降优先级和限线程；运行：aiw slot [选项] -- 命令。
# 依赖：Python 3.11+ 标准库、POSIX flock；macOS 可选 taskpolicy。
from __future__ import annotations

import argparse
from contextlib import contextmanager
import datetime as dt
import fcntl
import json
import math
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import time


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(64, f"aiw slot: {message}\n")


def positive(value):
    number = int(value)
    if number < 1:
        raise ValueError("名额数必须是正整数")
    return number


def seconds(value):
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError("秒数必须是有限非负数")
    return number


def warn(message):
    print(f"[aiw slot] {message}", file=sys.stderr, flush=True)


def slot_directory():
    directory = Path(os.environ.get("AIW_SLOT_DIR", "/tmp/aiw-slots"))
    try:
        create_directory(directory)
    except OSError:
        directory = Path(os.environ.get("TMPDIR", "/tmp")) / "aiw-slots"
        warn("名额只在本沙箱内生效")
        create_directory(directory)
    return directory


def create_directory(directory):
    try:
        directory.mkdir(parents=True, mode=0o777)
    except FileExistsError:
        if not directory.is_dir():
            raise
    else:
        directory.chmod(0o777)  # 不受 umask 影响；不改已有目录权限。


def open_lock(path):
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o666)
    lock = os.fdopen(fd, "a")
    try:
        os.fchmod(fd, 0o666)
    except OSError:
        pass  # 已有共享锁可能属于另一用户。
    return lock


def holders(directory, pool="slot"):
    result = []
    for path in sorted(directory.glob(f"{pool}-*.json")):
        with open_lock(path.with_suffix(".lock")) as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(data, dict):
                        result.append((path.stem, data))
                except (OSError, ValueError):
                    pass
            else:
                remove_record(path)
    return result


def remove_record(path):
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def pressure_level():
    try:
        command = shlex.split(os.environ.get(
            "AIW_SLOT_PRESSURE_CMD", "sysctl -n kern.memorystatus_vm_pressure_level"))
        result = subprocess.run(command, capture_output=True, text=True, timeout=1)
        return int(result.stdout.strip()) if result.returncode == 0 else 1
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return 1


def summary(name, data):
    return (f"{name}: pid={data.get('pid', '?')} {data.get('label', '')} "
            f"{data.get('command', '')} cwd={data.get('cwd', '')} "
            f"started={data.get('started', '')}")


@contextmanager
def reservation(command, slots=None, max_wait=900, label="", pool="slot"):
    # pool="slot" 是测试名额，持有期间标记 AIW_SLOT_HELD；其他池（如派单名额）不标记，
    # 执行方自己跑的 aiw slot 仍要排测试名额。
    marks = pool == "slot"
    if marks and os.environ.get("AIW_SLOT_HELD") == "1":
        yield
        return
    slots = positive(os.environ.get("AIW_TEST_SLOTS", "2")) if slots is None else positive(slots)
    deadline = time.monotonic() + seconds(max_wait)
    directory = slot_directory()
    lock = record = None
    queued = False
    saved_held = os.environ.get("AIW_SLOT_HELD")
    try:
        while lock is None:
            for index in range(slots):
                candidate = open_lock(directory / f"{pool}-{index}.lock")
                try:
                    fcntl.flock(candidate, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    candidate.close()
                    continue
                lock = candidate
                record = directory / f"{pool}-{index}.json"
                data = dict(pid=os.getpid(), label=label, command=shlex.join(command)[:120],
                            cwd=os.getcwd(), started=dt.datetime.now().astimezone().isoformat())
                try:
                    record.write_text(json.dumps(data, ensure_ascii=False) + "\n", encoding="utf-8")
                    record.chmod(0o666)
                except OSError as error:
                    warn(f"写持有者记录失败：{error}")
                break
            if lock is not None:
                break
            if not queued:
                warn(f"名额已满（{slots} 个），排队中…")
                for name, data in holders(directory, pool):
                    warn(summary(name, data))
                queued = True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                warn("名额等待已超过等待上限，直接运行")
                break
            time.sleep(min(5, remaining))
        waiting = False
        while pressure_level() >= 2:
            if not waiting:
                warn("内存压力高，等待…")
                waiting = True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                warn("内存等待已超过等待上限，直接运行")
                break
            time.sleep(min(15, remaining))
        if marks:
            os.environ["AIW_SLOT_HELD"] = "1"
        yield
    finally:
        if marks and saved_held is None:
            os.environ.pop("AIW_SLOT_HELD", None)
        elif marks:
            os.environ["AIW_SLOT_HELD"] = saved_held
        if record is not None:
            remove_record(record)
        if lock is not None:
            lock.close()


def lower_to(target=10):
    try:
        current = os.getpriority(os.PRIO_PROCESS, 0)
        if current < target:
            os.nice(target - current)
        return True
    except OSError:
        return False


def qos(current=False):
    mode = os.environ.get("AIW_SLOT_QOS", "nice")
    if mode == "0":
        warn("运行方式：普通优先级（AIW_SLOT_QOS=0）")
        return []
    if mode not in ("nice", "bg"):
        warn(f"未知 AIW_SLOT_QOS 值 {mode!r}，按默认 nice 处理")
    if mode == "bg":
        warn("警告：主机繁忙时后台 QoS 可能长时间拿不到 CPU，带超时的测试会误判")
    policy = "/usr/sbin/taskpolicy"
    if mode == "bg" and sys.platform == "darwin" and Path(policy).is_file():
        try:
            probe = subprocess.run([policy, "-b", "/usr/bin/true"],
                                   capture_output=True, timeout=1)
            if probe.returncode == 0:
                if not current or subprocess.run(
                        [policy, "-b", "-p", str(os.getpid())],
                        capture_output=True, timeout=1).returncode == 0:
                    warn("运行方式：taskpolicy -b（后台 QoS，倾向能效核）")
                    return [] if current else [policy, "-b"]
        except (OSError, subprocess.TimeoutExpired):
            pass
    try:
        already_low = os.getpriority(os.PRIO_PROCESS, 0) >= 10
    except OSError:
        already_low = False
    if lower_to():
        warn("运行方式：nice +10（已是低优先级，未再降）" if already_low
             else "运行方式：nice +10")
    else:
        warn("运行方式：普通优先级（nice 被拒绝）")
    return []


THREAD_VARIABLES = (
    "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS", "POLARS_MAX_THREADS",
    "RAYON_NUM_THREADS",
)


def thread_environment():
    value = os.environ.get("AIW_SLOT_THREADS", "1")
    if not value.isascii() or not value.isdecimal():
        raise ValueError("AIW_SLOT_THREADS 必须是非负整数")
    env = dict(os.environ)
    if value.strip("0"):
        for name in THREAD_VARIABLES:
            env.setdefault(name, value)
    return env


def run(command, **options):
    try:
        env = thread_environment()
    except ValueError as error:
        warn(str(error))
        return 64
    child = None
    pending = []
    launching = False

    def forward(signum, frame):
        if child is None:
            pending.append(signum)
            # 排队阶段终止，避免收到终止信号后继续等到上限。
            if not launching:
                raise SystemExit(128 + signum)
            return
        try:
            os.killpg(child.pid, signum)
        except ProcessLookupError:
            pass

    handlers = {sig: signal.signal(sig, forward) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        with reservation(command, **options):
            env["AIW_SLOT_HELD"] = os.environ["AIW_SLOT_HELD"]
            prefix = qos()
            launching = True
            try:
                child = subprocess.Popen(prefix + command, start_new_session=True, env=env)
            except OSError as error:
                warn(f"启动命令失败：{error}")
                return 127 if isinstance(error, FileNotFoundError) else 126
            for sig in pending:
                forward(sig, None)
            return child.wait()
    finally:
        for sig, handler in handlers.items():
            signal.signal(sig, handler)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    parser = Parser(usage="aiw slot [--slots N] [--max-wait 秒] [--label 文本] -- 命令 [参数...] | --status")
    parser.add_argument("--slots", type=positive)
    parser.add_argument("--max-wait", type=seconds, default=900)
    parser.add_argument("--label", default="")
    parser.add_argument("--status", action="store_true")
    split = argv.index("--") if "--" in argv else len(argv)
    args = parser.parse_args(argv[:split])
    command = argv[split + 1:]
    try:
        slots = args.slots if args.slots is not None else positive(os.environ.get("AIW_TEST_SLOTS", "2"))
    except ValueError:
        parser.error("AIW_TEST_SLOTS 必须是正整数")
    if not args.status and not command:
        parser.error("缺少 -- 和命令")
    try:
        if args.status:
            active = holders(slot_directory())
            print(f"名额数：{slots}；内存压力等级：{pressure_level()}")
            for name, data in active:
                print(summary(name, data))
            if not active:
                print("空闲")
            return 0
        return run(command, slots=slots, max_wait=args.max_wait, label=args.label)
    except OSError as error:
        warn(str(error))
        return 1


if __name__ == "__main__":
    rc = main()
    if rc < 0:
        if -rc not in (signal.SIGKILL, signal.SIGSTOP):
            signal.signal(-rc, signal.SIG_DFL)
        os.kill(os.getpid(), -rc)
    sys.exit(rc)
