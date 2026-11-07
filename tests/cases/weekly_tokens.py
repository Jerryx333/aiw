"""weekly tokens regression cases."""
from __future__ import annotations

from support import (
    Path,
    REPO,
    check,
    json,
    os,
    inprocess_sh as sh,
    sys,
    tempfile,
    token_rollout,
    token_worker,
    weekly_repo_fixture,
    worker_call,
)


def codex_tokens_rollout():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); sid, path = token_rollout(t)
        def event(info):
            return json.dumps(dict(type='event_msg', payload=dict(type='token_count', info=info)))
        usage = dict(input_tokens=1000, cached_input_tokens=600, cache_write_input_tokens=20,
                     output_tokens=100, reasoning_output_tokens=30, total_tokens=1100)
        path.write_text('unrelated non-json line\n' + event(None) + '\n' +
                        event(dict(total_token_usage=dict(usage, input_tokens=100))) + '\n' +
                        event(dict(total_token_usage=usage)) + '\n' + event(None) + '\n')
        expected = dict(input=1000, cached=600, cache_write=20, output=100, reasoning=30,
                        calls=2, source='rollout')
        for seat in ('codex', 'codex-sol'):
            result, row = token_worker(t, f'session id: {sid}\ntokens used\n500\n', seat)
            check(result.returncode == 0 and row['tokens'] == expected and 'tokens_null_reason' not in row, str(row))
            # 执行端在 run 目录留 usage.json，转发命令据此把用量带回发起方。
            saved = json.loads((Path(row['run_dir']) / 'usage.json').read_text())
            check(saved == {'tokens': expected}, saved)
        result, row = token_worker(t, '', 'qoder')
        check(result.returncode == 0 and 'tokens' not in row and 'tokens_null_reason' not in row, str(row))
        check(not (Path(row['run_dir']) / 'usage.json').exists(), row)
        (t / 'state/disabled.json').write_text(json.dumps({'codex': {'reason': 'test'}}))
        result, row = token_worker(t, f'session id: {sid}\n')
        check(row['kind'].startswith('skipped_') and row['rc'] is None and 'tokens' not in row and 'tokens_null_reason' not in row, str(row))
        # Failed actual attempts also retain rollout measurements.
        script = "import ai_worker as w; w.run_seat=lambda *a: dict(out='', err='session id: " + sid + "\\n', kind='other', rc=7, secs=0); raise SystemExit(w.main())"
        (t / 'state/disabled.json').unlink()
        result, row = token_worker(t, '', script=script)
        check(row['rc'] == 7 and row['tokens'] == expected, str(row))

        # A later filename with smaller totals must not replace the larger rollout.
        second = path.with_name('rollout-zz-' + sid + '.jsonl')
        path.write_text(event(dict(total_token_usage=dict(input_tokens=1000, total_tokens=1000))) + '\n')
        second.write_text((event(dict(total_token_usage=dict(input_tokens=100, total_tokens=100))) + '\n') * 3)
        result, row = token_worker(t, f'session id: {sid}\n')
        check(row['tokens']['input'] == 1000 and row['tokens']['calls'] == 1, str(row))

        # Isolated worker CODEX_HOME rollouts also live under the state directory.
        state_path = t / 'state/codex-home/sessions/2020/01/01' / path.name
        state_path.parent.mkdir(parents=True)
        state_path.write_text(event(dict(total_token_usage=usage)) + '\n')
        path.unlink()
        second.unlink()
        result, row = token_worker(t, f'session id: {sid}\n')
        check(result.returncode == 0 and row['tokens'] == dict(expected, calls=1)
              and 'tokens_null_reason' not in row, str(row))



def codex_tokens_fallback():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); sid, path = token_rollout(t)
        stderr = f'session id: {sid}\ntokens used\n24,324\n'
        result, row = token_worker(t, stderr)
        check(result.returncode == 0 and row['tokens'] == dict(source='stderr', uncached_plus_output=24324)
              and row['tokens_null_reason'] == 'rollout_missing', str(row))
        check(not (Path(row['run_dir']) / 'usage.json').exists(), 'stderr 兜底不回传')
        for err in ('', 'session id: ../../bad\n'):
            result, row = token_worker(t, err)
            check(result.returncode == 0 and row['tokens'] is None and row['tokens_null_reason'] == 'no_session_id', str(row))
        path.write_text('{"token_count": broken\n')
        for err in (stderr, f'session id: {sid}\n'):
            result, row = token_worker(t, err)
            check(result.returncode == 0 and row['tokens_null_reason'] == 'rollout_unparsed', str(row))
            check(row['tokens'] == (dict(source='stderr', uncached_plus_output=24324) if err == stderr else None), str(row))
        path.write_bytes(b'{"token_count": \xff}\n')
        result, row = token_worker(t, stderr)
        check(row['tokens'] == dict(source='stderr', uncached_plus_output=24324)
              and row['tokens_null_reason'] == 'rollout_unparsed', str(row))
        path.write_text('{"token_count": broken\n')
        path.chmod(0)
        try:
            if os.geteuid() != 0:
                result, row = token_worker(t, stderr)
                check(row['tokens'] == dict(source='stderr', uncached_plus_output=24324)
                      and row['tokens_null_reason'] == 'rollout_unparsed', str(row))
        finally:
            path.chmod(0o600)
        path.write_text('{"type":"event_msg","payload":{"type":"token_count","info":null}}\n')
        # Inject at run_seat, after configuration/state parsing, so only token parsing raises.
        script = "import ai_worker as w; original=w.run_seat\ndef run(*a):\n r=original(*a); w.json.loads=lambda *a, **k: (_ for _ in ()).throw(RuntimeError('test')); return r\nw.run_seat=run\nraise SystemExit(w.main())"
        result, row = token_worker(t, stderr, script=script)
        check(result.returncode == 0 and row['rc'] == 0 and row['tokens'] is None and row['tokens_null_reason'] == 'error', str(row) + result.stderr)

        path.write_text(json.dumps(dict(type='event_msg', payload=dict(type='token_count',
                        info=dict(total_token_usage=dict(input_tokens=900, cached_input_tokens=800, output_tokens=10))))) + '\n')
        # Inject a timeout carrying captured stderr; token extraction must survive timeout normalization.
        script = ("import ai_worker as w; import os; from pathlib import Path\n"
                  "def run(*a, **k):\n"
                  " raise w.subprocess.TimeoutExpired(a[0], a[1], output='', "
                  "stderr=Path(os.environ['FAKE_CODEX_STDERR']).read_text())\n"
                  "w.run_group=run\nraise SystemExit(w.main())")
        result, row = token_worker(t, f'session id: {sid}\n', script=script)
        check(result.returncode == 124 and row['kind'] == 'timeout' and row['err_tail'] == 'timeout'
              and row['tokens']['source'] == 'rollout' and row['tokens']['input'] == 900, str(row) + result.stderr)



def tokens_report():
    import datetime as dt
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); projects = t / 'projects'; project = projects / 'fake-project'
        project.mkdir(parents=True)
        now = dt.datetime.now(dt.timezone.utc)
        sid, other = '11111111-fake-session', '22222222-fake-session'
        sentinel = 'PRIVATE_BODY_SENTINEL'
        def assistant(mid, ctx, offset=1, **kw):
            return dict(type='assistant', timestamp=(now - dt.timedelta(hours=offset)).isoformat(),
                        sessionId=sid, cwd='/fake/example', message=dict(id=mid, content=sentinel,
                        usage=dict(input_tokens=100 if ctx else 0, cache_read_input_tokens=max(0, ctx-100),
                                   cache_creation_input_tokens=0, output_tokens=10 if ctx else 0)), **kw)
        first = assistant('one', 100000, 3)
        image = dict(type='user', timestamp=now.isoformat(), sessionId=sid,
                     message=dict(content=[dict(type='tool_result', content=[dict(type='image'), dict(type='text', text=sentinel)])]))
        rows = [assistant('two', 500000, 2), first, first, assistant('old', 900000, 240),
                assistant('zero', 0), dict(assistant('side', 20000, isSidechain=True), sessionId='child-fake-session'), image]
        rows[-1]['timestamp'] = (now - dt.timedelta(minutes=1)).isoformat()
        (project / (sid + '.jsonl')).write_text('\n'.join(json.dumps(r) for r in rows) + '\n{"assistant": broken\n')
        third = assistant('three', 200000, 1); third['sessionId'] = other
        third['message'].pop('id'); third['uuid'] = 'fake-uuid'; third.pop('cwd')
        (project / (other + '.jsonl')).write_text(json.dumps(third) + '\n')
        sub = project / sid / 'subagents'; sub.mkdir(parents=True)
        (sub / 'agent-fake.jsonl').write_text(json.dumps(assistant('child', 30000)) + '\n')
        state = t / 'state'; state.mkdir()
        base = dict(ts=(now-dt.timedelta(minutes=1)).isoformat(), actual='codex', rc=0, session=sid,
                    tokens=dict(source='rollout', input=1000, cached=700, output=20))
        led = [base, dict(base, actual='codex-sol', rc=124), dict(base, actual='qoder', tokens=None),
               dict(base, session=None, tokens=dict(source='stderr')), dict(base, rc=None),
               dict(base, ts=(now-dt.timedelta(days=20)).isoformat())]
        (state / 'seat-ledger.jsonl').write_text('\n'.join(json.dumps(r) for r in led))
        env = dict(os.environ, AIW_CLAUDE_PROJECTS=str(projects), AIW_STATE=str(state))
        cmd = [str(REPO / 'bin/aiw'), 'tokens', '7']
        result = sh(cmd + ['--json'], env=env); check(result.returncode == 0, result.stderr)
        d = json.loads(result.stdout)
        check({'window_days', 'totals', 'weighted', 'context', 'concentration', 'sessions', 'subagents', 'dispatch'} <= set(d), str(d))
        check(d['totals']['total'] == 800030 and d['totals']['input'] == 300, str(d))
        check(d['weighted']['total'] == 80420 and d['weighted']['output'] == 150, str(d['weighted']))
        c = d['context']
        check(c['rounds'] == 3 and c['ctx_median'] == 200000 and c['ctx_p90'] == 500000, str(c))
        check(c['thresholds']['400000']['round_share'] == 1/3 and c['thresholds']['400000']['token_share'] == 500010/800030, str(c))
        check(c['fixed_estimate'] == 150000 and c['fixed_share'] == .5 and c['growth_share'] == .5, str(c))
        a = d['sessions'][0]
        check(a['session'] == sid[:8] and a['screenshots'] == 1 and a['dispatches'] == 3 and a['codex_input'] == 2000, str(a))
        check(d['sessions'][1]['project'] == 'fake-project', str(d))
        check(d['subagents']['rounds'] == 2 and d['subagents']['tokens'] == 50020 and len(d['subagents']['parents']) == 1, str(d))
        check(d['dispatch']['count'] == 4 and d['dispatch']['session_share'] == .75 and d['dispatch']['rollout']['codex']['cached'] == 700, str(d))
        check(d['malformed_lines'] == 1 and '1 行解析不了' in result.stderr, result.stderr)
        bool_state = t / 'bool-state'; bool_state.mkdir()
        bool_ledger = [dict(base, tokens=dict(source='rollout', input=True, cached=0, output=0)), base]
        (bool_state / 'seat-ledger.jsonl').write_text('\n'.join(json.dumps(r) for r in bool_ledger))
        bool_env = dict(env, AIW_CLAUDE_PROJECTS=str(bool_state), AIW_STATE=str(bool_state))
        bool_result = sh(cmd + ['--json'], env=bool_env)
        check(bool_result.returncode == 0, bool_result.stderr)
        bool_data = json.loads(bool_result.stdout)
        check(bool_data['malformed_lines'] == 1 and bool_data['dispatch']['count'] == 1
              and bool_data['dispatch']['rollout']['codex']['input'] == 1000, str(bool_data))
        missing_state = t / 'missing-state'; missing_state.mkdir()
        missing_ledger = [dict(base, tokens=dict(source='rollout'))]
        missing_ledger += [dict(base, tokens={k: v for k, v in base['tokens'].items() if k != field})
                           for field in ('input', 'cached', 'output')]
        (missing_state / 'seat-ledger.jsonl').write_text(
            '\n'.join(json.dumps(r) for r in [*missing_ledger, base]))
        missing_result = sh(cmd + ['--json'], env=dict(env, AIW_STATE=str(missing_state)))
        check(missing_result.returncode == 0, missing_result.stderr)
        missing_data = json.loads(missing_result.stdout)
        check(missing_data['dispatch']['rollout']['codex'] == dict(input=1000, cached=700, output=20)
              and missing_data['dispatch']['count'] == 1, str(missing_data))
        check(missing_data['coverage']['sources'][1] ==
              dict(name='seat-ledger.jsonl', exists=True, rows=1, bad=4)
              and missing_data['malformed_lines'] == 5, str(missing_data))
        text = sh(cmd, env=env)
        for expected in ('主会话 3 轮', 'ctx 中位 200000', '轮次 33.33%', '固定 50.00%', '增长 50.00%',
                         '600020 1 3 2000', 'codex rollout: input 1000 · cached 700 · output 20'):
            check(expected in text.stdout, text.stdout)
        check(sentinel not in result.stdout + result.stderr + text.stdout + text.stderr and sid not in json.dumps(d) + text.stdout, 'privacy leak')
        limited = json.loads(sh(cmd + ['--json', '--top', '1'], env=env).stdout)
        check(len(limited['sessions']) == 1 and limited['totals'] == d['totals'], str(limited))
        alias_state = t / 'alias-state'; alias_state.mkdir()
        aliases = [dict(base, actual='codex-z'), dict(base, actual='codex-high'),
                   dict(base, actual='codex-high', tokens=dict(source='rollout', input=200, cached=100, output=5)),
                   dict(base, actual='codex-high', rc=None),
                   dict(base, actual='codex-high', ts=(now-dt.timedelta(days=20)).isoformat()),
                   dict(base, actual='qoder', tokens=None),
                   dict(base, actual='stderr-only', tokens=dict(source='stderr'))]
        aliases += [dict(base, actual='invalid-alias', tokens=dict(source='rollout', input=value))
                    for value in (True, -1, 1.5)]
        (alias_state / 'seat-ledger.jsonl').write_text('\n'.join(json.dumps(r) for r in aliases))
        alias_result = sh(cmd + ['--json'], env=dict(env, AIW_STATE=str(alias_state)))
        check(alias_result.returncode == 0, alias_result.stderr)
        alias_data = json.loads(alias_result.stdout)
        rollout = alias_data['dispatch']['rollout']
        check(list(rollout) == ['codex', 'codex-sol', 'codex-high', 'codex-z'], str(rollout))
        check(rollout['codex-high'] == dict(input=1200, cached=800, output=25), str(rollout))
        check(rollout['codex-z'] == dict(input=1000, cached=700, output=20), str(rollout))
        check(rollout['codex'] == rollout['codex-sol'] == dict(input=0, cached=0, output=0), str(rollout))
        check(alias_data['malformed_lines'] == 4, str(alias_data))
        duplicate = assistant('updated', 100)
        duplicate['message']['usage']['output_tokens'] = 1
        transcript = project / (sid + '.jsonl')
        with transcript.open('a') as stream:
            stream.write(json.dumps(duplicate) + '\n')
            duplicate['message']['usage']['output_tokens'] = 20
            stream.write(json.dumps(duplicate) + '\n')
        updated = json.loads(sh(cmd + ['--json'], env=env).stdout)
        check(updated['totals']['output'] == d['totals']['output'] + 20
              and updated['context']['rounds'] == c['rounds'] + 1, str(updated))
        empty = t / 'empty'; empty.mkdir()
        env.update(AIW_CLAUDE_PROJECTS=str(empty), AIW_STATE=str(empty))
        result = sh(cmd, env=env)
        check(result.returncode == 0 and result.stdout.strip() == '数据覆盖：无数据（seat-ledger.jsonl 不存在；窗口内未读到有效行）\n覆盖只说明读到多少，不代表结论可信度\n无数据', result.stdout)



def tokens_phases():
    import datetime as dt
    with tempfile.TemporaryDirectory() as td:
        root = Path(td); project = root / 'projects/fake-project'
        project.mkdir(parents=True)
        state = root / 'state'; state.mkdir()
        (state / 'seat-ledger.jsonl').write_text('')
        now = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)
        rows = []
        sentinel = 'PRIVATE_PHASE_SENTINEL'
        def add(kind, content, mid=None, ctx=100000, **extra):
            message = dict(content=content)
            if kind == 'assistant':
                message.update(id=mid, usage=dict(input_tokens=100, cache_read_input_tokens=ctx-120,
                                                  cache_creation_input_tokens=20, output_tokens=10))
            row = dict(type=kind, timestamp=(now + dt.timedelta(seconds=len(rows))).isoformat(),
                       sessionId='fake-session', message=message, **extra)
            rows.append(row)
            return row
        def tool(name, **args):
            return dict(type='tool_use', name=name, input=args)
        add('user', '  ' + sentinel + '  ')
        add('assistant', [tool('Read', file_path='/repo/' + sentinel)], 'split', 210000)
        add('assistant', [tool('Bash', command='aiw codex /repo 60 /tmp/' + sentinel)], 'split', 210000)
        add('user', '  <task-notification>done</task-notification>')
        add('assistant', [tool('Monitor')], 'wait')
        add('assistant', [tool('Write', file_path='/repo/' + sentinel)], 'edit')
        add('assistant', [tool('Bash', command='git commit -m test; pytest')], 'test')
        add('assistant', [tool('Bash', command='git -C /repo push')], 'commit')
        add('assistant', [tool('Edit', file_path='/private/tmp/' + sentinel)], 'draft')
        add('assistant', [tool('Grep', pattern=sentinel)], 'read')
        add('assistant', [tool('Agent', prompt=sentinel)], 'agent')
        add('assistant', sentinel, 'text')
        add('assistant', [tool('Mystery')], 'other')
        add('assistant', [tool('Bash', command='aiw write /repo 60 /tmp/task')], 'side', isSidechain=True)
        for prefix in ('<system-reminder', '<local-command', 'Caveat:', '[Request interrupted',
                       '<bash-', 'This session is being continued'):
            add('user', '  ' + prefix + sentinel)
        add('user', sentinel, isMeta=True)
        add('user', [dict(type='tool_result', content=sentinel), dict(type='text', text=sentinel)])
        add('user', [dict(type='text', text='  ' + sentinel)])
        add('assistant', [tool('Bash', command='sleep 2')], 'wait-high', 310000)
        add('user', sentinel)
        add('assistant', [], 'boundary', 200000)
        add('user', sentinel)
        add('assistant', [], 'boundary-high', 300000)
        add('user', sentinel)
        # A user without a following measured assistant has no start ctx.
        transcript = project / 'fake-session.jsonl'
        transcript.write_text('\n'.join(json.dumps(r) for r in rows) + '\n')
        sub = project / 'fake-session/subagents'; sub.mkdir(parents=True)
        (sub / 'agent-fake.jsonl').write_text(json.dumps(rows[2]) + '\n')
        env = dict(os.environ, AIW_CLAUDE_PROJECTS=str(root / 'projects'), AIW_STATE=str(state))
        cmd = [sys.executable, str(REPO / 'tokens.py'), '3']
        plain = sh(cmd + ['--json'], env=env)
        result = sh(cmd + ['--json', '--phases'], env=env)
        check(result.returncode == plain.returncode == 0, result.stderr + plain.stderr)
        data, baseline = json.loads(result.stdout), json.loads(plain.stdout)
        check({k: v for k, v in data.items() if k not in ('phases', 'wait_per_dispatch', 'turn_start')} == baseline,
              'default fields changed')
        phases = data['phases']
        expected = {'发起派单': 1, '主会话改代码': 1, '跑测试/验收': 1, '提交/PR': 1,
                    '等待派单': 2, '写单/草稿': 1, '调查/读取': 1, '子代理': 1, '文字回复': 3, '其他': 1}
        check({k: v['rounds'] for k, v in phases.items()} == expected, str(phases))
        def cost(ctx):
            return 100 + (ctx-120)*.1 + 20*1.25 + 10*5
        check(phases['发起派单']['weighted'] == cost(210000), str(phases))
        total = cost(210000) + 9*cost(100000) + cost(310000) + cost(200000) + cost(300000)
        check(abs(sum(v['weighted'] for v in phases.values())-total) < .001
              and abs(sum(v['share'] for v in phases.values())-1) < 1e-10, str(phases))
        check(data['wait_per_dispatch'] == 2, str(data))
        start = data['turn_start']
        check(start['turns'] == 4 and start['ctx_median'] == 255000, str(start))
        check(start['over_200k']['turns_share'] == .75 and start['over_300k']['turns_share'] == .25, str(start))
        check(abs(start['over_200k']['weighted_share'] - (total-cost(200000))/total) < 1e-10
              and abs(start['over_300k']['weighted_share'] - cost(310000)/total) < 1e-10, str(start))
        text, plain_text = sh(cmd + ['--phases'], env=env), sh(cmd, env=env)
        check(text.stdout.split('\n环节（')[0] == plain_text.stdout, text.stdout)
        check('每单等待轮数: 2.00' in text.stdout and '回合起点上下文：4 回合' in text.stdout, text.stdout)
        check(sentinel not in result.stdout + result.stderr + text.stdout + text.stderr, 'privacy leak')
        rows[2]['message']['content'] = [tool('Read', file_path='/repo/file')]
        # The second content line can omit usage without losing the measured round.
        rows[2]['message'].pop('usage')
        transcript.write_text('\n'.join(json.dumps(r) for r in rows) + '\n')
        no_dispatch = json.loads(sh(cmd + ['--json', '--phases'], env=env).stdout)
        check(no_dispatch['wait_per_dispatch'] is None and no_dispatch['phases']['发起派单']['rounds'] == 0
              and no_dispatch['phases']['调查/读取']['weighted'] == cost(210000) + cost(100000), str(no_dispatch))
        check('每单等待轮数: 无派单' in sh(cmd + ['--phases'], env=env).stdout, 'missing no-dispatch label')
        transcript.write_text('')
        (sub / 'agent-fake.jsonl').unlink()
        empty = sh(cmd + ['--phases'], env=env)
        check(empty.returncode == 0 and '环节' in empty.stdout and '回合起点上下文：0 回合' in empty.stdout, empty.stdout)


def tokens_phases_rules():
    import importlib.util
    spec = importlib.util.spec_from_file_location('tokens_phases_rules', REPO / 'tokens.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    def block(name, **args):
        return dict(type='tool_use', name=name, input=args)
    def classify(*tools):
        return module.phase(list(tools))
    for seat in ('codex', 'write', 'claude-write', 'goal run', 'review', 'draft', 'panel',
                 'codex-sol', 'workbuddy', 'workbuddy-cn', 'qoder', 'opencode', 'doubao',
                 'qianwen', 'claude', 'map'):
        for cli in ('/bin/aiw', 'python3 /repo/ai_worker.py'):
            check(classify(block('Bash', command=f'{cli} {seat} /repo 60 /tmp/task')) == '发起派单', seat)
    for command in ('pytest', 'npm test', 'npm run test', 'go test', 'cargo test', 'node --test',
                    'bun test', 'swift test', 'xcodebuild', 'make', './scripts/check.sh',
                    './scripts/unit-check.sh', 'python3 tests/test_harness.py', 'aiw goal verify plan'):
        check(classify(block('Bash', command=command)) == '跑测试/验收', command)
    for command in ('git commit -m x', 'git push', 'git merge branch', 'git rebase HEAD',
                    'git tag v1', 'git -C /repo commit -m x', 'gh pr view', 'gh issue list'):
        check(classify(block('Bash', command=command)) == '提交/PR', command)
    for command in ('sleep 10', 'sleep 0.5; cat file', 'until test -f file; do true; done',
                    'ps aux', 'pgrep python', 'cat /private/tmp/tasks/abc-123.output'):
        check(classify(block('Bash', command=command)) == '等待派单', command)
    for command in ('echo asleep 10', 'echo sleepiness', 'aiw status', 'aiw board', 'ls'):
        check(classify(block('Bash', command=command)) == '调查/读取', command)
    for name in ('Edit', 'Write', 'MultiEdit', 'NotebookEdit'):
        check(classify(block(name, notebook_path='/repo/file')) == '主会话改代码', name)
    for path in ('/tmp/file', '/private/tmp/file', '/repo/scratchpad/file'):
        check(classify(block('Write', file_path=path)) == '写单/草稿', path)
    check(classify(block('TaskOutput'), block('Agent')) == '等待派单', 'wait priority')
    check(classify(block('Read'), block('Agent')) == '调查/读取', 'read priority')
    check(classify(block('Write', file_path='/repo/file'), block('Bash', command='pytest')) == '主会话改代码', 'edit priority')
    check(classify(block('mcp__codegraph__query')) == '调查/读取', 'codegraph')


def weekly_repo_section():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        weekly_repo_fixture(t)
        r = sh([sys.executable, str(REPO / 'weekly.py')], env=dict(os.environ, AIW_STATE=str(t)))
        check(r.returncode == 0, r.stderr)
        section = r.stdout.split('## 按仓库\n')[1].split('\n\n')[0]
        expected = ('- owner/a: 尝试 3 · 成功 2 · 成功率 66.7% · 超时 1（33.3%） · 成功单中位耗时 3s · 写任务改动文件中位数 4\n'
                    '- /tmp/local: 尝试 1 · 成功 1 · 成功率 100.0% · 超时 0（0.0%） · 成功单中位耗时 0s · 写任务改动文件中位数 0\n'
                    '- owner/b: 尝试 1 · 成功 0 · 成功率 0.0% · 超时 0（0.0%） · 成功单中位耗时 无数据 · 写任务改动文件中位数 无数据\n'
                    '- 未记录: 尝试 1 · 成功 0 · 成功率 0.0% · 超时 0（0.0%） · 成功单中位耗时 无数据 · 写任务改动文件中位数 无数据')
        check(section == expected, section)
        check(r.stdout.index('## 按座位成功率') < r.stdout.index('## 按仓库') < r.stdout.index('被顶替但记成功'), r.stdout)
        check('外包调用 8 次；派单判定 1 条，命中 1，逃生口 0' in r.stdout, r.stdout)
        check('- codex: 尝试 6 · 成功 3 · 成功率 50.0% · 超时 1（16.7%） · 成功单中位耗时 2s · 失败分布 timeout 1、auth 1、format 1 · direct 5 · panel 1 · map 0 · flow 0 · 未记录 0' in r.stdout, r.stdout)
        check('被顶替但记成功：1 次' in r.stdout and '## token（codex 类座位）' in r.stdout and '## 改进缓冲区' in r.stdout, r.stdout)



def weekly_json():
    import datetime as dt
    import socket
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        rows = weekly_repo_fixture(t)
        rows.append(dict(rows[0], ts=(dt.datetime.now().astimezone() - dt.timedelta(days=5)).isoformat(), repo='old', kind='other', rc=1))
        with (t / 'seat-ledger.jsonl').open('a') as f:
            f.write(json.dumps(rows[-1]) + '\n')
        env = dict(os.environ, AIW_STATE=str(t))
        command = [sys.executable, str(REPO / 'weekly.py')]
        r = sh(command + ['--json'], env=env)
        check(r.returncode == 0 and '有 1 行解析不了' in r.stderr, repr(r))
        data = json.loads(r.stdout)
        check(set(data) == {'host', 'days', 'since', 'generated_at', 'calls', 'router', 'kinds', 'efforts', 'seats', 'repos', 'substituted_ok', 'substitute_limits', 'coverage', 'quality'}, str(data))
        check(data['host'] == socket.gethostname() and data['days'] == 7 and data['calls'] == 9, str(data))
        since, generated = (dt.datetime.fromisoformat(data[k]) for k in ('since', 'generated_at'))
        check(since.tzinfo is not None and generated.tzinfo is not None and generated - since == dt.timedelta(days=7), str(data))
        check(data['router'] == dict(total=1, hit=1, escape=0) and data['efforts'] == [dict(seat='codex', effort='high', n=9)], str(data))
        seat = data['seats'][0]
        check(seat == dict(actual='codex', attempts=7, ok=3, success_rate=3/7, timeouts=1, timeout_rate=1/7,
                           median_ok_secs=2, failures=dict(timeout=1, auth=1, format=1, other=1),
                           via=dict(direct=6, panel=1, map=0, flow=0, unknown=0)), str(seat))
        check(data['kinds'] == dict(ok=4, timeout=1, auth=1, format=1, skipped_disabled=1, other=1), str(data['kinds']))
        check(data['substituted_ok'] == 1, str(data))
        check(data['repos'][0] == dict(repo='owner/a', key_type='repo', attempts=3, ok=2, success_rate=2/3,
                                     timeouts=1, timeout_rate=1/3, median_ok_secs=3, median_changed_files=4), str(data['repos']))
        check([(r['repo'], r['key_type']) for r in data['repos']] == [('owner/a', 'repo'), ('/tmp/local', 'workdir'), ('old', 'repo'), ('owner/b', 'repo'), (None, 'unknown')], str(data['repos']))
        text = sh(command, env=env)
        check(text.returncode == 0 and text.stdout.splitlines()[0].endswith(str(data['substitute_limits'])), text.stdout)
        check('外包调用 9 次' in text.stdout and '成功率 42.9%' in text.stdout and 'owner/a: 尝试 3 · 成功 2 · 成功率 66.7%' in text.stdout, text.stdout)
        short = sh(command + ['3', '--json'], env=env)
        short_data = json.loads(short.stdout)
        check(short.returncode == 0 and short_data['days'] == 3 and short_data['calls'] == 8 and len(short_data['repos']) == 4, str(short_data))
        for days in ('0', '-1'):
            invalid = sh(command + [days, '--json'], env=env)
            check(invalid.returncode != 0 and 'usage:' in invalid.stderr and invalid.stdout == '', repr(invalid))
        (t / 'seat-ledger.jsonl').write_text('')
        empty = json.loads(sh(command + ['--json'], env=env).stdout)
        check(empty['calls'] == 0 and empty['seats'] == [] and empty['repos'] == [], str(empty))



def weekly_tokens():
    import datetime as dt
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        row = dict(ts=dt.datetime.now().astimezone().isoformat(), host='fake', seat='requested', actual='codex', rc=0, kind='ok', secs=1)
        usage = dict(input=1000, cached=600, cache_write=0, output=100, reasoning=0, calls=2, source='rollout')
        rows = [dict(row, tokens=usage), dict(row, tokens=dict(usage, input=3000, cached=1400, output=300, calls=4)),
                dict(row, tokens=dict(source='stderr', uncached_plus_output=24324)),
                dict(row, tokens=None, tokens_null_reason='no_session_id'),
                dict(row, tokens=None, tokens_null_reason='error', rc=1, kind='other'), row,
                dict(row, tokens=usage, rc=None, kind='skipped_breaker'),
                dict(row, actual='qoder'), dict(row, actual='codex-sol', tokens=usage),
                dict(row, actual='codex-high', tokens=usage),
                dict(row, actual='codex-high'),
                dict(row, actual='alias-null', tokens=None),
                dict(row, actual='alias-skipped', tokens=None, rc=None, kind='skipped_breaker'),
                dict(row, actual='alias-stderr', tokens=dict(source='stderr', uncached_plus_output=10)),
                dict(row, actual='old-alias', tokens=usage,
                     ts=(dt.datetime.now().astimezone() - dt.timedelta(days=20)).isoformat())]
        def report(data):
            (t / 'seat-ledger.jsonl').write_text('\n'.join(json.dumps(r) for r in data) + '\n')
            result = sh([sys.executable, str(REPO / 'weekly.py')], env=dict(os.environ, AIW_STATE=str(t)))
            check(result.returncode == 0, result.stderr)
            return result.stdout
        output = report(rows)
        section = output.split('## token（codex 类座位）')[1].split('## 派单规则命中')[0]
        for expected in ('codex: 总尝试 6', 'rollout 2（覆盖率 33.3%）', 'stderr 兜底 1',
                         'tokens=null 2（error 1、no_session_id 1）', '旧记录 1',
                         '2000 / 1000 / 200 / 3', '合计 input 4000、cached 2000（缓存占比 50.0%）、output 400',
                         'codex-sol: 总尝试 1', 'Claude 主会话用量见 aiw tokens'):
            check(expected in section, section)
        check('qoder' not in section and 'old-alias' not in section, section)
        alias = section.split('- codex-high:')[1].split('\n')[0]
        for expected in ('总尝试 2', 'rollout 1（覆盖率 50.0%）', '旧记录 1',
                         '合计 input 1000、cached 600（缓存占比 60.0%）、output 100'):
            check(expected in alias, alias)
        check('alias-null: 总尝试 1' in section and 'tokens=null 1（未记录 1）' in section, section)
        check('- alias-skipped: 无数据' in section, section)
        check('alias-stderr: 总尝试 1' in section and 'stderr 兜底 1' in section, section)
        check(section.index('- codex:') < section.index('- codex-sol:')
              < section.index('- alias-null:') < section.index('- alias-stderr:')
              < section.index('- codex-high:'), section)
        check(output.index('## 按座位成功率') < output.index('## token（codex 类座位）') < output.index('## 派单规则命中'), output)
        empty = report([])
        check('- codex: 无数据' in empty and '- codex-sol: 无数据' in empty, empty)
        zero = report([dict(row, tokens=dict(usage, input=0, cached=0))])
        check('缓存占比 无数据' in zero, zero)
        missing_actual = dict(ts=row['ts'], tokens=None, rc=None)
        check('- codex: 无数据' in report([missing_actual]), '缺 actual 的行导致崩溃')
        missing_field = dict(usage)
        missing_field.pop('calls')
        malformed = [dict(usage, input=True), missing_field, dict(usage, output='100'), []]
        bad_output = report([dict(row, tokens=usage), missing_actual] +
                            [dict(row, tokens=tokens) for tokens in malformed])
        bad_line = bad_output.split('## token（codex 类座位）')[1].split('- codex:')[1].split('\n')[0]
        for expected in ('总尝试 5', 'rollout 1（覆盖率 20.0%）', '坏记录 4',
                         '1000 / 600 / 100 / 2',
                         '合计 input 1000、cached 600（缓存占比 60.0%）、output 100'):
            check(expected in bad_line, bad_line)
        bad_only = report([dict(row, tokens=tokens) for tokens in malformed])
        check('坏记录 4 · rollout 用量无数据' in bad_only, bad_only)
        for key in ('input', 'cached', 'output', 'calls'):
            negative = report([dict(row, tokens=dict(usage, **{key: -1}))])
            check('坏记录 1 · rollout 用量无数据' in negative, negative)



def weekly_via():
    import datetime as dt
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        row = dict(ts=dt.datetime.now().astimezone().isoformat(), host='fake',
                   seat='requested', actual='qoder', rc=0, kind='ok', secs=1)
        rows = [dict(row, via='direct'), dict(row, via='panel'),
                dict(row, via='map', rc=1, kind='other'), dict(row),
                dict(row, via='direct', rc=None, kind='skipped_breaker'),
                dict(row, actual='workbuddy', via='panel'),
                dict(row, actual='antigravity', via='map'),
                dict(row, actual='skipped-only', rc=None, kind='skipped_disabled')]
        (t / 'seat-ledger.jsonl').write_text('\n'.join(json.dumps(r) for r in rows) + '\n')
        r = sh([sys.executable, str(REPO / 'weekly.py')],
               env=dict(os.environ, AIW_STATE=str(t)))
        check(r.returncode == 0, r.stdout + r.stderr)
        section = r.stdout.split('## 按座位成功率\n')[1].split('\n\n')[0]
        expected = ('口径：按实际执行座位；不计 freecheck=true 和 skipped_*；成功与超时按 kind 判断；失败分布取前三项。\n'
                    '- qoder: 尝试 4 · 成功 3 · 成功率 75.0% · 超时 0（0.0%） · 成功单中位耗时 1s · 失败分布 other 1 · direct 1 · panel 1 · map 1 · flow 0 · 未记录 1\n'
                    '- antigravity: 尝试 1 · 成功 1 · 成功率 100.0% · 超时 0（0.0%） · 成功单中位耗时 1s · 失败分布 无 · direct 0 · panel 0 · map 1 · flow 0 · 未记录 0\n'
                    '- workbuddy: 尝试 1 · 成功 1 · 成功率 100.0% · 超时 0（0.0%） · 成功单中位耗时 1s · 失败分布 无 · direct 0 · panel 1 · map 0 · flow 0 · 未记录 0')
        check(section == expected, f'成功率/来源统计错误: {section}')
        check(r.stdout.index('## 结果分类') < r.stdout.index('## 按座位成功率'), '小节位置错误')

    for panel_dir, map_dir, expected in ((None, None, 'direct'), ('panel', None, 'panel'),
                                         (None, 'map', 'map'), ('panel', 'map', 'panel'),
                                         ('', '', 'direct'), ('', 'map', 'map')):
        with tempfile.TemporaryDirectory() as td:
            t = Path(td)
            cmd, env, wd = worker_call(t, AIW_STATE=str(t / 'state'))
            for key, value in (('AIW_PANEL_DIR', panel_dir), ('AIW_MAP_DIR', map_dir)):
                env.pop(key, None)
                if value is not None:
                    env[key] = value
            r = sh(cmd + ['--no-fallback'], env=env)
            check(r.returncode == 0, r.stdout + r.stderr)
            ledger = t / 'state/seat-ledger.jsonl'
            last = json.loads(ledger.read_text().splitlines()[-1])
            check(last['via'] == expected and last['rc'] == 0, f'调用来源错误: {last}')
            (t / 'state/disabled.json').write_text(json.dumps({'qoder': {'reason': 'paid'}}))
            r = sh(cmd + ['--no-fallback'], env=env)
            last = json.loads(ledger.read_text().splitlines()[-1])
            check(r.returncode == 1 and last['kind'] == 'skipped_disabled'
                  and last['rc'] is None and last['via'] == expected, f'跳过来源错误: {last}')



def weekly_seat_stats():
    import datetime as dt
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        row = dict(ts=dt.datetime.now().astimezone().isoformat(), host='fake',
                   seat='requested', actual='qoder', rc=0, kind='ok', secs=10)
        rows = [row, dict(row, secs=30), dict(row, kind='timeout', rc=1, secs=500),
                dict(row, kind='format', rc=0, secs=1000),
                dict(row, freecheck=True, secs=9000),
                dict(row, freecheck=True, kind='timeout', rc=1),
                dict(row, actual='probe-only', freecheck=True),
                dict(row, actual='skipped-only', kind='skipped_disabled', rc=None),
                dict(row, kind='skipped_breaker', rc=1),
                dict(row, actual='failed-only', kind='timeout', rc=1),
                dict(row, actual='odd-success', rc=1)]
        (t / 'seat-ledger.jsonl').write_text('\n'.join(json.dumps(r) for r in rows) + '\n')
        result = sh([sys.executable, str(REPO / 'weekly.py')],
                    env=dict(os.environ, AIW_STATE=str(t)))
        check(result.returncode == 0, result.stderr)
        section = result.stdout.split('## 按座位成功率\n')[1].split('\n\n')[0]
        check('- qoder: 尝试 4 · 成功 2 · 成功率 50.0% · 超时 1（25.0%）'
              ' · 成功单中位耗时 20s · 失败分布 timeout 1、format 1' in section, section)
        check('- failed-only: 尝试 1 · 成功 0 · 成功率 0.0% · 超时 1（100.0%）'
              ' · 成功单中位耗时 无数据' in section, section)
        check('- odd-success: 尝试 1 · 成功 1 · 成功率 100.0%' in section, section)
        check('probe-only' not in section and 'skipped-only' not in section and 'requested' not in section, section)
        check('不计 freecheck=true 和 skipped_*' in section, section)



def weekly_mixed_ledger():
    import datetime as dt
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); row = dict(host='fake', seat='qoder', actual='qoder', rc=0, kind='ok', secs=1)
        rows = [dict(row, ts=dt.datetime.now().astimezone().isoformat()), dict(row, ts=dt.datetime.now().isoformat())]
        rows += [dict(row, seat='codex', actual='codex', effort=effort, ts=rows[0]['ts']) for effort in ('high', 'high', 'low')]
        rows.append(dict(rows[-1], rc=None, kind='skipped_breaker'))
        (t / 'seat-ledger.jsonl').write_text('\n'.join(json.dumps(r) for r in rows) + '\n{bad\n')
        r = sh([sys.executable, str(REPO / 'weekly.py')], env=dict(os.environ, AIW_STATE=str(t)))
        check(r.returncode == 0 and '外包调用 5 次' in r.stdout and '有 1 行解析不了' in r.stderr, f'周报错误: {r.stdout}{r.stderr}')
        section = r.stdout.split('## 推理档位\n')[1].split('\n## 结果分类')[0]
        check(section.strip() == '- codex / high: 2\n- codex / low: 1', f'档位计数错误: {section}')



def tokens_coverage():
    import datetime as dt
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        projects = root / 'projects'
        state = root / 'state'; state.mkdir()
        (state / 'seat-ledger.jsonl').write_text('')
        env = dict(os.environ, AIW_CLAUDE_PROJECTS=str(projects), AIW_STATE=str(state))
        cmd = [sys.executable, str(REPO / 'tokens.py')]
        def read():
            result = sh(cmd + ['--json'], env=env)
            check(result.returncode == 0, result.stderr)
            text = sh(cmd, env=env)
            check(text.returncode == 0, text.stderr)
            check(str(projects) not in result.stdout and str(projects) not in text.stdout, 'transcript root privacy leak')
            data = json.loads(result.stdout)
            coverage = data['coverage']
            check(len(coverage['sources']) == 2, str(coverage))
            check(data['malformed_lines'] == sum(s['bad'] for s in coverage['sources']), str(data))
            if not data['malformed_lines']:
                check(not result.stderr, result.stderr)
            return coverage
        coverage = read()
        check(coverage['level'] == '无数据' and 'transcripts 不存在' in coverage['reasons'], str(coverage))
        text = sh(cmd, env=env)
        check('数据覆盖：无数据' in text.stdout and text.stdout.count('覆盖只说明读到多少，不代表结论可信度') == 1, text.stdout)
        project = projects / 'project'; project.mkdir(parents=True)
        now = dt.datetime.now().astimezone()
        row = dict(type='assistant', timestamp=(now - dt.timedelta(minutes=1)).isoformat(),
                   message=dict(id='m', usage=dict(input_tokens=10, output_tokens=2)))
        transcript = project / 'session.jsonl'
        ignored = '{"type":"summary","summary":"x"}\n' + json.dumps(dict(type='user', message=dict(content='x'))) + '\n'
        transcript.write_text(ignored + json.dumps(row) + '\n')
        coverage = read()
        check(coverage['level'] == '完整' and not coverage['reasons'], str(coverage))
        check(coverage['sources'][0] == dict(name='transcripts', exists=True, rows=1, bad=0,
                                          files=1, files_with_rows=1), str(coverage))
        check(coverage['sources'][1] == dict(name='seat-ledger.jsonl', exists=True, rows=0, bad=0), str(coverage))
        (project / 'ignored.jsonl').write_text(ignored + '{broken\n')
        old = dict(row, timestamp=(now - dt.timedelta(days=30)).isoformat())
        (project / 'old.jsonl').write_text(json.dumps(old) + '\n')
        subagents = project / 'session' / 'subagents'; subagents.mkdir(parents=True)
        (subagents / 'agent-one.jsonl').write_text(json.dumps(row) + '\n')
        coverage = read()
        check(coverage['level'] == '完整' and coverage['sources'][0] ==
              dict(name='transcripts', exists=True, rows=2, bad=0, files=4, files_with_rows=2), str(coverage))
        # Main's prefilter also accepts other types mentioning assistant; no extra type filter.
        selected = dict(type='summary', summary='assistant', timestamp=row['timestamp'])
        with transcript.open('a') as stream:
            stream.write(json.dumps(selected) + '\n')
            stream.write('{"type":"assistant",broken\n')
            stream.write('{"type":"summary","summary":"tool_result"}\n')
        coverage = read()
        check(coverage['level'] == '部分' and any('解析失败' in r for r in coverage['reasons']), str(coverage))
        check(coverage['sources'][0] == dict(name='transcripts', exists=True, rows=3, bad=2,
                                          files=4, files_with_rows=2), str(coverage))
        (subagents / 'agent-one.jsonl').unlink()
        transcript.write_text(json.dumps(old) + '\n')
        check(read()['level'] == '无数据', 'old rows count as coverage')


def weekly_coverage():
    import datetime as dt
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        env = dict(os.environ, AIW_STATE=str(root))
        cmd = [sys.executable, str(REPO / 'weekly.py')]
        def read():
            result = sh(cmd + ['--json'], env=env)
            check(result.returncode == 0, result.stderr)
            data = json.loads(result.stdout)
            check({'coverage', 'host', 'days', 'since', 'generated_at', 'calls', 'router', 'kinds',
                   'efforts', 'seats', 'repos', 'substituted_ok', 'substitute_limits', 'quality'} == set(data), str(data))
            return data['coverage']
        coverage = read()
        check(coverage['level'] == '无数据' and any('seat-ledger.jsonl 不存在' == r for r in coverage['reasons']), str(coverage))
        row = dict(ts=(dt.datetime.now().astimezone() - dt.timedelta(minutes=1)).isoformat(),
                   seat='codex', actual='codex', rc=0, kind='ok', secs=1)
        ledger = root / 'seat-ledger.jsonl'
        ledger.write_text(json.dumps(row) + '\n')
        (root / 'router-log.jsonl').write_text('')
        coverage = read()
        check(coverage['level'] == '完整' and coverage['sources'][0]['rows'] == 1, str(coverage))
        with ledger.open('a') as stream:
            stream.write('{broken\n')
        coverage = read()
        check(coverage['level'] == '部分' and coverage['sources'][0]['bad'] == 1
              and any('解析失败' in r for r in coverage['reasons']), str(coverage))
        text = sh(cmd, env=env)
        check('数据覆盖：部分' in text.stdout and text.stdout.count('覆盖只说明读到多少，不代表结论可信度') == 1, text.stdout)
        # Findings are read only in text mode, and missing inputs must be visible.
        check('findings/records.jsonl 不存在' in text.stdout, text.stdout)
        ledger.write_text(''); (root / 'router-log.jsonl').write_text('')
        check(read()['level'] == '无数据', 'collector was not reset')


def seat_quality_rating():
    import datetime as dt
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        env = dict(os.environ, AIW_STATE=str(t), CLAUDE_CODE_SESSION_ID='quality-session', AIW_NESTED='0')
        now = dt.datetime.now().astimezone().isoformat()
        ledger = [dict(ts=now, seat='requested', actual='executor', run_dir=str(t / 'runs' / rid),
                       session='quality-session', rc=0, kind='ok', ro=False, prompt='prompt head', secs=1)
                  for rid in ('one', 'two', 'three', 'pending')]
        # 重复 run 取最后执行行。
        ledger.insert(0, dict(ledger[0], actual='old'))
        ledger.append(dict(ledger[-1], run_dir=str(t / 'runs/other'), session='other'))
        (t / 'seat-ledger.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in ledger))
        command = [str(REPO / 'bin/aiw'), 'rate']
        for value in ('one', str(t / 'runs/two'), str(t / 'runs/three/output.md')):
            r = sh(command + [value, 'ok', '--note', '已核对'], env=env)
            check(r.returncode == 0 and 'executor' in r.stdout, repr(r))
        r = sh(command + ['one', 'fixed'], env=env)
        check(r.returncode == 0, repr(r))
        from rating import latest_ratings
        ratings = latest_ratings(t)
        check(ratings['one']['rating'] == 'fixed' and ratings['two']['note'] == '已核对'
              and ratings['one']['task_class'] == 'write' and ratings['one']['role'] is None, str(ratings))
        check(sh(command + ['unknown', 'ok'], env=env).returncode == 2, 'unknown run')
        r = sh(command + ['--pending'], env=env)
        check(r.returncode == 0 and 'pending\t' in r.stdout and 'one\t' not in r.stdout
              and 'other\t' not in r.stdout and 'prompt head' in r.stdout, repr(r))
        from ai_worker import auto_prompt_prefix
        run = t / 'runs/pending'; run.mkdir(parents=True)
        body = '主会话正文开头：修正座位质量评分显示。' + '正文' * 100
        # 历史日期和其它时区的自动前缀也须跳过，不依赖评分时的本机日期。
        prefix = auto_prompt_prefix(dt.datetime(2020, 1, 2, tzinfo=dt.timezone.utc))
        (run / 'task.txt').write_text(prefix + body)
        r = sh(command + ['--pending'], env=env)
        check(r.returncode == 0 and r.stdout.rstrip('\n').split('\t')[-1] == body[:100]
              and '今天是' not in r.stdout and '不要断言它不存在' not in r.stdout, repr(r))
        (run / 'task.txt').write_text(body)
        r = sh(command + ['--pending'], env=env)
        check(r.stdout.rstrip('\n').split('\t')[-1] == body[:100], repr(r))
        check('other\t' in sh(command + ['--pending', '--session', 'other'], env=env).stdout, 'session filter')
        sh(command + ['pending', 'discard'], env=env)
        check(sh(command + ['--pending'], env=env).stdout.strip() == '无待评分派单', 'empty pending')
        check(sh(command + ['one', 'ok'], env=dict(env, AIW_NESTED='1')).returncode == 64, 'nested rate')
        check(sh(command + ['--pending'], env=dict(env, AIW_NESTED='1')).returncode == 64, 'nested pending')


def weekly_quality():
    import datetime as dt
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        repo = t / 'repo'; repo.mkdir()
        check(sh(['git', 'init', '-q', str(repo)]).returncode == 0, 'git init')
        (repo / 'file').write_text('keep\n')
        generated = dt.datetime.now().astimezone()
        old = (generated - dt.timedelta(hours=49)).isoformat()
        recent = generated.isoformat()
        rows, ratings = [], []
        for actual, count in [('good', 10), ('bad', 10), ('small', 1)]:
            for i in range(count):
                run = t / 'runs' / f'{actual}-{i}'; run.mkdir(parents=True)
                evidence = dict(workdir=str(repo), base_head='test', files={'file': ['keep', 'gone'], 'missing': ['lost']}, truncated=False)
                (run / 'changes.json').write_text(json.dumps(evidence))
                row = dict(ts=old, seat=actual, actual=actual, kind='ok', rc=0, secs=1,
                           run_dir=str(run), ro=False, verify=dict(passed=1, total=2))
                if actual == 'good':
                    row['ro'] = True  # 旧行推断 review，不做存活检查
                rows.append(row)
                ratings.append(dict(run_id=run.name, rating='ok' if actual != 'bad' or i < 7 else 'discard'))
        # 最后一次评分覆盖旧评分；跳过 verify 不进分母。
        ratings.insert(0, dict(run_id='good-0', rating='discard'))
        rows[0]['verify'] = dict(passed=None, total=100, skipped=True)
        for rid, stamp, workdir in [('young', recent, str(repo)), ('absent', old, str(t / 'absent'))]:
            run = t / 'runs' / rid; run.mkdir()
            (run / 'changes.json').write_text(json.dumps(dict(workdir=workdir, files={'file': ['keep']})))
            rows.append(dict(rows[-1], run_dir=str(run), ts=stamp))
        rows += [dict(rows[0], actual='draft', text=True),
                 {k: v for k, v in dict(rows[0], actual='unknown').items() if k != 'ro'},
                 dict(rows[0], actual='excluded', freecheck=True),
                 dict(rows[0], actual='excluded', kind='skipped_disabled')]
        (t / 'seat-ledger.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in rows))
        env = dict(os.environ, AIW_STATE=str(t))
        cmd = [sys.executable, str(REPO / 'weekly.py')]
        r = sh(cmd + ['--json'], env=env)
        check(r.returncode == 0 and all(q['rated'] == 0 for q in json.loads(r.stdout)['quality']), repr(r))
        text = sh(cmd, env=env).stdout
        lines = {line.split(' × ')[0][2:]: line for line in text.splitlines() if ' × ' in line and line.startswith('- ')}
        check(all('评分覆盖率 0.0%） · 未评分' in line and '采纳率' not in line
                  and '返工率' not in line and 'ok 0' not in line for line in lines.values()), text)
        check('verify' not in lines['draft'] and '改动存活率' not in lines['good']
              and '无数据' not in '\n'.join(lines.values()), text)
        check('verify 通过率 50.0%（样本不足）（3/6）' in lines['small']
              and '改动存活率 33.3%（样本不足）（样本 1）' in lines['small'], text)
        (t / 'seat-ratings.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in ratings))
        r = sh(cmd + ['--json'], env=env)
        quality = {q['actual']: q for q in json.loads(r.stdout)['quality']}
        good, bad, small = (quality[k] for k in ('good', 'bad', 'small'))
        check(good['rated'] == 10 and good['adoption_rate'] == 1 and good['rework_rate'] == 0
              and good['verify_passed'] == 9 and good['verify_total'] == 18 and good['verify_rate'] == .5
              and good['suggestions'] == ['good 做 review 质量稳定，可多派'], str(good))
        check(bad['adoption_rate'] == .7 and bad['rework_rate'] == .3
              and abs(bad['survival_rate'] - 1/3) < 1e-8 and bad['survival_samples'] == 10
              and bad['suggestions'] == ['建议该类任务少派 bad'], str(bad))
        check(small['successful'] == 3 and small['survival_samples'] == 1 and not small['suggestions'], str(small))
        check(quality['draft']['task_class'] == 'draft' and quality['unknown']['task_class'] == 'unknown'
              and 'excluded' not in quality, str(quality))
        text = sh(cmd, env=env).stdout
        check('## 按座位 × 任务类别的质量' in text and '样本不足' in text
              and '建议该类任务少派 bad' in text and 'good 做 review 质量稳定，可多派' in text, text)
        lines = {line.split(' × ')[0][2:]: line for line in text.splitlines() if ' × ' in line and line.startswith('- ')}
        check('评分覆盖率 33.3%）' in lines['small']
              and '采纳率 100.0%（样本不足）' in lines['small']
              and '返工率 0.0%（样本不足）' in lines['small']
              and 'ok 1 / fixed 0 / rework 0 / discard 0' in lines['small'], text)
        check('样本不足' not in lines['bad'] and '样本不足' not in lines['good']
              and 'verify' not in lines['draft'] and '改动存活率' not in lines['draft'], text)
        # 写单采纳率达标，但足量存活证据低于 80%，不能建议多派。
        for rating in ratings:
            rating['rating'] = 'fixed'
        (t / 'seat-ratings.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in ratings))
        quality = {q['actual']: q for q in json.loads(sh(cmd + ['--json'], env=env).stdout)['quality']}
        check(not quality['bad']['suggestions'], str(quality['bad']))
        for run in (t / 'runs').iterdir():
            evidence = run / 'changes.json'
            evidence.write_text(json.dumps(dict(workdir=str(repo), files={'file': ['keep']})))
        quality = {q['actual']: q for q in json.loads(sh(cmd + ['--json'], env=env).stdout)['quality']}
        check(quality['bad']['suggestions'] == ['bad 做 write 质量稳定，可多派'], str(quality['bad']))


def changes_evidence():
    from support import fake_bins
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        bins = fake_bins(t)
        cmd, env, wd = worker_call(t, AIW_STATE=str(t / 'state'), FAKE_CODEX='write', AIW_NESTED='0')
        env['PATH'] = str(bins) + os.pathsep + env['PATH']
        sh(['git', 'init', '-q', str(wd)])
        (wd / 'tracked').write_text('original\n')
        sh(['git', '-C', str(wd), 'add', '.'])
        sh(['git', '-C', str(wd), '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'base'])
        # 已脏的 changed 行不算本单新增；created 中的 new 也预先存在。
        (wd / 'tracked').write_text('changed\npreexisting dirty\n')
        (wd / 'created').write_text('new\n')
        cmd[2] = 'codex'; cmd.remove('--ro')
        cmd += ['--no-fallback', '--no-findings']
        r = sh(cmd, env=env)
        row = json.loads((t / 'state/seat-ledger.jsonl').read_text().splitlines()[-1])
        evidence = json.loads((Path(row['run_dir']) / 'changes.json').read_text())
        check(r.returncode == 0 and evidence['files'] == {} and not evidence['truncated']
              and evidence['workdir'] == str(wd.resolve()) and evidence['base_head']
              and row['role'] is None and row['task_class'] == 'write', repr(r) + str(evidence))
        (wd / 'tracked').write_text('original\n')
        (wd / 'created').unlink()
        r = sh(cmd, env=env)
        row = json.loads((t / 'state/seat-ledger.jsonl').read_text().splitlines()[-1])
        evidence = json.loads((Path(row['run_dir']) / 'changes.json').read_text())
        check(r.returncode == 0 and evidence['files'] == {'tracked': ['changed'], 'created': ['new']}, str(evidence))
        # 证据上限及错误隔离，不依赖真实供应商。
        import ai_worker as w
        from unittest.mock import patch
        run = t / 'evidence'; run.mkdir()
        with w.WorkChanges(str(wd), False) as changes:
            (wd / 'many-lines').write_text(''.join(f'line-{i}\n' for i in range(5001)))
            changes.save_evidence(run)
        evidence = json.loads((run / 'changes.json').read_text())
        check(evidence['truncated'] and sum(map(len, evidence['files'].values())) == 5000, str(evidence.keys()))
        (wd / 'many-lines').unlink()
        with w.WorkChanges(str(wd), False) as changes:
            for i in range(201):
                (wd / f'extra-{i:03}').write_text('new line\n')
            changes.save_evidence(run)
            evidence = json.loads((run / 'changes.json').read_text())
            check(evidence['truncated'] and len(evidence['files']) == 200, str(evidence.keys()))
            with patch.object(w, 'save_json', side_effect=OSError('test evidence failure')):
                changes.save_evidence(run)  # 只警告，不抛出。
        # worktree 从仓库子目录派单时证据路径相对 source_root。
        from support import git_template
        repo = git_template(t / 'source', {'sub/tracked': 'base\n'})
        with patch.object(w, 'STATE', t / 'worktree-state'):
            wd2, fields, source, head = w.create_task_worktree(str(repo / 'sub'), t / 'run-worktree')
            with w.WorkChanges(wd2, False) as changes:
                (Path(wd2) / 'tracked').write_text('base\nadded\n')
                changes.save_evidence(run, source)
            evidence = json.loads((run / 'changes.json').read_text())
            check(evidence['workdir'] == str(source) and evidence['base_head'] == head
                  and evidence['files'] == {'sub/tracked': ['added']}, str(evidence))


CASES = {case.__name__: case for case in (
    codex_tokens_rollout,
    codex_tokens_fallback,
    tokens_report,
    tokens_phases,
    tokens_phases_rules,
    tokens_coverage,
    weekly_coverage,
    weekly_repo_section,
    weekly_json,
    weekly_tokens,
    weekly_via,
    weekly_seat_stats,
    weekly_mixed_ledger,
    seat_quality_rating,
    weekly_quality,
    changes_evidence,
)}
