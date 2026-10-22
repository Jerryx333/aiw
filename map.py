#!/usr/bin/env python3
"""逐文件派同一份文本任务（只读，保留正常顶替链）。

    aiw map <workdir> <timeout_s> <prompt_file> <文件 ...> [--seat 座位] [--jobs N]

默认座位由 map_default 配置、并发 3；1–50 个附件逐个校验，通过后才派单。
结果在 state/maps/<时间>-<pid>/，summary.json 按输入顺序汇总。
返回码：0 全部成功；1 任一单失败；64 用法或附件错误；130/143 信号中断。
"""
from __future__ import annotations

import concurrent.futures as cf
import datetime as dt
import json
import math
import os
from env_compat import getenv
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from harness_ids import format_panel_id

from ai_worker import AttachError, SEATS, build_attachments, dispatch, resolve_role, RoleError
from seats_config import ROLE_NAMES, ConfigError, forward_command

HERE = Path(__file__).resolve().parent
STATE = Path(getenv("AIW_STATE", HERE / "state"))


def outer_timeout(seat: str, timeout: int, grace: float) -> float:
    # worker 对每家分别限时，agent-use 每家另有 30 秒余量；须覆盖整条顶替链。
    return (1 + len(SEATS["fallback"].get(seat, []))) * (timeout + 30) + grace


class Workers:
    def __init__(self):
        self.lock = threading.Lock()
        self.active = set()
        self.interrupted = set()
        self.stop_code = 0

    def signal(self, signum, frame):
        # 处理器只置标志，不碰锁；启动与登记可能正持锁，清理由主循环完成。
        if not self.stop_code:
            self.stop_code = 128 + signum

    def stop(self):
        with self.lock:
            active = [p for p in self.active if p.poll() is None]
            self.interrupted.update(active)
        for p in active:
            try:
                p.terminate()  # worker 自己清理供应商进程组
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + 10
        for p in active:
            try:
                p.wait(timeout=max(0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()


def ask(item: tuple[int, str], seat: str, workdir: str, timeout: str,
        out: Path, grace: float, workers: Workers) -> dict:
    index, file = item
    stem = f"{index:02d}-{Path(file).name}"
    provider_file = out / f"{stem}.provider"
    env = dict(os.environ, AIW_MAP_DIR=str(out), AIW_PROVIDER_FILE=str(provider_file))
    env.pop("AIW_PANEL_DIR", None)  # 本批次账本的来源是 map
    argv = [sys.executable, str(HERE / "ai_worker.py"), seat, workdir, timeout,
            str(out / "prompt.md"), "--ro", "--text", "--attach", file]
    limit = outer_timeout(seat, int(timeout), grace)
    with workers.lock:
        if workers.stop_code:
            return {"file": file, "rc": workers.stop_code, "provider": None, "chars": 0}
        p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
        workers.active.add(p)
    try:
        stdout, stderr = p.communicate(timeout=limit)
        rc = p.returncode
    except subprocess.TimeoutExpired:
        p.terminate()  # worker 负责清理供应商进程组
        try:
            stdout, stderr = p.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()
            stdout, stderr = p.communicate()
        rc = 124
        stderr += f"\n批处理外层超时（{limit}s）\n"
    finally:
        with workers.lock:
            workers.active.discard(p)
            if p in workers.interrupted:
                rc = workers.stop_code
                workers.interrupted.discard(p)
    (out / f"{stem}.md").write_text(stdout, encoding="utf-8")
    if stderr:
        (out / f"{stem}.stderr.txt").write_text(stderr, encoding="utf-8")
    provider = provider_file.read_text(encoding="utf-8").strip() if provider_file.exists() else None
    return {"file": file, "rc": rc, "provider": provider, "chars": len(stdout)}


def main() -> int:
    if os.environ.get("AIW_NESTED") == "1":
        argv = sys.argv
        try:
            sys.argv = [str(HERE / "ai_worker.py")]
            return dispatch(None, {})  # 复用 worker 的嵌套拒绝及退出码
        finally:
            sys.argv = argv
    args, seat, jobs = [], SEATS.get("map_default", "draft"), "3"
    try:
        it = iter(sys.argv[1:])
        for arg in it:
            if arg in ("--seat", "--jobs"):
                value = next(it, None)
                if value is None or value.startswith("--"):
                    raise ValueError(f"{arg} 后面缺参数")
                if arg == "--seat":
                    seat = value
                else:
                    jobs = value
            elif arg.startswith("-"):
                raise ValueError(f"未知开关：{arg}")
            else:
                args.append(arg)
        if not 4 <= len(args) <= 53:
            raise ValueError("需要 1–50 个文件\n" + __doc__)
        workdir, timeout, prompt_file, *files = args
        if not jobs.isascii() or not jobs.isdigit() or not 1 <= int(jobs) <= 6:
            raise ValueError("--jobs 必须为 1–6 的整数")
        jobs = int(jobs)
        forwarding_role = bool(forward_command(SEATS)) and seat in ROLE_NAMES
        if seat in ROLE_NAMES and not forwarding_role:
            role = seat
            try:
                seat = resolve_role(role)
            except RoleError as exc:
                print(exc, file=sys.stderr)
                return 2
            print(f"[ai-worker] 角色 {role} → 座位 {seat}", file=sys.stderr)
        cfg = {} if forwarding_role else SEATS["seats"].get(seat)
        if cfg is None:
            raise ValueError(f"未知座位：{seat}")
        limit = cfg.get("max_concurrency")
        if limit is not None and (type(limit) is not int or limit < 1):
            raise ValueError("max_concurrency 必须为正整数")
        if not forwarding_role and cfg.get("kind") != "agent_use" and not cfg.get("argv_ro"):
            raise ValueError(f"座位 {seat} 不支持只读任务")
        if not timeout.isascii() or not timeout.isdigit() or int(timeout) <= 0:
            raise ValueError("超时应为正整数秒")
        if not Path(workdir).is_dir():
            raise ValueError(f"工作目录不存在：{workdir}")
        workdir = str(Path(workdir).resolve())
        for file in files:
            build_attachments(workdir, [file])
        prompt = Path(prompt_file).read_text(encoding="utf-8")
        grace = float(os.environ.get("AIW_MAP_GRACE", "90"))
        if not math.isfinite(grace) or grace < 0:
            raise ValueError("AIW_MAP_GRACE 必须是非负有限秒数")
    except (ValueError, OSError, AttachError, ConfigError) as e:
        print(e, file=sys.stderr)
        return 64
    if limit is not None and jobs > limit:
        jobs = min(jobs, limit)
        print(f"{seat} max_concurrency：并发降为 {jobs}（jobs={jobs}）", file=sys.stderr)
    out = STATE.resolve() / "maps" / format_panel_id(dt.datetime.now(), os.getpid())
    out.mkdir(parents=True, exist_ok=True)
    (out / "prompt.md").write_text(prompt, encoding="utf-8")
    workers = Workers()
    previous = {sig: signal.signal(sig, workers.signal) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        with cf.ThreadPoolExecutor(max_workers=jobs) as ex:
            futures = [ex.submit(ask, item, seat, workdir, timeout, out, grace, workers)
                       for item in enumerate(files, 1)]
            pending = set(futures)
            while pending:
                if workers.stop_code:
                    workers.stop()
                _, pending = cf.wait(pending, timeout=0.1, return_when=cf.FIRST_COMPLETED)
            results = [f.result() for f in futures]
        summary = {"dir": str(out), "seat": seat, "jobs": jobs, "items": results}
        (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"批处理目录：{out}")
        for index, r in enumerate(results, 1):
            mark = "✓" if r["rc"] == 0 else "✗"
            print(f"  {mark} {index:02d} {r['file']} rc={r['rc']} {r['provider'] or '-'} {r['chars']} 字")
        return workers.stop_code or (0 if all(r["rc"] == 0 for r in results) else 1)
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    sys.exit(main())
