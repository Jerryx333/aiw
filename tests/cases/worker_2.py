"""worker 2 regression cases."""
from __future__ import annotations

from support import (
    inprocess_sh,
    git_template,
    FAKE_AGENT_USE,
    FINDINGS_BLOCK,
    Path,
    REPO,
    check,
    fake_agent_use,
    fake_home,
    free_guard_fixture,
    json,
    os,
    sh,
    subprocess,
    sys,
    tempfile,
    worker_call,
    wait_until,
)


def executor_watchdog():
    """Real aiw death and all orderly exits leave no executor/watchdog behind."""
    import signal
    from concurrent.futures import ThreadPoolExecutor

    def exited(pid):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        return False

    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        cli = t / 'fake-cli'
        cli.write_text(
            '#!' + sys.executable + '\n'
            "import os, signal, time\nfrom pathlib import Path\n"
            "Path(os.environ['EXECUTOR_PID']).write_text(str(os.getpid()))\n"
            "if os.environ['WATCHDOG_MODE'] == 'stubborn':\n"
            "    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "    Path(os.environ['EXECUTOR_READY']).touch()\n"
            "while not Path(os.environ['EXECUTOR_RELEASE']).exists(): time.sleep(.02)\n"
            + "print('executor ok')\n")
        cli.chmod(0o755)
        wrapper = t / 'wrapper.py'
        wrapper.write_text('''import os, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv.pop(1))
import ai_worker as w
w.WATCHDOG_GRACE = 0.3
original_popen = w.subprocess.Popen
watchdogs = []
def popen(argv, **kw):
    is_watchdog = argv[:2] == [sys.executable, '-c'] and argv[2] == w.WATCHDOG_CODE
    if is_watchdog and os.environ['WATCHDOG_MODE'] == 'failed':
        raise OSError('simulated watchdog spawn failure')
    is_executor = argv[0] == os.environ['CODEX_BIN']
    p = original_popen(argv, **kw)
    if is_executor and os.environ['WATCHDOG_MODE'] == 'timeout':
        original_communicate = p.communicate
        first_wait = True
        def communicate(*args, **kwargs):
            nonlocal first_wait
            if first_wait:
                first_wait = False
                deadline = time.monotonic() + 30
                while not Path(os.environ['TIMEOUT_TRIGGER']).exists():
                    if time.monotonic() >= deadline:
                        raise AssertionError('超时握手未到达')
                    time.sleep(.02)
                kwargs['timeout'] = .01
            return original_communicate(*args, **kwargs)
        p.communicate = communicate
    if is_watchdog:
        assert w.DEFERRED_DEPTH > 0, '看门狗启动未延迟信号'
        assert not os.get_inheritable(kw['stdin']), '管道读端可被继承'
        watchdogs.append(p)
        Path(os.environ['WATCHDOG_PID']).write_text(str(p.pid))
    return p
w.subprocess.Popen = popen
try:
    sys.exit(w.main())
finally:
    for p in watchdogs:
        assert p.returncode is not None, '看门狗未 wait 回收'
        try:
            os.waitpid(p.pid, os.WNOHANG)
        except ChildProcessError:
            pass
        else:
            raise AssertionError('看门狗仍是未回收子进程')
    Path(os.environ['WATCHDOG_REAPED']).touch()
''')
        def run_mode(mode):
            case = t / mode
            case.mkdir()
            executor_pid, watchdog_pid = case / 'executor.pid', case / 'watchdog.pid'
            release, reaped = case / 'release', case / 'reaped'
            cmd, env, _ = worker_call(
                case, CODEX_BIN=str(cli), AIW_STATE=str(case / 'state'),
                WATCHDOG_MODE=mode,
                TIMEOUT_TRIGGER=str(case / 'timeout-trigger'),
                EXECUTOR_PID=str(executor_pid), WATCHDOG_PID=str(watchdog_pid),
                EXECUTOR_RELEASE=str(release), WATCHDOG_REAPED=str(reaped))
            cmd[2] = 'codex'
            cmd[4] = '120'
            cmd += ['--no-fallback', '--no-findings']
            with (case / 'log').open('w+') as log:
                proc = subprocess.Popen(
                    [sys.executable, str(wrapper), str(REPO), *cmd[2:]],
                    env=env, stdout=log, stderr=log)
                try:
                    wait_until(lambda: executor_pid.exists() and executor_pid.read_text(),
                               '执行方未就绪', timeout=30)
                    if mode != 'failed':
                        wait_until(lambda: watchdog_pid.exists() and watchdog_pid.read_text(),
                                   '看门狗未就绪', timeout=30)
                    if mode == 'killed':
                        proc.kill()
                        wait_until(lambda: exited(int(executor_pid.read_text())),
                                   'aiw SIGKILL 后执行方仍存活', timeout=30)
                    elif mode == 'timeout':
                        (case / 'timeout-trigger').touch()
                    elif mode == 'interrupted':
                        proc.send_signal(signal.SIGTERM)
                    elif mode in ('normal', 'failed'):
                        release.touch()
                    wait_until(lambda: proc.poll() is not None,
                               f'{mode} aiw 未退出', timeout=30)
                    log.seek(0)
                    output = log.read()
                    expected = -signal.SIGKILL if mode == 'killed' else 124 if mode in ('timeout', 'interrupted') else 0
                    check(proc.returncode == expected, f'{mode} 退出码错误: {proc.returncode} {output}')
                    if mode != 'failed':
                        wait_until(lambda: exited(int(watchdog_pid.read_text())),
                                   f'{mode} 看门狗仍存活', timeout=30)
                    if mode != 'killed':
                        check(reaped.exists(), f'{mode} 看门狗未回收: {output}')
                    if mode == 'failed':
                        check(output.count('警告：执行方看门狗启动失败') == 1,
                              '看门狗启动失败未警告一次: ' + output)
                finally:
                    if proc.poll() is None:
                        proc.kill()
                    wait_until(lambda: proc.poll() is not None,
                               '测试清理 aiw 未退出', timeout=30)
                    # PID publication is the executor's first action after imports.
                    wait_until(lambda: executor_pid.exists() and executor_pid.read_text(),
                               '测试清理执行方未发布 PID', timeout=30)
                    for pidfile in (executor_pid, watchdog_pid):
                        if pidfile.exists() and pidfile.read_text():
                            pid = int(pidfile.read_text())
                            try:
                                os.killpg(pid, signal.SIGKILL)
                            except (ProcessLookupError, PermissionError):
                                pass
                            wait_until(lambda: exited(pid), '测试清理残留进程', timeout=30)

        def stubborn_executor():
            """Own the executor so its wait status proves SIGKILL escalation."""
            import ai_worker as w
            from unittest.mock import patch

            case = t / 'stubborn'
            case.mkdir()
            pidfile = case / 'executor.pid'
            env = dict(os.environ, WATCHDOG_MODE='stubborn',
                       EXECUTOR_PID=str(pidfile), EXECUTOR_RELEASE=str(case / 'release'),
                       EXECUTOR_READY=str(case / 'ready'))
            executor = subprocess.Popen([str(cli)], env=env, start_new_session=True,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            reader = writer = watchdog = None
            try:
                wait_until(lambda: pidfile.exists() and pidfile.read_text() and (case / 'ready').exists(),
                           '忽略 SIGTERM 的执行方未就绪', timeout=30)
                reader, writer = os.pipe()
                with patch.object(w, 'WATCHDOG_GRACE', 0.3):
                    watchdog = subprocess.Popen(
                        [sys.executable, '-c', w.WATCHDOG_CODE,
                         str(executor.pid), str(w.WATCHDOG_GRACE), str(w.HERE)],
                        stdin=reader, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                os.close(reader)
                reader = None
                os.close(writer)  # Simulate aiw dying without acknowledgement.
                writer = None
                wait_until(lambda: executor.poll() is not None,
                           '忽略 SIGTERM 的执行方未被 SIGKILL', timeout=30)
                check(executor.returncode == -signal.SIGKILL,
                      f'忽略 SIGTERM 的执行方退出码错误: {executor.returncode}')
                wait_until(lambda: watchdog.poll() is not None,
                           'SIGKILL 后看门狗未退出', timeout=30)
                check(watchdog.returncode == 0, 'SIGKILL 看门狗退出码错误')
            finally:
                for fd in (reader, writer):
                    if fd is not None:
                        os.close(fd)
                for proc in (executor, watchdog):
                    if proc is not None:
                        if proc.poll() is None:
                            proc.kill()
                        wait_until(lambda: proc.poll() is not None,
                                   'SIGKILL 子用例清理失败', timeout=30)

        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(run_mode, mode) for mode in
                       ('killed', 'normal', 'timeout', 'interrupted', 'failed')]
            futures.append(pool.submit(stubborn_executor))
            for future in futures:
                future.result()


def inflight_liveness_lock():
    import fcntl
    import signal
    import time
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        import importlib.util
        from unittest.mock import patch
        spec = importlib.util.spec_from_file_location("worker_lock_test", REPO / "ai_worker.py")
        worker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(worker)
        stale = t / "stale.lock"
        stale.touch()
        stale.chmod(0o444)
        held = worker.acquire_liveness_lock(stale)
        check(held is not None, "只读残留锁未重建")
        try:
            with stale.open("r") as other:
                try:
                    fcntl.flock(other, fcntl.LOCK_SH | fcntl.LOCK_NB)
                except BlockingIOError:
                    pass
                else:
                    raise AssertionError("重建锁未被持有")
        finally:
            held.close()
        with patch.object(worker, "acquire_lock", side_effect=OSError("模拟拿锁失败")):
            check(worker.acquire_liveness_lock(stale) is None, "拿锁失败应返回 None")
        check(not stale.exists(), "拿锁失败留下假锁文件")

        backend = fake_agent_use(t)
        pidfile, release = t / 'seat.pid', t / 'release'
        (backend / 'scripts/run_agent.py').write_text(
            "import os, time\nfrom pathlib import Path\n"
            "Path(os.environ['SEAT_PID']).write_text(str(os.getpid()))\n"
            "while not Path(os.environ['SEAT_RELEASE']).exists(): time.sleep(.02)\n"
            + FAKE_AGENT_USE)
        cmd, env, _ = worker_call(t, AGENT_USE_HOME=str(backend),
                                  SEAT_PID=str(pidfile), SEAT_RELEASE=str(release))
        for killed in (False, True):
            pidfile.unlink(missing_ok=True)
            release.unlink(missing_ok=True)
            p = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            path = Path(env['AIW_STATE']) / 'inflight' / f'{p.pid}.json'
            lock = path.with_suffix('.lock')
            try:
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline and p.poll() is None:
                    if pidfile.exists() and pidfile.read_text() and path.exists():
                        break
                    time.sleep(.02)
                check(path.exists() and lock.exists() and pidfile.exists(), '在途文件或座位进程缺失')
                with lock.open('r') as fd:
                    try:
                        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
                    except BlockingIOError:
                        pass
                    else:
                        raise AssertionError('运行中未持有 inflight 排他锁')
                if killed:
                    p.kill()
                    p.wait(timeout=5)
                    # 看门狗可能已清理座位；锁必须随 worker 释放。
                    check(path.exists() and lock.exists(), 'SIGKILL 未留下状态文件')
                    with lock.open('r') as fd:
                        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
                        fcntl.flock(fd, fcntl.LOCK_UN)
                else:
                    release.touch()
                    out, err = p.communicate(timeout=10)
                    check(p.returncode == 0, out + err)
                    check(not path.exists() and not lock.exists(), '正常结束残留 inflight 文件')
            finally:
                if p.poll() is None:
                    p.kill()
                    p.wait(timeout=5)
                if pidfile.exists() and pidfile.read_text():
                    try:
                        os.killpg(int(pidfile.read_text()), signal.SIGKILL)
                    except (ProcessLookupError, PermissionError):
                        pass
                p.communicate(timeout=5)
                path.unlink(missing_ok=True)
                lock.unlink(missing_ok=True)

        inflight = Path(env['AIW_STATE']) / 'inflight'
        inflight.chmod(0o500)
        try:
            release.touch()
            r = sh(cmd, env=env)
            check(r.returncode == 0, r.stdout + r.stderr)
            check('警告：获取 inflight 锁失败' in r.stderr and '警告：写 inflight 失败' in r.stderr,
                  '目录不可写未产生预期警告')
            check('Traceback' not in r.stderr, r.stderr)
        finally:
            inflight.chmod(0o700)



def test_inflight_lifecycle():
    import datetime as dt
    import time
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        cmd, env, wd = worker_call(t, FAKE_AU_QODER='brief', CLAUDE_CODE_SESSION_ID='board-session')
        p = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        path = Path(env['AIW_STATE']) / 'inflight' / f'{p.pid}.json'
        try:
            deadline = time.monotonic() + 8
            while not path.exists() and p.poll() is None and time.monotonic() < deadline:
                time.sleep(0.02)
            check(path.exists(), "worker 未及时创建 inflight")
            row = json.loads(path.read_text())
            fields = {'pid', 'host', 'session', 'claude_pid', 'seat', 'actual', 'model', 'workdir',
                      'prompt', 'ro', 'timeout', 'started', 'panel', 'nested'}
            check(fields <= row.keys(), f'字段缺失: {row}')
            check(row['seat'] == row['actual'] == 'qoder' and row['session'] == 'board-session', '座位/会话错误')
            check(row['timeout'] == 10 and dt.datetime.fromisoformat(row['started']).tzinfo is not None, '时间格式错误')
            out, err = p.communicate(timeout=10)
            check(p.returncode == 0, out + err)
            check(not path.exists(), '完成后仍有 inflight')
            rows = [json.loads(x) for x in (path.parent.parent / 'seat-ledger.jsonl').read_text().splitlines()]
            check(rows[-1]['session'] == 'board-session', '账本缺 session')
        finally:
            if p.poll() is None:
                p.terminate()
                p.communicate(timeout=10)



def test_inflight_removed_on_failure():
    with tempfile.TemporaryDirectory() as td:
        cmd, env, wd = worker_call(Path(td), FAKE_AU_QODER='auth')
        r = sh(cmd + ['--no-fallback'], env=env)
        check(r.returncode == 1, r.stderr)
        check(not list((Path(env['AIW_STATE']) / 'inflight').glob('*.json')), '失败后 inflight 残留')
        inflight = Path(env['AIW_STATE']) / 'inflight'
        inflight.rmdir(); inflight.write_text('not a directory')
        cmd[2] = 'workbuddy'
        r = sh(cmd + ['--no-fallback'], env=env)
        check(r.returncode == 0 and '警告：写 inflight 失败' in r.stderr, 'inflight 写失败影响派单')



def write_fallback_text_seat():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        home, env = fake_home(t)
        repo = home / '.claude/aiw'
        config = repo / 'seats.json'
        data = json.loads(config.read_text())
        data['fallback']['codex'] = ['qoder']
        config.write_text(json.dumps(data))
        work = t / 'work'; work.mkdir()
        prompt = t / 'prompt'; prompt.write_text('修改文件')
        log = t / 'agent-calls'
        env.update(FAKE_CODEX='quota', FAKE_AU_LOG=str(log))
        r = inprocess_sh([sys.executable, str(repo / 'ai_worker.py'), 'codex', str(work),
                '10', str(prompt)], env=env)
        check(r.returncode == 1, f'写任务不能由文本座位伪成功: {r.stdout}{r.stderr}')
        check(not log.exists(), '写任务调用了文本后端')
        check(not (work / '.provider').exists(), '写任务不应记录文本座位成功')
        rows = [json.loads(x) for x in (t / 'state/seat-ledger.jsonl').read_text().splitlines()]
        check(len(rows) == 1 and rows[0]['actual'] == 'codex' and rows[0]['kind'] == 'quota',
              f'写任务不应尝试文本顶替: {rows}')



def generic_adapter_reasons():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); env = free_guard_fixture(t)
        local = t / 'generic.json'
        local.write_text(json.dumps({
            'seats': {'custom': {'kind': 'agent_use', 'provider': 'generic', 'model': 'test', 'free_guard': True}},
            'fallback': {'custom': ['claude']}}))
        env['AIW_CONFIG'] = str(local)
        prompt = t / 'prompt'; prompt.write_text('test')
        for reason, hint in (('outside_free_window', '不在免费时段'), ('promotion_expired', '免费活动已到期')):
            r = inprocess_sh([sys.executable, str(REPO / 'ai_worker.py'), 'custom', str(t), '10', str(prompt), '--ro'],
                   env=dict(env, FAKE_AU_GENERIC=reason))
            check(r.returncode == 0 and 'custom ' + hint in r.stderr and '已顶替' in r.stderr, repr(r))
            r = inprocess_sh([sys.executable, str(REPO / 'freecheck.py')], env=dict(env, FAKE_AU_GENERIC=reason))
            check(r.returncode == 0 and 'custom:' in r.stdout and hint in r.stdout, repr(r))



def repository_identity_contract():
    from unittest.mock import patch
    import findings
    from gitenv import repository_git_env

    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        home, env = fake_home(t)
        harness = home / '.claude/aiw'
        settings = json.loads((harness / 'seats.json').read_text())
        settings['findings'] = {'collect': True}
        (harness / 'seats.json').write_text(json.dumps(settings))
        prompt, output = t / 'prompt', t / 'output'
        prompt.write_text('test')
        output.write_text('正文\n' + FINDINGS_BLOCK + '\n')
        env.update(FAKE_CODEX='findings', FAKE_FINDINGS_OUTPUT=str(output))
        for key in ('AIW_PANEL_DIR', 'AIW_MAP_DIR'):
            env.pop(key, None)
        clean_env = repository_git_env()
        repo, linked, other, plain = (t / name for name in ('repo', 'linked', 'other', 'plain'))
        plain.mkdir()

        def git(workdir, *args):
            result = sh(['git', '-C', str(workdir), *args], env=clean_env)
            check(result.returncode == 0, result.stderr)
            return result.stdout.strip()

        for path, remote in ((repo, 'git@github.com:Owner/Name.git'),
                             (other, 'git@github.com:Wrong/Repo.git')):
            path.mkdir()
            git_template(path)
            git(path, 'remote', 'add', 'origin', remote)
        git(repo, 'worktree', 'add', '-qb', 'linked', str(linked))
        sub, linked_sub = repo / 'sub', linked / 'sub'
        sub.mkdir()
        linked_sub.mkdir()
        identity = 'github.com/owner/name'
        polluted = dict(GIT_DIR=str(other / '.git'), GIT_WORK_TREE=str(other))

        def verify(workdir, root, expected, locators=None):
            with patch.dict(os.environ, locators or {}):
                actual = findings.repository_identity(str(workdir))
            check(actual == (str(root.resolve()) if root else None, expected), repr(actual))
            result = inprocess_sh([sys.executable, str(harness / 'ai_worker.py'), 'codex', str(workdir),
                         '10', str(prompt), '--ro', '--no-fallback'], env=dict(env, **(locators or {})))
            check(result.returncode == 0, result.stderr)
            ledger = json.loads((t / 'state/seat-ledger.jsonl').read_text().splitlines()[-1])
            records = [json.loads(line) for line in (t / 'state/findings/records.jsonl').read_text().splitlines()]
            observations = [row for row in records if row['run_dir'] == ledger['run_dir']
                            and row['type'] == 'observation']
            check(observations and ledger['repo'] == expected
                  and all(row['repo'] == ledger['repo'] for row in observations),
                  repr((ledger, observations)))
            fallback = str(workdir.resolve()) if expected is None else None
            check(ledger['workdir'] == fallback and all(row['workdir'] == fallback for row in observations),
                  'workdir fallback changed')
            check(all(row['repo_rel'] == (os.path.relpath(workdir.resolve(), root.resolve()) if root else None)
                      for row in observations), 'repo_rel changed')

        verify(sub, repo, identity)
        verify(linked_sub, linked, identity)
        verify(sub, repo, identity, polluted)
        verify(linked_sub, linked, identity, polluted)
        verify(plain, None, None, polluted)
        for remote in ('https://github.com/Owner/Name.git', 'ssh://git@github.com/Owner/Name'):
            git(repo, 'remote', 'set-url', 'origin', remote)
            verify(sub, repo, identity)
        git(repo, 'remote', 'remove', 'origin')
        verify(sub, repo, None)
        for remote in ('not-a-remote', 'https://[invalid/Owner/Name.git'):
            git(repo, 'remote', 'add', 'origin', remote)
            verify(sub, repo, None)
            git(repo, 'remote', 'remove', 'origin')
        verify(plain, None, None)
        # Root failure suppresses the origin query; origin command failure retains the root.
        with patch.object(findings.subprocess, 'run', side_effect=OSError('git unavailable')) as run:
            check(findings.repository_identity(str(sub)) == (None, None), 'root failure fallback')
            check(run.call_count == 1, 'origin queried without a root')
        root_result = subprocess.CompletedProcess([], 0, str(repo.resolve()) + '\n', '')
        with patch.object(findings.subprocess, 'run', side_effect=[root_result, subprocess.TimeoutExpired('git', 10)]):
            check(findings.repository_identity(str(sub)) == (str(repo.resolve()), None), 'origin failure fallback')




def write_verify():
    import shlex
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        cmd, env, wd = worker_call(t, AIW_STATE=str(t / 'state'), FAKE_CODEX='ok')
        cmd[2] = 'codex'
        cmd.remove('--ro')
        cmd += ['--no-fallback', '--no-findings']
        def run(*flags, **extra):
            (t / 'state/breaker.json').unlink(missing_ok=True)
            result = inprocess_sh([*cmd, *flags], env=dict(env, **extra))
            check((t / 'state/seat-ledger.jsonl').exists(), repr(result))
            row = json.loads((t / 'state/seat-ledger.jsonl').read_text().splitlines()[-1])
            return result, row

        r, row = run('--verify', 'echo passed', '--verify', 'echo failed >&2; exit 1')
        check(r.returncode == 4 and '## aiw 验收（主会话环境执行）' in r.stdout
              and '1/2 通过' in r.stdout and 'failed' in r.stdout, repr(r))
        check(r.stdout.startswith('codex ok\n'), r.stdout)
        check(row['rc'] == 0 and row['kind'] == 'ok' and row['verify']['total'] == 2
              and row['verify']['passed'] == 1 and row['verify']['secs'] >= 0, repr(row))
        full = (Path(row['run_dir']) / 'verify.txt').read_text()
        check('echo passed' in full and 'rc=0 secs=' in full and 'rc=1 secs=' in full
              and 'failed' in full, full)
        r, row = run('--verify', 'exit 1', '--verify', 'echo later > later')
        check(r.returncode == 4 and (wd / 'later').exists(), repr(r))
        r, row = run('--verify', 'true', '--verify', 'echo good')
        check(r.returncode == 0 and '2/2 通过' in r.stdout and row['verify']['passed'] == 2, repr(r))
        probe = shlex.join([sys.executable, '-c',
                            'import os; print(os.getpriority(os.PRIO_PROCESS, 0))'])
        nested = shlex.join(['bash', str(REPO / 'bin/aiw'), 'slot', '--']) + ' ' + probe
        r, row = run('--verify', probe, '--verify', nested,
                     AIW_SLOT_QOS='nice', AIW_SLOT_PRESSURE_CMD='/usr/bin/true')
        check(r.returncode == 0 and row['verify']['passed'] == 2, repr(r))
        full = (Path(row['run_dir']) / 'verify.txt').read_text()
        check([line.strip() for line in full.splitlines() if line.strip().isdigit()]
              == ['10', '10'], full)
        r, row = run('--verify', 'sleep 5', '--verify-timeout', '1', '--verify', 'true')
        check(r.returncode == 4 and 'rc=124' in r.stdout and '超时' in r.stdout
              and row['verify']['passed'] == 1, repr(r))
        # The successful substitute owns acceptance and its archive.
        script = ('import ai_worker as w; '
                  'w.SEATS["fallback"]["codex"] = ["codex-sol"]; '
                  'w.SEATS["seats"]["codex-sol"]["ro_only"] = False; '  # 写单顶替会跳过只读座位
                  'raise SystemExit(w.main())')
        (t / 'state/breaker.json').unlink(missing_ok=True)
        r = inprocess_sh([sys.executable, '-c', script, *[arg for arg in cmd[2:] if arg != '--no-fallback'], '--verify', 'true'],
                         env=dict(env, FAKE_CODEX='auth_sol', FAKE_CODEX_AUTH_MARKER=str(t / 'auth-marker')))
        rows = [json.loads(line) for line in (t / 'state/seat-ledger.jsonl').read_text().splitlines()]
        check(r.returncode == 0 and r.stdout.count('## aiw 验收') == 1
              and rows[-1]['actual'] == 'codex-sol' and rows[-1]['verify']['passed'] == 1
              and rows[-2]['verify']['skipped'], repr(r))


def write_verify_interrupted():
    import signal
    # Cover interruption of the first command and preservation of earlier results.
    for completed in (False, True):
        with tempfile.TemporaryDirectory() as td:
            t = Path(td)
            cmd, env, wd = worker_call(t, AIW_STATE=str(t / 'state'), FAKE_CODEX='ok')
            cmd[2] = 'codex'
            cmd.remove('--ro')
            cmd += ['--no-fallback', '--no-findings']
            if completed:
                cmd += ['--verify', 'echo completed-output']
            cmd += ['--verify', "sh -c 'echo partial-output; touch verify-started; sleep 30'",
                    '--verify', 'touch should-not-run']
            proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True)
            try:
                wait_until(lambda: (wd / 'verify-started').exists() or proc.poll() is not None,
                           '验收命令未启动', timeout=30)
                check((wd / 'verify-started').exists(), 'aiw 在验收启动前退出')
                proc.send_signal(signal.SIGTERM)
                out, err = proc.communicate(timeout=30)
                check(proc.returncode == 124, repr((proc.returncode, out, err)))
                rows = [json.loads(line) for line in (t / 'state/seat-ledger.jsonl').read_text().splitlines()]
                check(len(rows) == 1, repr(rows))
                row = rows[-1]
                check(row['kind'] == 'interrupted' and row['rc'] == 124
                      and row['verify']['interrupted'] is True
                      and row['verify']['total'] == 2 + int(completed)
                      and row['verify']['passed'] == int(completed), repr(row))
                full = (Path(row['run_dir']) / 'verify.txt').read_text()
                check('rc=124 secs=' in full and '验收中断' in full
                      and 'partial-output' in full, full)
                if completed:
                    check('rc=0 secs=' in full and 'completed-output' in full, full)
                check(not (wd / 'should-not-run').exists()
                      and '跳过 aiw 验收' not in out, repr((out, err)))
            finally:
                if proc.poll() is None:
                    proc.send_signal(signal.SIGTERM)
                    proc.communicate(timeout=30)


def write_verify_not_executed():
    import fcntl
    import hashlib
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        state = t / 'state'; state.mkdir()
        marker = t / 'executor-called'
        cmd, env, wd = worker_call(t, AIW_STATE=str(state), FAKE_CODEX='ok',
                                   FAKE_CODEX_ARGS=str(marker))
        cmd[2] = 'codex'
        cmd.remove('--ro')
        cmd += ['--no-fallback', '--no-findings', '--verify', 'touch should-not-run']
        lock_path = state / ('lock-' + hashlib.sha1(str(wd.resolve()).encode()).hexdigest()[:12])
        with lock_path.open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            r = inprocess_sh(cmd, env=env)
        check(r.returncode == 75 and '跳过 aiw 验收' not in r.stdout, repr(r))
        check(not marker.exists() and not (wd / 'should-not-run').exists(), repr(r))
        # A disabled sole candidate never enters run_seat either.
        (state / 'disabled.json').write_text(json.dumps({'codex': {'disabled_by_user': 'test'}}))
        r = inprocess_sh(cmd, env=env)
        check(r.returncode != 0 and '跳过 aiw 验收' not in r.stdout, repr(r))
        check(not marker.exists() and not (wd / 'should-not-run').exists(), repr(r))
        row = json.loads((state / 'seat-ledger.jsonl').read_text().splitlines()[-1])
        check(row['kind'] == 'skipped_disabled', repr(row))


def write_verify_skipped():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        cmd, env, wd = worker_call(t, AIW_STATE=str(t / 'state'), FAKE_CODEX='ok')
        cmd[2] = 'codex'
        cmd.remove('--ro')
        cmd += ['--no-fallback', '--no-findings']
        def run(*flags, **extra):
            (t / 'state/breaker.json').unlink(missing_ok=True)
            r = inprocess_sh([*cmd, *flags], env=dict(env, **extra))
            row = json.loads((t / 'state/seat-ledger.jsonl').read_text().splitlines()[-1])
            return r, row
        for mode, timeout, rc in (('quota', '10', 1), ('partial_timeout', '1', 124)):
            cmd[4] = timeout
            r, row = run('--verify', 'touch should-not-run', FAKE_CODEX=mode)
            check(r.returncode == rc and '执行方未成功，跳过 aiw 验收' in r.stdout
                  and not (wd / 'should-not-run').exists(), repr(r))
            check(row['verify'] == {'total': 1, 'passed': None, 'skipped': True}, repr(row))
            check(not (Path(row['run_dir']) / 'verify.txt').exists(), repr(row))
        cmd[4] = '10'
        empty = t / 'empty'; empty.write_text('')
        r, row = run('--verify', 'touch should-not-run', FAKE_CODEX='findings',
                     FAKE_FINDINGS_OUTPUT=str(empty))
        check(r.returncode == 2 and '执行方未成功，跳过 aiw 验收' in r.stdout
              and not (wd / 'should-not-run').exists() and row['verify']['skipped'], repr(r))
        r, row = run()
        check(r.returncode == 0 and 'verify' not in row and r.stdout == 'codex ok\n', repr(row))


def write_verify_validation():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        marker = t / 'called'
        cmd, env, wd = worker_call(t, AIW_STATE=str(t / 'state'), FAKE_CODEX_ARGS=str(marker))
        cmd[2] = 'codex'
        cmd.remove('--ro')
        for seat, flags in (
                ('codex', ['--ro', '--verify', 'true']),
                ('review', ['--verify', 'true']), ('draft', ['--verify', 'true']),
                ('claude', ['--verify', 'true']),
                ('codex', ['--verify', 'true', '--verify-timeout', '0']),
                ('codex', ['--verify', 'true', '--verify-timeout', 'abc']),
                ('codex', ['--verify', '']), ('codex', ['--verify', '   ']),
                ('codex', ['--verify']), ('codex', ['--verify-timeout', '-1'])):
            cmd[2] = seat
            r = inprocess_sh([*cmd, *flags], env=env)
            check(r.returncode == 64 and not marker.exists()
                  and not (t / 'state/seat-ledger.jsonl').exists(), repr(r))


def write_verify_worktree():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        work = git_template(t / 'repo', {'sub/tracked': 'original\n'})
        prompt = t / 'prompt'; prompt.write_text('测试')
        env = dict(os.environ, AIW_STATE=str(t / 'state'), FAKE_CODEX='ok')
        cmd = [sys.executable, str(REPO / 'ai_worker.py'), 'write', str(work / 'sub'),
               '10', str(prompt), '--worktree', '--no-fallback', '--no-findings']
        r = inprocess_sh([*cmd, '--verify', 'pwd > verify-pwd'], env=env)
        check(r.returncode == 0, repr(r))
        row = json.loads((t / 'state/seat-ledger.jsonl').read_text().splitlines()[-1])
        wt = Path(row['worktree'])
        check((wt / 'sub/verify-pwd').read_text().strip() == str((wt / 'sub').resolve()), repr(row))
        check(not (work / 'sub/verify-pwd').exists() and '本单无改动，worktree 已清理' not in r.stderr
              and '未跟踪文件数：1' in r.stderr, repr(r))
        r = inprocess_sh([*cmd, '--verify', 'test -f tracked'], env=env)
        check(r.returncode == 0 and '1/1 通过' in r.stdout
              and '本单无改动，worktree 已清理' in r.stderr, repr(r))
        row = json.loads((t / 'state/seat-ledger.jsonl').read_text().splitlines()[-1])
        check(not Path(row['worktree']).exists(), repr(row))

CASES = {case.__name__: case for case in (
    write_verify,
    write_verify_skipped,
    write_verify_validation,
    write_verify_worktree,
    write_verify_interrupted,
    write_verify_not_executed,
    executor_watchdog,
    inflight_liveness_lock,
    test_inflight_lifecycle,
    test_inflight_removed_on_failure,
    write_fallback_text_seat,
    generic_adapter_reasons,
    repository_identity_contract,
)}
