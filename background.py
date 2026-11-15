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


def alive(pid) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def current_state(job: dict) -> str:
    """running 但监工已不在（被杀、关机）时报 lost，避免永远显示在跑。"""
    if job.get("state") == "running" and not alive(job.get("supervisor_pid")):
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
        ident = identity.get("id")
        if isinstance(ident, str) and ident.strip():
            return ident.strip(), ""
        addresses = identity.get("addresses")
        if isinstance(addresses, list) and addresses and isinstance(addresses[0], str) and addresses[0].strip():
            return addresses[0].strip(), ""
    return None, "ocs 认不出当前会话"


def start(argv: list[str]) -> int:
    if os.environ.get("AIW_NESTED") == "1":
        print("[aiw bg] 已在被外包的会话里，不能后台派单（执行方不得联系或唤醒其它会话）", file=sys.stderr)
        return 64
    notify = "--no-notify" not in argv
    argv = [a for a in argv if a not in ("--background", "--no-notify")]
    sys.path.insert(0, str(HERE))
    from ai_worker import AttachError, split_argv
    try:
        args, _, _ = split_argv(argv, DISPATCH_FLAGS, values={})
    except AttachError as e:
        print(f"[aiw bg] {e}", file=sys.stderr)
        return 64
    if len(args) != 4 or args[0] in {"doctor", "enable"}:
        print(USAGE, file=sys.stderr)
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
    with (path / "supervisor.log").open("ab") as log:
        subprocess.Popen([sys.executable, "-B", str(Path(__file__).resolve()), "supervise", job_id],
                               start_new_session=True, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                               close_fds=True, cwd=os.getcwd())
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
                return line[len(label):].split("（", 1)[0].strip()
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


def supervise(job_id: str) -> int:
    path = job_dir(job_id)
    if path is None or not (path / "job.json").exists():
        return 64
    job = load(path)
    job["supervisor_pid"] = os.getpid()
    save(path, job)
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


def describe(job: dict) -> str:
    notify = job.get("notify") or {}
    if notify.get("address"):
        sent = "已通知" if notify.get("exit") == 0 else (f"通知投递失败（ocs 退出码 {notify['exit']}，不补发）"
                                                       if "exit" in notify else "结束后通知")
        notice = f"{sent} {notify['address']}"
    else:
        notice = f"未接通知（{notify.get('disabled', '未知原因')}）"
    rc = job.get("rc")
    return "\n".join([f"任务编号: {job['id']}", f"状态: {current_state(job)}" + (f"（退出码 {rc}）" if rc is not None else ""),
                      f"开始: {job.get('started_at')}" + (f"  结束: {job['finished_at']}" if job.get("finished_at") else ""),
                      f"结果: {job.get('result') or job.get('run_dir') or '（未结束）'}", f"通知: {notice}"])


def main(argv: list[str]) -> int:
    if argv[:1] == ["start"]:
        return start(argv[1:])
    if argv[:1] == ["supervise"] and len(argv) == 2:
        return supervise(argv[1])
    if argv[:1] == ["list"] and argv[1:] in ([], ["--all"]):
        paths = sorted(JOBS.glob("bg-*"), reverse=True) if JOBS.is_dir() else []
        for path in paths if argv[1:] else paths[:20]:
            try:
                job = load(path)
            except (OSError, ValueError):
                continue
            print(f"{job['id']}  {current_state(job):11s}  rc={job.get('rc', '-')}  {' '.join(job['argv'][:1])}")
        return 0
    if argv[:1] in (["status"], ["result"]) and len(argv) in (2, 3):
        path = job_dir(argv[1])
        if path is None or not (path / "job.json").exists():
            print(f"[aiw bg] 没有这个后台任务：{argv[1]}", file=sys.stderr)
            return 64
        job = load(path)
        if argv[0] == "status":
            if argv[2:] == ["--json"]:
                print(json.dumps({**job, "state": current_state(job)}, ensure_ascii=False))
            elif argv[2:]:
                print(USAGE, file=sys.stderr)
                return 64
            else:
                print(describe(job))
            return 0
        if argv[2:]:
            print(USAGE, file=sys.stderr)
            return 64
        state = current_state(job)
        if state == "running":
            print(f"[aiw bg] {job['id']} 还在跑，结束后会通知；现在可看 aiw bg status {job['id']}", file=sys.stderr)
            return 75
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
