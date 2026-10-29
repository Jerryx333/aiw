"""Isolated test-slot regressions; real nice changes only in child processes."""
from __future__ import annotations

import fcntl
from contextlib import nullcontext, redirect_stderr
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
from unittest.mock import patch

from support import REPO, check


class Fixture:
    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.env = dict(os.environ, AIW_SLOT_DIR=str(self.root), AIW_SLOT_QOS="0",
                        AIW_TEST_SLOTS="2", AIW_SLOT_PRESSURE_CMD="/usr/bin/true")
        self.env.pop("AIW_SLOT_HELD", None)
        self.env.pop("AIW_SLOT_THREADS", None)
        for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
                     "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS",
                     "POLARS_MAX_THREADS", "RAYON_NUM_THREADS"):
            self.env.pop(name, None)
        return self

    def __exit__(self, *args):
        self.tmp.cleanup()

    def run(self, *args, **overrides):
        return subprocess.run(["bash", str(REPO / "bin/aiw"), "slot", *args],
                              env=dict(self.env, **overrides), capture_output=True,
                              text=True, timeout=4)

    def lock(self, index=0):
        lock = (self.root / f"slot-{index}.lock").open("a")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return lock


def slot_exit_and_arguments():
    with Fixture() as f:
        result = f.run("--", "sh", "-c", "exit 3")
        check(result.returncode == 3, result.stderr)
        check(not list(f.root.glob("*.json")), "退出后应删除记录")
        check(f.run().returncode == 64, "缺命令应返回 64")
        for value in ("0", "-1", "bad", "1.5"):
            check(f.run("--slots", value, "--", "true").returncode == 64, value)
        check(f.run("--", "true", AIW_TEST_SLOTS="bad").returncode == 64,
              "环境中的名额数也要校验")
        check(f.run("--slots", "1", "--", "true", AIW_TEST_SLOTS="bad").returncode == 0,
              "显式名额数应优先")
        for value in ("-1", "nan", "inf"):
            check(f.run("--max-wait", value, "--", "true").returncode == 64, value)
        result = f.run("--", sys.executable, "-c",
                       "import os,sys; print(os.environ['AIW_SLOT_HELD']); print(sys.argv[1])",
                       "literal $(false); spaced")
        check(result.stdout.splitlines() == ["1", "literal $(false); spaced"], result.stdout)


def slot_queue_timeout():
    with Fixture() as f:
        with f.lock():
            result = f.run("--slots", "1", "--max-wait", "2", "--", "true")
            check(result.returncode == 0, result.stderr)
            check("名额已满（1 个），排队中" in result.stderr, result.stderr)
            check("已超过等待上限，直接运行" in result.stderr, result.stderr)
            check(not list(f.root.glob("*.json")), "超时未拿锁不能发布记录")
        result = f.run("--slots", "1", "--max-wait", "0", "--", "true")
        check(result.returncode == 0 and "排队中" not in result.stderr, result.stderr)


def slot_status_and_stale():
    with Fixture() as f:
        data = dict(pid=os.getpid(), label="held-test", command="test argv",
                    cwd=str(f.root), started="2026-10-14T00:00:00+07:00")
        with f.lock():
            (f.root / "slot-0.json").write_text(json.dumps(data))
            (f.root / "slot-1.json").write_text(json.dumps(dict(data, label="stale")))
            result = f.run("--slots", "1", "--status")
            check(result.returncode == 0, result.stderr)
            check("held-test" in result.stdout and "名额数：1" in result.stdout, result.stdout)
            check("stale" not in result.stdout and not (f.root / "slot-1.json").exists(),
                  "陈旧记录应被清理")
            queued = f.run("--slots", "1", "--max-wait", "0", "--", "true")
            check("held-test" in queued.stderr, "排队需显示持有者摘要")
        result = f.run("--status")
        check("空闲" in result.stdout and not (f.root / "slot-0.json").exists(), result.stdout)


def slot_memory_timeout():
    with Fixture() as f:
        fake = f.root / "pressure.py"
        fake.write_text("print(2)\n")
        pressure = f"{sys.executable} {fake}"
        result = f.run("--max-wait", "0.2", "--", "true", AIW_SLOT_PRESSURE_CMD=pressure)
        check(result.returncode == 0, result.stderr)
        check("内存压力高，等待" in result.stderr, result.stderr)
        check("内存等待已超过等待上限，直接运行" in result.stderr, result.stderr)
        check(not list(f.root.glob("*.json")), "压力超时后退出仍需释放名额")
        result = f.run("--status", AIW_SLOT_PRESSURE_CMD=pressure)
        check("内存压力等级：2" in result.stdout, result.stdout)
        result = f.run("--", "true", AIW_SLOT_PRESSURE_CMD="/no-such-pressure-command")
        check(result.returncode == 0 and "内存压力高" not in result.stderr, result.stderr)


def slot_nested_harness():
    import slot
    harness = sys.modules.get("test_harness", sys.modules["__main__"])
    with Fixture() as f, patch.dict(os.environ, f.env, clear=True):
        with f.lock():
            result = f.run("--slots", "1", "--max-wait", "0", "--", "true",
                           AIW_SLOT_HELD="1")
            check(result.returncode == 0 and "排队中" not in result.stderr, result.stderr)
        with patch.object(slot, "reservation", side_effect=AssertionError("重复抢名额")):
            with patch.dict(os.environ, AIW_SLOT_HELD="1"):
                with harness.suite_reservation([]):
                    pass
            with harness.suite_reservation(["slot_nested_harness"]):
                pass
        with harness.suite_reservation([]):
            check(os.environ.get("AIW_SLOT_HELD") == "1", "全量应标记已取得名额")
            check(len(list(f.root.glob("*.json"))) == 1, "全量应直接复用 reservation")
        check("AIW_SLOT_HELD" not in os.environ, "退出应恢复环境")
        seen = []
        with patch.dict(harness.CASES, {"slot_fixture_probe": lambda: seen.append(
                (os.environ["HOME"], os.environ["AIW_SLOT_DIR"]))}):
            check(harness.run_named(["slot_fixture_probe"], []) == 0, "隔离用例失败")
        check(len(seen) == 1 and seen[0][1] == str(Path(seen[0][0]) / "slots"),
              "每个用例名额目录应位于其临时 HOME")


def slot_shared_deadline_and_release():
    import slot
    with Fixture() as f, patch.dict(os.environ, f.env, clear=True):
        with f.lock(), patch.object(slot.time, "monotonic", side_effect=[0, 0, 2, 2]), \
                patch.object(slot.time, "sleep") as sleep, \
                patch.object(slot, "pressure_level", side_effect=[2, 1]):
            with slot.reservation(["true"], slots=1, max_wait=2):
                pass
            check(sleep.call_args_list == [((2,), {})], "排队和压力必须共用等待预算")
        try:
            with slot.reservation(["true"], slots=1, max_wait=0):
                raise RuntimeError("模拟异常")
        except RuntimeError:
            pass
        with f.lock():
            check(not list(f.root.glob("*.json")), "异常也应清理记录和锁")


def slot_qos_probe_and_fallback():
    import slot
    with patch.dict(os.environ, AIW_SLOT_QOS="bg"), \
            patch.object(slot.sys, "platform", "darwin"), \
            patch.object(slot.Path, "is_file", return_value=True), \
            patch.object(slot.os, "getpriority", return_value=0), \
            patch.object(slot.os, "nice") as nice, \
            patch.object(slot.subprocess, "run") as probe:
        probe.return_value = subprocess.CompletedProcess([], 0, b"", b"")
        error = io.StringIO()
        with redirect_stderr(error):
            check(slot.qos() == ["/usr/sbin/taskpolicy", "-b"], "成功探测应使用 taskpolicy")
        check("主机繁忙时后台 QoS 可能长时间拿不到 CPU，带超时的测试会误判" in
              error.getvalue(), error.getvalue())
        check(probe.call_args.args[0] == ["/usr/sbin/taskpolicy", "-b", "/usr/bin/true"],
              "探测不可执行真实命令")
        nice.assert_not_called()
        check(slot.qos(current=True) == [], "当前进程应应用后台 QoS")
        check(probe.call_args.args[0] == ["/usr/sbin/taskpolicy", "-b", "-p", str(os.getpid())],
              "全量 harness 应调整当前进程")
        probe.return_value = subprocess.CompletedProcess([], 1, b"", b"")
        check(slot.qos() == [], "探测失败应退回 nice")
        nice.assert_called_once_with(10)
        nice.side_effect = PermissionError("sandbox")
        check(slot.qos() == [], "nice 被拒绝应正常运行")
        with patch.dict(os.environ, AIW_SLOT_QOS="0"):
            probe.reset_mock()
            nice.reset_mock()
            check(slot.qos() == [], "调试应禁用调整")
            probe.assert_not_called()
            nice.assert_not_called()


def slot_qos_default():
    import slot
    with Fixture() as f:
        f.env.pop("AIW_SLOT_QOS")
        result = f.run("--", "true")
        check(result.returncode == 0, result.stderr)
        check("nice" in result.stderr and "taskpolicy" not in result.stderr, result.stderr)
        result = f.run("--", "true", AIW_SLOT_QOS="unknown")
        check(result.returncode == 0 and "未知" in result.stderr and "nice" in result.stderr,
              result.stderr)
        with patch.dict(os.environ, f.env, clear=True), \
                patch.object(slot.os, "getpriority", return_value=0) as priority, \
                patch.object(slot.os, "nice") as nice, \
                patch.object(slot.subprocess, "run") as probe:
            for mode in (None, "nice", "unknown"):
                if mode is None:
                    os.environ.pop("AIW_SLOT_QOS", None)
                else:
                    os.environ["AIW_SLOT_QOS"] = mode
                for current in (False, True):
                    nice.reset_mock()
                    with redirect_stderr(io.StringIO()) as error:
                        check(slot.qos(current=current) == [], "默认应使用 nice")
                    nice.assert_called_once_with(10)
                    probe.assert_not_called()
                    check("nice" in error.getvalue() and "taskpolicy" not in error.getvalue(),
                          error.getvalue())
            nice.side_effect = PermissionError("sandbox")
            with redirect_stderr(io.StringIO()) as error:
                slot.qos(current=True)
            check("普通优先级（nice 被拒绝）" in error.getvalue(), error.getvalue())
            for value in (10, 15):
                priority.return_value = value
                nice.reset_mock()
                with redirect_stderr(io.StringIO()) as error:
                    slot.qos()
                nice.assert_not_called()
                check("已是低优先级，未再降" in error.getvalue(), error.getvalue())
            priority.side_effect = PermissionError("sandbox")
            check(slot.lower_to() is False, "读取优先级失败应返回 False")


def slot_nice_no_stacking():
    with Fixture() as f:
        code = ("import os,sys; current=os.getpriority(os.PRIO_PROCESS,0); "
                "os.nice(10-current) if current < 10 else None; "
                "assert os.getpriority(os.PRIO_PROCESS,0) == 10; "
                "os.execvp('bash', ['bash', *sys.argv[1:]])")
        result = subprocess.run(
            [sys.executable, "-c", code, str(REPO / "bin/aiw"), "slot", "--",
             sys.executable, "-c", "import os; print(os.getpriority(os.PRIO_PROCESS,0))"],
            env=dict(f.env, AIW_SLOT_QOS="nice"), capture_output=True, text=True, timeout=4)
        check(result.returncode == 0, result.stderr)
        check(result.stdout.strip() == "10", result.stdout + result.stderr)
        check("已是低优先级，未再降" in result.stderr, result.stderr)


def slot_thread_limits():
    import slot
    with Fixture() as f:
        command = ("--", sys.executable, "-c",
                   f"import os,json; print(json.dumps([os.environ.get(n) for n in {slot.THREAD_VARIABLES!r}]))")
        result = f.run("--", "sh", "-c", "echo $OMP_NUM_THREADS $POLARS_MAX_THREADS")
        check(result.returncode == 0 and result.stdout.strip() == "1 1", result.stderr)
        for value in (None, "3", "0"):
            overrides = {} if value is None else {"AIW_SLOT_THREADS": value}
            result = f.run(*command, **overrides)
            expected = None if value == "0" else value or "1"
            check(result.returncode == 0 and json.loads(result.stdout) == [expected] * 7,
                  result.stdout + result.stderr)
        existing = dict.fromkeys(slot.THREAD_VARIABLES, "4")
        existing["OMP_NUM_THREADS"] = ""
        for value in ("1", "0"):
            result = f.run(*command, AIW_SLOT_THREADS=value, **existing)
            check(result.returncode == 0 and json.loads(result.stdout) == list(existing.values()),
                  "已有变量（包括空值）不能被覆盖：" + result.stdout + result.stderr)
        for value in ("bad", "-1", "1.5", "", " 1", "+1", "١"):
            result = f.run("--", "sh", "-c", "echo launched", AIW_SLOT_THREADS=value)
            check(result.returncode == 64 and not result.stdout, value + result.stderr)


def slot_harness_thread_limits():
    import slot
    harness = sys.modules.get("test_harness", sys.modules["__main__"])
    with Fixture() as f, patch.object(harness, "suite_reservation", return_value=nullcontext()), \
            patch.object(slot, "qos") as qos, \
            patch.object(harness, "run_named", return_value=0) as run_named:
        for value in (None, "2", "0", "bad"):
            with patch.dict(os.environ, f.env, clear=True):
                if value is not None:
                    os.environ["AIW_SLOT_THREADS"] = value
                os.environ["MKL_NUM_THREADS"] = "5"
                run_named.reset_mock()
                qos.reset_mock()
                result = harness.main(["-j", "1"])
                check(result == (64 if value == "bad" else 0), str(result))
                if value == "bad":
                    run_named.assert_not_called()
                    qos.assert_not_called()
                else:
                    expected = None if value == "0" else value or "1"
                    check(os.environ.get("OMP_NUM_THREADS") == expected, "全量应限线程")
                    check(os.environ["MKL_NUM_THREADS"] == "5", "全量应保留已有设置")
                    qos.assert_called_once_with(current=True)


def slot_signal_forwarding():
    with Fixture() as f:
        for sig in (signal.SIGINT, signal.SIGTERM):
            code = ("import signal,sys,time; "
                    "signal.signal(signal.SIGINT, lambda *a: sys.exit(7)); "
                    "signal.signal(signal.SIGTERM, lambda *a: sys.exit(8)); "
                    "print('ready', flush=True); time.sleep(3)")
            process = subprocess.Popen(["bash", str(REPO / "bin/aiw"), "slot", "--",
                                        sys.executable, "-c", code], env=f.env,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                check(process.stdout.readline().strip() == "ready", "子进程未启动")
                process.send_signal(sig)
                _, error = process.communicate(timeout=1)
                check(process.returncode == (7 if sig == signal.SIGINT else 8), error)
                check(not list(f.root.glob("*.json")), "信号退出应释放名额")
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate(timeout=1)


def slot_separate_pools():
    import slot
    with Fixture() as f, patch.dict(os.environ, f.env, clear=True), redirect_stderr(io.StringIO()) as err:
        with f.lock():
            with slot.reservation(["aiw", "codex"], slots=1, max_wait=0, pool="write"):
                check("排队中" not in err.getvalue(), "写任务名额不应被测试名额占住")
                check("AIW_SLOT_HELD" not in os.environ, "写任务名额不应标记测试名额")
                check([p.name for p in f.root.glob("*.json")] == ["write-0.json"], "写任务记录应单独成池")
                with slot.reservation(["aiw", "codex"], slots=1, max_wait=0, pool="write"):
                    check("名额已满（1 个）" in err.getvalue(), err.getvalue())
                with slot.reservation(["true"], slots=2, max_wait=0):
                    check(os.environ.get("AIW_SLOT_HELD") == "1", "测试名额应标记")
        check(not list(f.root.glob("*.json")), "退出应释放名额")


def slot_build_gate():
    # shims/ 在 PATH 最前：构建排名额并限并行编译数，其余子命令原样放行。
    with Fixture() as f:
        fake = f.root / "bin"
        fake.mkdir()
        for name in ("swift", "xcodebuild", "go"):
            tool = fake / name
            tool.write_text('#!/bin/sh\necho "held=${AIW_SLOT_HELD-} $*"\n')
            tool.chmod(0o755)
        path = f"{REPO / 'shims'}:{fake}:/usr/bin:/bin"

        def call(*args, **overrides):
            env = dict(f.env, PATH=path, AIW_BUILD_JOBS="3", **overrides)
            return subprocess.run([str(REPO / "shims" / args[0]), *args[1:]], env=env,
                                  capture_output=True, text=True, timeout=4)

        for args, expected in (
                (("swift", "build", "-c", "release"), "held=1 build --jobs 3 -c release"),
                (("swift", "test", "--filter", "X"), "held=1 test --jobs 3 --filter X"),
                (("swift", "build", "-j", "8"), "held=1 build -j 8"),
                (("swift", "run", "App"), "held= run App"),
                (("swift", "--version"), "held= --version"),
                (("xcodebuild", "-scheme", "A", "build"), "held=1 -jobs 3 -scheme A build"),
                (("xcodebuild", "-version"), "held= -version"),
                (("go", "build", "./..."), "held=1 build -p 3 ./..."),
                (("go", "test", "-p", "2", "./..."), "held=1 test -p 2 ./..."),
                (("go", "test", "-p=1", "-run", "X"), "held=1 test -p=1 -run X"),
                (("go", "vet", "./..."), "held=1 vet -p 3 ./..."),
                (("go", "tool", "mygo", "build", "-skip-dmg"), "held=1 tool mygo build -skip-dmg"),
                (("go", "tool", "mygo", "dev"), "held= tool mygo dev"),
                (("go", "run", "."), "held= run ."),
                (("go", "version"), "held= version"),
                (("go", "env", "GOPATH"), "held= env GOPATH")):
            result = call(*args)
            check(result.returncode == 0 and result.stdout.strip() == expected,
                  f"{args}: {result.stdout!r} {result.stderr!r}")
        result = call("swift", "build", AIW_SLOT_HELD="1")
        check(result.stdout.strip() == "held=1 build --jobs 3", "已持有名额不再排队，仍限并行")
        result = call("swift", "build", AIW_BUILD_GATE="0")
        check(result.stdout.strip() == "held= build", "AIW_BUILD_GATE=0 原样放行")
        check(not list(f.root.glob("*.json")), "退出后应删除记录")
        missing = subprocess.run([str(REPO / "shims" / "swift"), "build"], capture_output=True, text=True,
                                 env=dict(f.env, PATH=f"{REPO / 'shims'}:/nonexistent"), timeout=4)
        check(missing.returncode == 127, "找不到真命令时不能调回自己")


def slot_build_gate_path():
    # path.zsh 只在 agent 会话里生效：把 shims 挪到最前，导出 AIW_SHIMS，并用 GOFLAGS 给绕过 shims 的 go 限并行。
    script = REPO / "shims" / "path.zsh"
    def source(**env):
        base = {"HOME": os.environ.get("HOME", "/tmp"), "PATH": "/usr/bin:/bin", "AIW_BUILD_JOBS": "3"}
        result = subprocess.run(["zsh", "-fc", f'source "{script}"; print -r -- "$PATH|${{AIW_SHIMS-}}|${{GOFLAGS-}}"'],
                                env=dict(base, **env), capture_output=True, text=True, timeout=4)
        check(result.returncode in (0, 1), result.stderr)
        return result.stdout.strip().split("|")
    shims = str((REPO / "shims").resolve())
    path, exported, goflags = source(CLAUDECODE="1")
    check(path.split(":")[0] == shims and exported == shims and goflags == "-p=3", (path, exported, goflags))
    check(source(AIW_NESTED="1", GOFLAGS="-mod=mod")[2] == "-mod=mod -p=3", "已有 GOFLAGS 时追加")
    check(source(CLAUDECODE="1", GOFLAGS="-p=2")[2] == "-p=2", "已有 -p 时不改")
    path, exported, goflags = source(PATH=f"/usr/bin:{shims}:/bin", CLAUDECODE="1")
    check(path == f"{shims}:/usr/bin:/bin", f"挪到最前且不重复：{path}")
    check(source() == ["/usr/bin:/bin", "", ""], "主人终端不动")


CASES = {name: value for name, value in globals().copy().items()
         if name.startswith("slot_") and callable(value)}
