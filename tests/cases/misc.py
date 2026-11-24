"""misc regression cases."""
from __future__ import annotations

from support import (
    Path,
    REPO,
    check,
    fake_agent_use,
    findings_fixture,
    git_template,
    json,
    latest_provider,
    os,
    inprocess_sh as sh,
    shutil,
    subprocess,
    sys,
    tempfile,
    worker_call,
)


def baseline_group_report():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        work = t / "work"; work.mkdir()
        check(sh(["git", "init", "-q", str(work)]).returncode == 0, "git init")
        for name in ("dirty", "tracked"):
            (work / name).write_text("initial")
        check(sh(["git", "add", "."], cwd=work).returncode == 0, "git add")
        check(sh(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "initial"], cwd=work).returncode == 0, "git commit")
        (work / "dirty").write_text("existing")
        (work / ".provider").write_text("legacy")
        baseline = sh(["git", "status", "--porcelain"], cwd=work).stdout
        prompt = t / "prompt"; prompt.write_text("test")
        backend = t / "backend"
        backend.write_text('#!/bin/sh\necho changed > dirty\necho changed > tracked\necho new > created\necho ok\n')
        backend.chmod(0o755)
        env = dict(os.environ, AIW_STATE=str(t / "state"), CODEX_BIN=str(backend))
        r = sh([sys.executable, str(REPO / "ai_worker.py"), "codex", str(work), "10", str(prompt)], env=env)
        check(r.returncode == 0, r.stderr)
        run = latest_provider(t / "state").parent
        check((run / "baseline-status.txt").read_text() == baseline, "开工快照不符")
        existing, added = r.stderr.split("开工前已有改动的文件：", 1)[1].split("本单期间新出现变化的文件：", 1)
        check(existing.startswith("2 个文件") and "dirty" in existing and ".provider" in existing, existing)
        check(added.startswith("2 个文件") and "created" in added and "tracked" in added and "dirty" not in added, added)
        row = json.loads((t / "state/seat-ledger.jsonl").read_text().splitlines()[-1])
        check(row["changed_files"] == 3, str(row))

        (work / "created").unlink()
        check(sh(["git", "restore", "tracked"], cwd=work).returncode == 0, "git restore")
        for i in range(23):
            (work / f"existing-{i:02d}").write_text("existing")
        backend.write_text('#!/bin/sh\necho ok\n')
        r = sh([sys.executable, str(REPO / "ai_worker.py"), "codex", str(work), "10", str(prompt)], env=env)
        check(r.returncode == 0, r.stderr)
        existing = r.stderr.split("开工前已有改动的文件：", 1)[1].split("本单期间新出现变化的文件：", 1)[0]
        check(existing.startswith("25 个文件")
              and len([line for line in existing.splitlines() if line.startswith("  ") and not line.startswith("  …")]) == 20
              and "  … 另 5 个\n" in existing, existing)



def dispatch_date():
    import hashlib
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        env = dict(os.environ, AIW_STATE=str(t / 'state'),
                   AGENT_USE_HOME=str(fake_agent_use(t)), TZ='Asia/Bangkok')
        pf = t / 'prompt'; pf.write_text('检查新版本')
        script = """import datetime, sys
from unittest.mock import patch
import ai_worker as w
day = int(sys.argv[1])
class Fixed(datetime.datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 10, day, 12, tzinfo=tz)
sys.argv = ['ai_worker', 'qoder', sys.argv[2], '10', sys.argv[3], '--ro']
with patch.object(w.dt, 'datetime', Fixed):
    raise SystemExit(w.main())
"""
        for day in ('11', '12'):
            r = sh([sys.executable, '-c', script, day, str(t), str(pf)], env=env, cwd=REPO)
            check(r.returncode == 0, r.stdout + r.stderr)
        rows = [json.loads(x) for x in (t / 'state/seat-ledger.jsonl').read_text().splitlines()]
        check(len(rows) == 2 and {r['prompt'] for r in rows} == {hashlib.sha1(pf.read_bytes()).hexdigest()[:10]}, str(rows))
        for day, row in zip(('11', '12'), rows):
            task = (Path(row['run_dir']) / 'task.txt').read_text()
            check(task.startswith(f'今天是 2026-10-{day}（') and '+0700' in task and '本机时区' in task, task)
            check(task.count('今天是 ') == 1 and '标「未核实」，不要断言它不存在' in task, task)
        # 顶替链的每次尝试收到同一份前言；map 也走同一入口。
        before = len(rows)
        r = sh([sys.executable, '-c', script, '12', str(t), str(pf)],
               env=dict(env, FAKE_AU_QODER='auth', AIW_MAP_DIR=str(t / 'map')), cwd=REPO)
        check(r.returncode == 0, r.stderr)
        rows = [json.loads(x) for x in (t / 'state/seat-ledger.jsonl').read_text().splitlines()][before:]
        check(len(rows) >= 2 and all(x['via'] == 'map' for x in rows), str(rows))
        tasks = [(Path(row['run_dir']) / 'task.txt').read_text() for row in rows]
        check(len({x.split('\n\n本单原工作目录：')[0] for x in tasks}) == 1
              and all(x.count('今天是 ') == 1 for x in tasks), str(tasks))
        before = set((t / 'state/runs').iterdir())
        r = sh([str(REPO / 'bin/aiw'), 'freecheck'], env=env)
        check(r.returncode == 0, r.stderr)
        probes = set((t / 'state/runs').iterdir()) - before
        check(probes and all((d / 'task.txt').read_text().split('\n\n本单原工作目录：')[0] ==
                             'Reply with exactly: FREECHECK_OK' for d in probes), str(probes))



def hook_bad_payload():
    cmd = [sys.executable, str(REPO / 'route.py'), '--hook']
    for payload in ('[]', '{"prompt": null}', '{"prompt": "x", "cwd": 5}'):
        r = sh(cmd, input=payload)
        check(r.returncode == 0 and not r.stdout and not r.stderr, f'坏输入未静默: {r}')
    code = "import route,sys; sys.argv=['route','--hook']; route.route=lambda *a: 1/0; sys.exit(route.main())"
    r = sh([sys.executable, '-c', code], cwd=REPO, input='{"prompt":"x"}')
    check(r.returncode == 0 and not r.stdout and len(r.stderr.splitlines()) == 1, '钩子异常未放行')
    r = sh([sys.executable, '-c', code.replace("['route','--hook']", "['route','x']")], cwd=REPO)
    check(r.returncode != 0, '命令行意外吞异常')



def corrupt_state_rules():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); state = t / 'state'; state.mkdir(); breaker = state / 'breaker.json'; breaker.write_text('{bad')
        cmd, env, _ = worker_call(t, AIW_STATE=str(state))
        r = sh(cmd, env=env)
        check(r.returncode == 1 and '状态文件损坏' in r.stderr and breaker.read_text() == '{bad', '损坏状态被覆盖')
        for name in ('route.py', 'platform_lock.py', 'dispatch.py', 'ai_worker.py', 'findings.py', 'harness_ids.py', 'gitenv.py', 'env_compat.py', 'seats_config.py', 'seats.json'): shutil.copy(REPO / name, t / name)
        (t / 'router-rules.json').write_text('{bad')
        r = sh([sys.executable, str(t / 'route.py'), '--check'], cwd=t)
        check(r.returncode == 1, '损坏全局规则未报错')



def unknown_flags():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); cmd, env, wd = worker_call(t)
        for args in (cmd, [sys.executable, str(REPO / 'panel.py'), str(wd), '10', '测试', 'qoder', 'workbuddy']):
            r = sh(args + ['--no-fallbak'], env=env)
            check(r.returncode == 64 and '--no-fallbak' in r.stderr and '允许' in r.stderr, '未知开关未报错')
            r = sh(args + ['--require-no-tools'], env=env)
            check(r.returncode == 0 and '--require-no-tools 已移除，忽略' in r.stderr, '废弃开关未兼容')
        r = sh(['bash', str(REPO / 'bin/aiw'), 'doctor', '--no-fallbak'], env=env)
        check(r.returncode == 64 and '--no-fallbak' in r.stderr, 'doctor 绕过白名单')
        r = sh([sys.executable, str(REPO / 'panel.py'), '--ro'], env=env)
        check(r.returncode == 64 and '未知开关' in r.stderr, 'panel 白名单过宽')



def substitute_reservations():
    import datetime as dt
    import time
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); env = dict(os.environ, AIW_STATE=str(t))
        code = "import ai_worker as w; print(w.reserve_substitute('claude', 1) or 'denied')"
        procs = [subprocess.Popen([sys.executable, '-c', code], cwd=REPO, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
        results = [p.communicate(timeout=10)[0].strip() for p in procs]
        check(all(p.returncode == 0 for p in procs) and results.count('denied') == 1, f'预占非原子: {results}')
        token = next(x for x in results if x != 'denied')
        r = sh([sys.executable, '-c', f"import ai_worker as w; w.finish_substitute({token!r}, False); print(w.reserve_substitute('claude', 1))"], env=env, cwd=REPO)
        check(r.returncode == 0 and r.stdout.strip() != 'None', '失败未释放')
        token = r.stdout.strip()
        r = sh([sys.executable, '-c', f"import ai_worker as w; w.finish_substitute({token!r}, True); print(w.reserve_substitute('claude', 1))"], env=env, cwd=REPO)
        check(r.returncode == 0 and r.stdout.strip() == 'None', '成功未计数')
        d = json.loads((t / 'daily-count.json').read_text())
        check(d['counts']['claude'] == 1 and not d['reserved'], '预约未转用量')
        d['counts'] = {}; d['reserved'] = {'old': {'seat': 'claude', 'pid': 1, 'ts': time.time() - 7201}}
        (t / 'daily-count.json').write_text(json.dumps(d))
        r = sh([sys.executable, '-c', code], env=env, cwd=REPO)
        check(r.returncode == 0 and r.stdout.strip() != 'denied', '过期未清理')
        check('old' not in json.loads((t / 'daily-count.json').read_text())['reserved'], '过期预约残留')
        d = json.loads((t / 'daily-count.json').read_text())
        d['date'] = (dt.date.today() - dt.timedelta(days=1)).isoformat()
        d['counts'] = {'claude': 10}
        token = next(iter(d['reserved']))
        (t / 'daily-count.json').write_text(json.dumps(d))
        r = sh([sys.executable, '-c', code], env=env, cwd=REPO)
        check(r.stdout.strip() == 'denied', '跨日丢失在途预约')
        r = sh([sys.executable, '-c', f"import ai_worker as w; w.finish_substitute({token!r}, True)"], env=env, cwd=REPO)
        d = json.loads((t / 'daily-count.json').read_text())
        check(r.returncode == 0 and d['counts'] == {'claude': 1} and not d['reserved'], '跨日完成未计数')
        # 直接点名不计数；顶替异常释放；旧配置仍有效。
        code = """import ai_worker as w, sys
w.SEATS['daily_limit'] = {'workbuddy': 1}
w.SEATS.pop('substitute_limit', None)
assert w.substitute_limits() == {'workbuddy': 1}
w.breaker_open = lambda s: None
w.ledger = lambda r: None
w.trip = lambda *a: None
def run(s, *args):
    if s == 'qoder':
        return dict(kind='quota', rc=1, secs=0, err='', out='')
    raise RuntimeError('fake failure')
w.run_seat = run
sys.argv = ['worker', 'qoder', sys.argv[1], '10', sys.argv[2], '--ro']
try: w.main()
except RuntimeError: pass
else: raise AssertionError('异常未抛出')
assert not (w.STATE / 'inflight' / f'{w.os.getpid()}.json').exists()
assert not any(r['seat'] == 'workbuddy' for r in w.load_json(w.DAILY, {})['reserved'].values())
"""
        pf = t / 'prompt'; pf.write_text('测试')
        r = sh([sys.executable, '-c', code, str(t), str(pf)], env=env, cwd=REPO)
        check(r.returncode == 0, f'异常释放失败: {r.stderr}')
        before = (t / 'daily-count.json').read_text()
        r = sh([sys.executable, str(REPO / 'ai_worker.py'), 'qoder', str(t), '10', str(pf), '--ro'], env=env)
        check(r.returncode == 0 and (t / 'daily-count.json').read_text() == before, '直接点名计数了')



def gc_old_directories():
    import time
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); state = t / 'state'; state.mkdir()
        env = dict(os.environ, AIW_STATE=str(state))
        for parent in ('runs', 'panels', 'maps'):
            for age in ('old', 'new'):
                p = state / parent / age; p.mkdir(parents=True); (p / 'data').write_text('data')
                if parent == 'runs':
                    (p / 'scratch/deliver').mkdir(parents=True)
                    (p / 'scratch/deliver/result').write_text('retained artifact')
                if age == 'old': os.utime(p, (time.time() - 31 * 86400,) * 2)
        for name in ('seat-ledger.jsonl', 'breaker.json', 'daily-count.json', 'router-log.jsonl', '.state.lock'):
            (state / name).write_text('保留')
        findings = state / 'findings'
        (findings / 'triage/old').mkdir(parents=True)
        for path in (findings / 'records.jsonl', findings / 'triage/old/batch.md'):
            path.write_text('保留')
            os.utime(path, (time.time() - 90 * 86400,) * 2)
        for path in (findings / 'triage/old', findings / 'triage', findings):
            os.utime(path, (time.time() - 90 * 86400,) * 2)
        (state / 'runs/file').write_text('保留')
        outside = t / 'outside'; outside.mkdir(); (outside / 'data').write_text('保留')
        (state / 'runs/link').symlink_to(outside)
        cmd = ['bash', str(REPO / 'bin/aiw'), 'gc']
        r = sh(cmd + ['--dry-run'], env=env)
        check(r.returncode == 0 and '预计删除 3 个目录' in r.stdout and (state / 'runs/old').exists(), 'dry-run 删除了目录')
        r = sh(cmd, env=env)
        check(r.returncode == 0 and '已删除 3 个目录' in r.stdout and '释放' in r.stdout, '删除数量错误')
        for parent in ('runs', 'panels', 'maps'):
            check(not (state / parent / 'old').exists() and (state / parent / 'new').exists(), '误删新目录')
        check((state / 'runs/new/scratch/deliver/result').read_text() == 'retained artifact', '误删新 scratch')
        check((outside / 'data').read_text() == '保留' and (state / 'runs/file').exists(), '误删目录外文件')
        check(all(p.read_text() == '保留' for p in state.iterdir() if p.is_file()), '误删账本或锁')
        check('已删除 0 个目录' in sh(cmd + ['30'], env=env).stdout, '重复运行不幂等')
        check(sh(cmd + ['--bad'], env=env).returncode == 64, '未知参数未拒绝')
        check((findings / 'records.jsonl').read_text() == '保留' and
              (findings / 'triage/old/batch.md').read_text() == '保留', 'gc 删除了 findings 旧文件或子目录')



def gc_codex_sessions():
    import datetime as dt
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        state = root / 'state'
        home = state / 'codex-home'
        cutoff = dt.date.today() - dt.timedelta(days=30)
        sessions = home / 'sessions'

        def dated(date):
            path = sessions / date.strftime('%Y/%m/%d')
            path.mkdir(parents=True, exist_ok=True)
            (path / 'rollout.jsonl').write_text('session')
            return path

        old = dated(cutoff - dt.timedelta(days=1))
        boundary = dated(cutoff)
        new = dated(dt.date.today())
        os.utime(new, (0, 0))  # Date, not mtime, determines retention.
        outside = root / 'outside'
        outside.mkdir()
        (outside / 'keep').write_text('keep')
        old_link = old / 'linked'
        old_link.symlink_to(outside)
        links = [sessions / '1900', sessions / '1901/01', sessions / '1902/01/01']
        for link in links:
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(outside)
        invalid = sessions / '1903/02/30'
        invalid.mkdir(parents=True)
        (home / 'config.toml').write_text('keep')
        (home / 'auth.json').symlink_to(outside / 'keep')
        cmd = [sys.executable, str(REPO / 'gc.py')]
        env = dict(os.environ, AIW_STATE=str(state))
        r = sh(cmd + ['--dry-run'], env=env)
        check(r.returncode == 0 and '预计删除 1 个目录' in r.stdout and old.exists(), 'sessions dry-run failed')
        r = sh(cmd, env=env)
        check(r.returncode == 0 and '已删除 1 个目录' in r.stdout and not old.exists(), 'old session not deleted')
        check(boundary.exists() and new.exists() and invalid.exists() and all(p.is_symlink() for p in links),
              'session boundary/symlink damaged')
        check((home / 'config.toml').read_text() == (outside / 'keep').read_text() == 'keep'
              and (home / 'auth.json').is_symlink(), 'non-session data damaged')
        for ancestor in (sessions, home):
            moved = ancestor.with_name(ancestor.name + '-real')
            ancestor.rename(moved)
            ancestor.symlink_to(moved)
            check('已删除 0 个目录' in sh(cmd + ['0'], env=env).stdout, 'followed session ancestor symlink')
            ancestor.unlink()
            moved.rename(ancestor)



def seats_json_canonical():
    # seats.json 保持 json.dumps(indent=2, ensure_ascii=False) 的标准排版：脚本改写不产生整文件重排的噪音 diff
    text = (REPO / "seats.json").read_text(encoding="utf-8")
    check(text == json.dumps(json.loads(text), ensure_ascii=False, indent=2) + "\n",
          "seats.json 不是标准排版；用 json.dumps(json.loads(s), ensure_ascii=False, indent=2) + '\\n' 重写后再提交")



def test_state_contract():
    import doctor_config as diagnostics
    from seats_config import load_seats
    fingerprint = diagnostics.fingerprint(REPO, load_seats(REPO))
    check(set(fingerprint) == {'harness', 'agent_use', 'registry', 'local', 'local_git', 'effective_hash'}, 'fingerprint schema')
    check(set(fingerprint['registry']) == {'source', 'sha256', 'git'}, 'registry fingerprint schema')
    check(set(fingerprint['local']) == {'seats', 'route', 'claude', 'agents'}, 'local fingerprint schema')

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
            check(fields == row.keys(), f'inflight 字段缺失: {row}')
            check(row['seat'] == row['actual'] == 'qoder' and row['session'] == 'board-session', '座位/会话错误')
            check(row['timeout'] == 10 and dt.datetime.fromisoformat(row['started']).tzinfo is not None, '时间格式错误')
            out, err = p.communicate(timeout=10)
            check(p.returncode == 0, out + err)
            check(not path.exists(), '完成后仍有 inflight')
            rows = [json.loads(x) for x in (path.parent.parent / 'seat-ledger.jsonl').read_text().splitlines()]
            ledger_fields = {'ts', 'host', 'session', 'seat', 'actual', 'kind', 'rc', 'secs', 'err_tail', 'substituted', 'via'}
            check(ledger_fields <= rows[-1].keys(), f'账本字段缺失: {rows[-1]}')
            check(not {'worktree', 'worktree_branch'} & rows[-1].keys(), '普通单含 worktree 字段')
        finally:
            if p.poll() is None:
                p.terminate()
                p.communicate(timeout=10)

        repo = git_template(t / 'contract-repo', {'tracked': 'base\n'})
        r = sh([sys.executable, str(REPO / 'ai_worker.py'), 'codex', str(repo), '10',
                str(t / 'prompt.md'), '--worktree', '--no-fallback', '--no-findings'],
               env=dict(env, FAKE_CODEX='ok'))
        check(r.returncode == 0, r.stderr)
        row = json.loads((Path(env['AIW_STATE']) / 'seat-ledger.jsonl').read_text().splitlines()[-1])
        check(ledger_fields | {'worktree', 'worktree_branch'} <= row.keys(), str(row))
        check(row['worktree'] == str(Path(env['AIW_STATE']).resolve() / 'worktrees' / Path(row['run_dir']).name)
              and row['worktree_branch'] == 'aiw/' + Path(row['worktree']).name, str(row))

        for claude_session, expected_session in ((None, 'codex-thread'),
                                                  ('claude-session', 'claude-session')):
            track_env = dict(env, CODEX_THREAD_ID='codex-thread')
            track_env.pop('CLAUDE_CODE_SESSION_ID', None)
            if claude_session is not None:
                track_env['CLAUDE_CODE_SESSION_ID'] = claude_session
            p = subprocess.Popen(['bash', str(REPO / 'bin/aiw'), 'track', 'session', '--',
                                  sys.executable, '-c', 'import time; time.sleep(30)'],
                                 env=track_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            path = Path(track_env['AIW_STATE']) / 'tracks' / f'{p.pid}.json'
            try:
                deadline = time.monotonic() + 8
                while not path.exists() and p.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.02)
                check(path.exists(), 'track 未及时创建状态')
                row = json.loads(path.read_text())
                check(row['session'] == expected_session,
                      f'track 会话来源错误: CLAUDE_CODE_SESSION_ID={claude_session!r}, {row}')
            finally:
                if p.poll() is None:
                    p.terminate()
                p.communicate(timeout=10)

        extract_root = t / 'extract'; extract_root.mkdir()
        f = findings_fixture(extract_root)
        f.extract(harness=f.HERE)
        signals = json.loads((f.STATE / 'findings/signals.json').read_text())
        check('coverage' in signals, 'signals coverage 缺失')
        coverage = signals['coverage']
        check(set(coverage) == {'sources', 'level', 'reasons'}, 'signals coverage schema')
        check(coverage['level'] in {'无数据', '部分', '完整'}, 'signals coverage level')
        check(isinstance(coverage['sources'], list) and coverage['sources'] and
              all({'name', 'exists', 'rows', 'bad'} <= source.keys() for source in coverage['sources']),
              'signals coverage sources schema')
        check(isinstance(coverage['reasons'], list) and all(isinstance(reason, str) for reason in coverage['reasons']),
              'signals coverage reasons schema')



def test_aiw_board_forward():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        fakebin = t / 'bin'
        fakebin.mkdir()
        # 仅提供入口解析需要的工具，确保找不到本机真实 aiw-board。
        for tool in ('dirname', 'readlink'):
            (fakebin / tool).symlink_to(shutil.which(tool))
        args = t / 'args'
        board = fakebin / 'aiw-board'
        board.write_text('#!/bin/bash\nprintf "%s\\n" "$@" > "$FORWARD_ARGS"\n')
        board.chmod(0o755)
        env = dict(os.environ, PATH=str(fakebin), AIW_PYTHON=sys.executable, FORWARD_ARGS=str(args))
        cmd = ['/bin/bash', str(REPO / 'bin/aiw')]
        for given, expected in ((['board', '--once', '--all'], ['--once', '--all']),
                                (['statusline'], ['statusline']),
                                (['statusline', '--session', 'two words'], ['statusline', '--session', 'two words'])):
            r = sh(cmd + given, env=env)
            check(r.returncode == 0, r.stdout + r.stderr)
            check(args.read_text().splitlines() == expected, '转发参数错误')
        board.unlink()
        install = '未安装 aiw-board（可选组件）：见 https://github.com/Jerryx333/aiw-board ，安装后确保 aiw-board 在 PATH 中'
        for subcommand in ('board', 'statusline'):
            r = sh(cmd + [subcommand], env=env)
            check(r.returncode == 127 and 'aiw-board' in r.stderr and install in r.stderr, r.stdout + r.stderr)



def invariants_table():
    import importlib.util
    import re
    spec = importlib.util.spec_from_file_location('mutation_check', REPO / 'tests/mutation_check.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ids = re.findall(r'^\|.*\| `([a-z0-9-]+)` \|$',
                     (REPO / 'docs/invariants.md').read_text(), re.M)
    expected = [m['id'] for m in module.MUTATIONS]
    check(len(ids) == len(set(ids)) and len(expected) == len(set(expected)), '变异 id 重复')
    check(set(ids) == set(expected), f'红线表与变异表不一致: {ids} / {expected}')
    check(all(isinstance(m.get('expect'), str) and m['expect'].strip()
              for m in module.MUTATIONS), '变异缺少非空 expect')
    check(all(c in CASES for m in module.MUTATIONS for c in m['tests']), '变异引用了未注册用例')
    bad_anchors = []
    for m in module.MUTATIONS:
        count = (REPO / m['file']).read_text(encoding='utf-8').count(m['old'])
        if count != 1:
            bad_anchors.append(f"{m['id']}: {count}")
    check(not bad_anchors, f"变异锚点出现次数应为 1: {', '.join(bad_anchors)}")



def readme_bilingual_headings():
    counts = [sum(line.startswith('## ') for line in (REPO / name).read_text().splitlines())
              for name in ('README.zh-CN.md', 'README.md')]
    check(counts[0] > 0 and counts[0] == counts[1], f'双语 README 二级标题数量不同或为空：{counts}')



CASES = {case.__name__: case for case in (
    baseline_group_report,
    dispatch_date,
    hook_bad_payload,
    corrupt_state_rules,
    unknown_flags,
    substitute_reservations,
    gc_old_directories,
    gc_codex_sessions,
    seats_json_canonical,
    test_state_contract,
    test_aiw_board_forward,
    invariants_table,
    readme_bilingual_headings,
)}
