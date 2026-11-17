"""background dispatch and completion notice cases (issue #63)."""
from __future__ import annotations

from support import (
    Path,
    REPO,
    check,
    fake_agent_use,
    json,
    os,
    sh,
    sys,
    tempfile,
    wait_until,
)

FAKE_OCS = r'''#!/usr/bin/env python3
import json, os, sys
log = os.environ["FAKE_OCS_LOG"]
if sys.argv[1:3] == ["whoami", "--json"]:
    if os.environ.get("FAKE_OCS_WHOAMI") == "fail":
        sys.exit(1)
    print(json.dumps({"host": "pi", "id": os.environ.get("FAKE_OCS_ID", "pi-1a2b3c4d"), "name": "renamable",
                      "addresses": ["renamable", "pi-1a2b3c4d"]}))
    sys.exit(0)
open(log, "a").write(json.dumps(sys.argv[1:], ensure_ascii=False) + "\n")
sys.exit(int(os.environ.get("FAKE_OCS_DM_EXIT", "0")))
'''


def fixture(t: Path, **extra):
    fb = t / "ocsbin"
    fb.mkdir()
    (fb / "ocs").write_text(FAKE_OCS)
    (fb / "ocs").chmod(0o755)
    wd = t / "work dir"
    wd.mkdir()
    prompt = t / "prompt.md"
    prompt.write_text("测试")
    env = dict(os.environ, AGENT_USE_HOME=str(fake_agent_use(t)), AIW_STATE=str(t / "state"),
               AIW_OCS_BIN=str(fb / "ocs"), FAKE_OCS_LOG=str(t / "ocs.log"), **extra)
    env.pop("AIW_NESTED", None)
    return env, wd, prompt


def aiw(*args, env, input=None, cwd=None):
    return sh(["bash", str(REPO / "bin/aiw"), *args], env=env, input=input, cwd=cwd)


def job_id(stdout: str) -> str:
    return next(line.split(": ", 1)[1] for line in stdout.splitlines() if line.startswith("任务编号: "))


def job(t: Path, ident: str) -> dict:
    return json.loads((t / "state/bg" / ident / "job.json").read_text())


def notices(t: Path) -> list[list[str]]:
    log = t / "ocs.log"
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def finished(t, ident):
    """等监工进程退出（它发完通知还会再写一次 job.json），再读最终记录。"""
    sys.path.insert(0, str(REPO))
    from background import supervised

    def done():
        data = job(t, ident)
        return data if data["state"] != "running" and not supervised(t / "state/bg" / ident) else None
    return wait_until(done, f"{ident} 没结束", timeout=90)


def background_success_and_parallel():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        env, wd, prompt = fixture(t, FAKE_AU_QODER="brief")
        a = aiw("qoder", str(wd), "30", str(prompt), "--ro", "--background", env=env)
        b = aiw("qoder", str(wd), "30", "-", "--background", "--ro", env=env, input="STDIN_MARKER")
        check(a.returncode == 0 and b.returncode == 0, a.stderr + b.stderr)
        check("会通知本会话 pi-1a2b3c4d" in a.stdout and "不用轮询" in a.stdout, a.stdout)
        first, second = job_id(a.stdout), job_id(b.stdout)
        check(first != second, "两单编号重复")
        # 立即返回：假执行方要 2 秒，返回时还在跑。
        check(job(t, first)["state"] == "running", "后台模式没有立即返回")
        r = aiw("bg", "status", first, env=env)
        check(r.returncode == 0 and "状态: running" in r.stdout, "刚启动不应误报 lost: " + r.stdout)
        r = aiw("bg", "supervise", first, "0", env=env)
        check(r.returncode == 64, "不带监工锁不能手动再起监工")
        r = aiw("bg", "result", first, env=env)
        check(r.returncode == 75 and "还在跑" in r.stderr, "运行中取结果应返回 75")
        for ident in (first, second):
            data = finished(t, ident)
            check(data["state"] == "succeeded" and data["rc"] == 0 and data["notify"]["exit"] == 0, str(data))
            check(data["result"].endswith("output.md") and Path(data["result"]).exists(), str(data))
        sent = notices(t)
        check(len(sent) == 2, f"每单应只通知一次: {sent}")
        for ident in (first, second):
            mine = [n for n in sent if ident in n[2]]
            check(len(mine) == 1 and mine[0][:2] == ["dm", "pi-1a2b3c4d"] and "--as" in mine[0], f"通知没按编号对上: {sent}")
            check("状态: succeeded（退出码 0）" in mine[0][2] and f"aiw bg result {ident}" in mine[0][2], mine[0][2])
            check("qoder ok" not in mine[0][2], "通知里不应带执行方输出")
        r = aiw("bg", "result", first, env=env)
        check(r.returncode == 0 and "qoder ok" in r.stdout, r.stdout + r.stderr)
        task = (Path(job(t, second)["run_dir"]) / "task.txt").read_text()
        check("STDIN_MARKER" in task, "标准输入 prompt 没传到后台执行")
        r = aiw("bg", "status", second, env=env)
        check(r.returncode == 0 and "已通知 pi-1a2b3c4d" in r.stdout, r.stdout)
        r = aiw("bg", "list", env=env)
        check(first in r.stdout and second in r.stdout, r.stdout)


def background_failure_timeout():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        env, wd, prompt = fixture(t, FAKE_AU_QODER="weird")
        r = aiw("qoder", str(wd), "30", str(prompt), "--ro", "--no-fallback", "--background", env=env)
        data = finished(t, job_id(r.stdout))
        check(data["state"] == "failed" and data["rc"] not in (0, 124), str(data))
        check(f"状态: failed（退出码 {data['rc']}）" in notices(t)[-1][2], str(notices(t)))
        r = aiw("bg", "result", data["id"], env=env)
        check(r.returncode == data["rc"], "result 应返回原退出码")
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        env, wd, prompt = fixture(t, FAKE_AU_QODER="slow")
        r = aiw("qoder", str(wd), "1", str(prompt), "--ro", "--no-fallback", "--background", env=env)
        data = finished(t, job_id(r.stdout))
        check(data["state"] == "timeout" and data["rc"] == 124, str(data))
        check("状态: timeout（退出码 124）" in notices(t)[-1][2], str(notices(t)))


def background_no_notify_and_guards():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        env, wd, prompt = fixture(t, FAKE_AU_QODER="ok", FAKE_OCS_WHOAMI="fail")
        r = aiw("qoder", str(wd), "30", str(prompt), "--ro", "--background", env=env)
        check(r.returncode == 0 and "完成后不会自动回报" in r.stdout, r.stdout)
        data = finished(t, job_id(r.stdout))
        check(data["state"] == "succeeded" and not notices(t), "没有会话地址时不应发通知")
        env["AIW_OCS_BIN"] = str(t / "missing-ocs")
        r = aiw("qoder", str(wd), "30", str(prompt), "--ro", "--background", env=env)
        check(r.returncode == 0 and "本机没有 ocs" in r.stdout and "不会自动回报" in r.stdout, r.stdout)
        finished(t, job_id(r.stdout))
        r = aiw("qoder", str(wd), "30", str(prompt), "--ro", "--background", "--no-notify", env=env)
        check(r.returncode == 0 and "--no-notify" in r.stdout and "不会自动回报" in r.stdout, r.stdout)
        finished(t, job_id(r.stdout))
        r = aiw("qoder", str(wd), "30", str(prompt), "--background", env=dict(env, AIW_NESTED="1"))
        check(r.returncode == 64 and "不能后台派单" in r.stderr, r.stderr)
        for bad in (["qoder", str(wd), "30", "--background"], ["qoder", str(wd), "30", str(prompt), "--bogus", "--background"],
                    ["qoder", str(wd), "bad", str(prompt), "--background"], ["qoder", str(wd), "0", str(prompt), "--background"],
                    ["review", str(wd), "30", str(prompt), "--verify", "true", "--background"],
                    ["qoder", str(wd), "30", str(prompt), "--ro", "--worktree", "--background"],
                    ["qoder", str(t / "missing"), "30", str(prompt), "--background"],
                    ["qoder", str(wd), "30", str(t / "missing.md"), "--background"]):
            r = aiw(*bad, env=env)
            check(r.returncode == 64, f"用法错误应立即返回 64: {bad} {r.stdout}{r.stderr}")
        r = aiw("qoder", str(wd), "30", "-", "--background", env=env, input=" \n")
        check(r.returncode == 64 and "是空的" in r.stderr, r.stderr)
        r = aiw("bg", "status", "../etc", env=env)
        check(r.returncode == 64, "非法编号应拒绝")
        before = sorted((t / "state/bg").iterdir())
        check(len(before) == 3, f"用法错误不应建后台任务目录: {before}")


def background_lost_supervisor():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        env = dict(os.environ, AIW_STATE=str(t / "state"))
        folder = t / "state/bg/bg-20260101-000000-abcdef"
        folder.mkdir(parents=True)
        # 记录的进程号还活着（被复用成别的进程）也要判 lost：判活看监工锁，不看进程号。
        (folder / "job.json").write_text(json.dumps({"id": folder.name, "argv": ["qoder"], "state": "running",
                                                     "supervisor_pid": os.getpid(), "notify": {}}))
        (folder / "supervisor.lock").write_text("")
        r = sh([sys.executable, str(REPO / "background.py"), "status", folder.name], env=env)
        check(r.returncode == 0 and "状态: lost" in r.stdout, r.stdout + r.stderr)
        r = sh([sys.executable, str(REPO / "background.py"), "result", folder.name], env=env)
        check(r.returncode == 1 and "Traceback" not in r.stderr, "lost 且没有日志时 result 不应崩: " + r.stderr)
        lock = os.open(folder / "supervisor.lock", os.O_RDWR)
        r = sh([sys.executable, str(REPO / "background.py"), "supervise", folder.name, str(lock)],
               env=dict(env, AIW_NESTED="1"))
        os.close(lock)
        check(r.returncode == 64 and not (folder / "stdout.log").exists(), "被外包的会话不能起监工")


def background_codex_no_net():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        args = t / "args"
        env, wd, prompt = fixture(t, FAKE_CODEX_ARGS=str(args))
        r = aiw("codex", str(wd), "30", str(prompt), "--no-net", "--no-fallback",
                "--background", "--no-notify", env=env)
        check(r.returncode == 0, r.stdout + r.stderr)
        data = finished(t, job_id(r.stdout))
        check(data["state"] == "succeeded", str(data))
        check('sandbox_workspace_write.network_access=' not in args.read_text(), args.read_text())
        row = json.loads((t / 'state/seat-ledger.jsonl').read_text().splitlines()[-1])
        task = (Path(row['run_dir']) / 'task.txt').read_text()
        check('本单不能联网、不能监听本地端口（--no-net）' in task, task)


def background_address_and_result_path():
    sys.path.insert(0, str(REPO))
    import background as b
    from unittest.mock import patch
    import subprocess as sp
    reply = sp.CompletedProcess([], 0, json.dumps({"name": "renamable", "addresses": ["renamable"]}), "")
    with patch.object(b.subprocess, "run", return_value=reply):
        address, reason = b.resolve_address()
    check(address is None and "固定编号" in reason, "没有固定编号时不能退回可改名的名字")
    path = "/tmp/state（测试）/runs/r/output.md"
    check(b.run_dir_from("[ai-worker] 全文：" + path) == path, "路径里的全角括号被截断")
    check(b.run_dir_from("[ai-worker] 排查：/tmp/r（output.md 没写成功，全文只在上面的输出里）") == "/tmp/r", "附注没去掉")


CASES = {case.__name__: case for case in (
    background_success_and_parallel,
    background_failure_timeout,
    background_no_notify_and_guards,
    background_lost_supervisor,
    background_address_and_result_path,
    background_codex_no_net,
)}
