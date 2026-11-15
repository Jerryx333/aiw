#!/usr/bin/env python3
"""普通派单的后台模式与完成通知（issue #63）。

    aiw <seat> <workdir> <timeout_s> <prompt_file|-> --background [--no-notify] [派单选项 ...]
    aiw bg status <编号> [--json] | result <编号> | list [--all]

后台模式先做和普通派单相同的用法检查，再交给脱离当前会话的监工进程执行，
立即打印任务编号和日志位置后返回 0。监工等派单结束，记下退出码和状态
（succeeded / failed / timeout / interrupted），再用 ocs 给发起会话发一条通知。

通知地址在发起时绑定为会话的固定编号（ocs whoami 的 id，如 claude-1a2b3c4d、
pi-1a2b3c4d），不用可改名的名字，切到别的会话也不会投错。通知只含本地字段
（编号、状态、退出码、结果位置、下一步命令），不含执行方输出，避免把外部
文本当成发起会话的指令。没有 ocs 或认不出会话时明说「不会自动回报」。
发起会话已退出时 ocs 投递失败，只记在 job.json，不补发。
"""
from __future__ import annotations

import fcntl
import json
import os
import secrets
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from env_compat import getenv

HERE = Path(__file__).resolve().parent
STATE = Path(getenv("AIW_STATE", HERE / "state"))
JOBS = STATE / "bg"
DISPATCH_FLAGS = {"--ro", "--text", "--worktree", "--net", "--no-fallback", "--chain", "--no-findings",
                  "--allow-nested", "--attach", "--effort", "--verify", "--verify-timeout"}
STATES = {0: "succeeded", 124: "timeout", 130: "interrupted"}
USAGE = __doc__.split("\n\n")[1]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ocs_bin() -> str:
    return os.environ.get("AIW_OCS_BIN", "ocs")


def job_dir(job_id: str) -> Path | None:
    if not job_id.startswith("bg-") or not all(c.isalnum() or c == "-" for c in job_id):
        return None
    return JOBS / job_id


def load(path: Path) -> dict:
    return json.loads((path / "job.json").read_text(encoding="utf-8"))


def save(path: Path, job: dict) -> None:
    tmp = path / f".job.{os.getpid()}.tmp"
    tmp.write_text(json.dumps(job, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path / "job.json")


def supervised(path: Path) -> bool:
    """监工整个生命周期持有 supervisor.lock；锁抢得到就说明没有活着的监工。
    用锁而不是进程号判活：进程号会被复用，锁随进程退出自动释放。"""
    try:
        fd = os.open(path / "supervisor.lock", os.O_RDONLY)
    except FileNotFoundError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    finally:
        os.close(fd)
    return False


def current_state(path: Path, job: dict) -> str:
    """running 但监工已不在（被杀、关机）时报 lost，避免永远显示在跑。"""
    if job.get("state") == "running" and not supervised(path):
        return "lost"
    return job.get("state", "unknown")


def resolve_address() -> tuple[str | None, str]:
    """返回 (固定会话编号, 失败原因)。"""
    try:
        reply = subprocess.run([ocs_bin(), "whoami", "--json"], capture_output=True, text=True, timeout=10)
    except FileNotFoundError:
        return None, "本机没有 ocs"
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, f"ocs 不可用（{type(e).__name__}）"
    try:
        identity = json.loads(reply.stdout) if reply.returncode == 0 else None
    except ValueError:
        identity = None
    if isinstance(identity, dict):
        # 只认固定编号；名字可被改名或 --force 抢走，退回名字会投错会话。
        ident = identity.get("id")
        if isinstance(ident, str) and ident.strip():
            return ident.strip(), ""
    return None, "ocs 认不出当前会话的固定编号"


def start(argv: list[str]) -> int:
    if os.environ.get("AIW_NESTED") == "1":
        print("[aiw bg] 已在被外包的会话里，不能后台派单（执行方不得联系或唤醒其它会话）", file=sys.stderr)
        return 64
    notify = "--no-notify" not in argv
    argv = [a for a in argv if a not in ("--background", "--no-notify")]
    sys.path.insert(0, str(HERE))
    from ai_worker import AttachError, split_argv
    try:
        values = {}
        args, flags, _ = split_argv(argv, DISPATCH_FLAGS, values=values)
    except AttachError as e:
        print(f"[aiw bg] {e}", file=sys.stderr)
        return 64
    if len(args) != 4 or args[0] in {"doctor", "enable"}:
        print(USAGE, file=sys.stderr)
        return 64
    # 和普通派单一样先拦下不读座位配置就能判的用法错误；要读配置的检查（座位
    # 是否存在、停用、只读座位等）留给后台执行，失败照常通知 failed。
    seat, workdir, timeout_s, prompt_file = args
    readonly = "--ro" in flags or "--text" in flags or seat in ("review", "draft")
    for flag, name in (("verify", "--verify"), ("--worktree", "--worktree")):
        if (values.get(flag) or flag in flags) and readonly:
            print(f"[aiw bg] {name} 只用于写任务", file=sys.stderr)
            return 64
    if not timeout_s.isdigit() or int(timeout_s) <= 0:
        print(f"[aiw bg] 超时应为正整数秒，收到 {timeout_s!r}", file=sys.stderr)
        return 64
    if not Path(workdir).is_dir():
        print(f"[aiw bg] 工作目录不存在：{workdir}", file=sys.stderr)
        return 64
    if prompt_file != "-" and not Path(prompt_file).is_file():
        print(f"[aiw bg] 读不了 prompt 文件 {prompt_file}", file=sys.stderr)
        return 64
    stdin_prompt = None
    if args[3] == "-":
        if argv.count("-") != 1:
            print("[aiw bg] 参数里有多个「-」，分不清哪个是标准输入 prompt，请改用说明文件", file=sys.stderr)
            return 64
        try:
            stdin_prompt = sys.stdin.buffer.read().decode("utf-8")
        except (OSError, UnicodeDecodeError) as e:
            print(f"[aiw bg] 读不了标准输入的 prompt：{e}", file=sys.stderr)
            return 64
        if not stdin_prompt.strip():
            print("[aiw bg] 标准输入的 prompt 是空的", file=sys.stderr)
            return 64

    address, reason = resolve_address() if notify else (None, "已用 --no-notify 关闭")
    job_id = f"bg-{datetime.now():%Y%m%d-%H%M%S}-{secrets.token_hex(3)}"
    path = JOBS / job_id
    path.mkdir(parents=True)
    if stdin_prompt is not None:
        prompt = path / "prompt.md"
        prompt.write_text(stdin_prompt, encoding="utf-8")
        argv[argv.index("-")] = str(prompt)
    job = {"id": job_id, "argv": argv, "cwd": os.getcwd(), "started_at": now(), "state": "running",
           "notify": {"address": address} if address else {"disabled": reason}}
    save(path, job)
    # 先在这里拿锁再交给监工，status 从第一刻起就判得出活着，没有「刚启动误报 lost」的空档。
    lock = os.open(path / "supervisor.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with (path / "supervisor.log").open("ab") as log:
            sup = subprocess.Popen([sys.executable, "-B", str(Path(__file__).resolve()), "supervise", job_id, str(lock)],
                                   start_new_session=True, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                   pass_fds=(lock,), cwd=os.getcwd())
    finally:
        os.close(lock)
    job["supervisor_pid"] = sup.pid
    save(path, job)
    print(f"任务编号: {job_id}")
    print("状态: running（后台执行中）")
    print(f"输出: {path / 'stdout.log'}")
    print(f"诊断: {path / 'stderr.log'}")
    print(f"查看: aiw bg status {job_id} ；结束后 aiw bg result {job_id}")
    if address:
        print(f"通知: 结束（成功、失败、超时）后会通知本会话 {address}，本会话会被唤醒接着处理；不用轮询")
    else:
        print(f"通知: 未接通知（{reason}）——完成后不会自动回报，需要自己用 aiw bg status {job_id} 查")
    return 0


def run_dir_from(stderr_text: str) -> str | None:
    for line in reversed(stderr_text.splitlines()):
        for label in ("[ai-worker] 全文：", "[ai-worker] 排查："):
            if line.startswith(label):
                # 只去掉 aiw 自己追加的附注，路径里本身的全角括号要保留。
                return line[len(label):].split("（output.md 没写成功", 1)[0].strip()
    return None


def send_notice(path: Path, job: dict) -> None:
    notification = job.get("notify") or {}
    address = notification.get("address")
    if not address or "sent_at" in notification:
        return
    lines = [f"[aiw 后台派单结束] 任务编号: {job['id']}",
             f"状态: {job['state']}（退出码 {job['rc']}）",
             f"结果: {job.get('result') or path / 'stdout.log'}",
             f"下一步: aiw bg result {job['id']}"]
    # 先记已发再发：中断或结果未知也不重发，ocs 已存的消息可按编号对上。
    notification.update(sent_at=now(), exit=3)
    save(path, job)
    try:
        reply = subprocess.run([ocs_bin(), "dm", address, "\n".join(lines), "--as", "aiw", "--json"],
                               capture_output=True, text=True, timeout=30)
        notification["exit"] = reply.returncode
    except subprocess.TimeoutExpired:
        notification["exit"] = 3
    except OSError:
        notification["exit"] = 1
    save(path, job)


def supervise(job_id: str, lock_fd: str) -> int:
    """只由 start 拉起：必须带着 start 交来的、已锁住的 supervisor.lock。"""
    if os.environ.get("AIW_NESTED") == "1":
        return 64
    path = job_dir(job_id)
    if path is None or not (path / "job.json").exists() or not lock_fd.isdigit():
        return 64
    try:
        held, expected = os.fstat(int(lock_fd)), os.stat(path / "supervisor.lock")
    except OSError:
        return 64
    if (held.st_dev, held.st_ino) != (expected.st_dev, expected.st_ino):
        return 64
    job = load(path)
    if job.get("state") != "running" or "rc" in job:
        return 64
    worker = [sys.executable, str(HERE / "ai_worker.py"), *job["argv"]]
    stop = {"signal": None}

    def forward(signum, _frame):
        stop["signal"] = signum
        if child is not None and child.poll() is None:
            child.send_signal(signum)

    child = None
    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(signum, forward)
    with (path / "stdout.log").open("wb") as out, (path / "stderr.log").open("wb") as err:
        child = subprocess.Popen(worker, stdin=subprocess.DEVNULL, stdout=out, stderr=err, cwd=job["cwd"])
        while True:
            try:
                rc = child.wait()
                break
            except InterruptedError:
                continue
    stderr_text = (path / "stderr.log").read_text(encoding="utf-8", errors="replace")
    run_dir = run_dir_from(stderr_text)
    result = None
    if run_dir and Path(run_dir).name == "output.md":
        result, run_dir = run_dir, str(Path(run_dir).parent)
    if rc < 0 or stop["signal"] is not None:
        state = "interrupted"
    else:
        state = STATES.get(rc, "failed")
    job.update(state=state, rc=rc, finished_at=now(), run_dir=run_dir, result=result)
    save(path, job)
    send_notice(path, job)
    return 0


def describe(path: Path, job: dict) -> str:
    notify = job.get("notify") or {}
    if notify.get("address"):
        sent = "已通知" if notify.get("exit") == 0 else (f"通知投递失败（ocs 退出码 {notify['exit']}，不补发）"
                                                       if "exit" in notify else "结束后通知")
        notice = f"{sent} {notify['address']}"
    else:
        notice = f"未接通知（{notify.get('disabled', '未知原因')}）"
    rc = job.get("rc")
    return "\n".join([f"任务编号: {job['id']}", f"状态: {current_state(path, job)}" + (f"（退出码 {rc}）" if rc is not None else ""),
                      f"开始: {job.get('started_at')}" + (f"  结束: {job['finished_at']}" if job.get("finished_at") else ""),
                      f"结果: {job.get('result') or job.get('run_dir') or '（未结束）'}", f"通知: {notice}"])


def main(argv: list[str]) -> int:
    if argv[:1] == ["start"]:
        return start(argv[1:])
    if argv[:1] == ["supervise"] and len(argv) == 3:
        return supervise(argv[1], argv[2])
    if argv[:1] == ["list"] and argv[1:] in ([], ["--all"]):
        paths = sorted(JOBS.glob("bg-*"), reverse=True) if JOBS.is_dir() else []
        for path in paths if argv[1:] else paths[:20]:
            try:
                job = load(path)
            except (OSError, ValueError):
                continue
            print(f"{job['id']}  {current_state(path, job):11s}  rc={job.get('rc', '-')}  {' '.join(job['argv'][:1])}")
        return 0
    if argv[:1] in (["status"], ["result"]) and len(argv) in (2, 3):
        path = job_dir(argv[1])
        if path is None or not (path / "job.json").exists():
            print(f"[aiw bg] 没有这个后台任务：{argv[1]}", file=sys.stderr)
            return 64
        job = load(path)
        if argv[0] == "status":
            if argv[2:] == ["--json"]:
                print(json.dumps({**job, "state": current_state(path, job)}, ensure_ascii=False))
            elif argv[2:]:
                print(USAGE, file=sys.stderr)
                return 64
            else:
                print(describe(path, job))
            return 0
        if argv[2:]:
            print(USAGE, file=sys.stderr)
            return 64
        state = current_state(path, job)
        if state == "running":
            print(f"[aiw bg] {job['id']} 还在跑，结束后会通知；现在可看 aiw bg status {job['id']}", file=sys.stderr)
            return 75
        if (path / "stdout.log").exists():
            sys.stdout.write((path / "stdout.log").read_text(encoding="utf-8", errors="replace"))
        tail = (path / "stderr.log").read_text(encoding="utf-8", errors="replace").splitlines()[-20:] \
            if (path / "stderr.log").exists() else []
        print("\n".join(tail), file=sys.stderr)
        rc = job.get("rc")
        return rc if isinstance(rc, int) and 0 <= rc < 256 else 1
    print(USAGE, file=sys.stderr)
    return 64


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
