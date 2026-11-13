"""派单转发协议的离线回归。"""
from support import Path, REPO, check, json, os, sys, tempfile, worker_call, sh, write_legacy_local


def fixture(t):
    cmd, env, wd = worker_call(t)
    script = t / 'fakebin' / 'forward'
    script.parent.mkdir(exist_ok=True)
    script.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
Path(os.environ['FORWARD_LOG']).write_text(json.dumps({'argv': sys.argv[1:], 'env': dict(os.environ)}))
run = Path(os.environ['AIW_FORWARD_RUN_DIR'])
state = Path(os.environ['AIW_STATE'])
inflight = state / 'inflight' / (str(os.getppid()) + '.json')
snapshot = {'argv': sys.argv[1:], 'env': dict(os.environ),
            'prompt': Path(sys.argv[5] if sys.argv[1] == 'fixed-arg' else sys.argv[4]).read_text(),
            'inflight': json.loads(inflight.read_text()),
            'stale_provider': bool(os.environ.get('AIW_PROVIDER_FILE') and Path(os.environ['AIW_PROVIDER_FILE']).exists()),
            'stdin_null': os.path.samestat(os.fstat(0), os.stat(os.devnull))}
(run / 'snapshot.json').write_text(json.dumps(snapshot))
(run / 'output.md').write_text('forward output')
(run / 'forward.json').write_text(os.environ.get('FORWARD_META', '{"actual":"remote-seat","host":"remote"}'))
print('forward stdout')
print('forward stderr', file=sys.stderr)
sys.exit(int(os.environ.get('FORWARD_RC', '0')))
''')
    script.chmod(0o755)
    env.update(AIW_FORWARD=json.dumps([str(script), 'fixed-arg']), FORWARD_LOG=str(t / 'called'), AIW_MAIN='claude')
    return cmd, env, wd, script


def forward_protocol():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); cmd, env, wd, script = fixture(t)
        (wd / 'attachment.txt').write_text('attachment')
        for role, mode, flag in [('review', 'ro', '--ro'), ('draft', 'text', '--text'), ('write', 'write', None)]:
            call = cmd[:6]; call[2] = role
            call += ['--attach', 'attachment.txt', '--effort', 'high', '--no-findings', '--no-fallback']
            if role == 'write':
                call += ['--verify', 'echo verified > verified', '--verify-timeout', '5']
            r = sh(call, env=env)
            check(r.returncode == 0 and 'forward stdout' in r.stdout and 'forward stderr' in r.stderr, r)
            data = json.loads((t / 'called').read_text()); argv = data['argv']; child = data['env']
            check(argv[:5] == ['fixed-arg', role, str(wd.resolve()), '10', str((t / 'prompt.md').resolve())], argv)
            check(argv[-2:] == ['--attach', str((wd / 'attachment.txt').resolve())], argv)
            check('--verify' not in argv and '--verify-timeout' not in argv, argv)
            check(flag is None or flag in argv, argv)
            check('AIW_FORWARD_GROUP' not in child, child)
            check(child['AIW_NO_FORWARD'] == '1' and child['AIW_MAIN'] == 'claude' and child['AIW_FORWARD_MODE'] == mode, child)
            run = Path(child['AIW_FORWARD_RUN_DIR'])
            check(run.is_absolute() and (run / 'task.txt').exists() and '全文：' in r.stderr, r)
        check((wd / 'verified').read_text().strip() == 'verified', 'verify not local')
        rows = [json.loads(line) for line in (Path(env['AIW_STATE']) / 'seat-ledger.jsonl').read_text().splitlines()]
        check(len(rows) == 3 and all(row['executor'] == 'forward' and row['actual'] == 'remote-seat' and row['forward_host'] == 'remote' for row in rows), rows)
        check(not (Path(env['AIW_STATE']) / 'breaker.json').exists() and not (Path(env['AIW_STATE']) / 'daily-count.json').exists(), 'local selection state changed')
        for args in [('rate', '--pending'), ('rate', str(run), 'ok'), ('weekly', '--json')]:
            r = sh([str(REPO / 'bin/aiw'), *args], env=env)
            check(r.returncode == 0, r)
        call = cmd[:]; call[2] = 'remote-only'
        no_main = dict(env, AIW_MAIN='invalid', CLAUDECODE='', OMPCODE='', PI_CODING_AGENT='')
        r = sh(call, env=no_main)
        check(r.returncode == 0, r)
        data = json.loads((t / 'called').read_text())
        check(data['argv'][1] == 'remote-only' and 'AIW_MAIN' not in data['env'], data)
        r = sh(cmd + ['--verify', 'exit 1'], env=env)
        check(r.returncode == 64, r)
        call = cmd[:6]; call[2] = 'write'
        r = sh(call + ['--verify', 'exit 1'], env=env)
        check(r.returncode == 4, r)
        for meta in ['bad json', '[]', '{"actual": [], "host": 42}']:
            r = sh(cmd, env=dict(env, FORWARD_META=meta))
            check(r.returncode == 0, r)
            row = json.loads((Path(env['AIW_STATE']) / 'seat-ledger.jsonl').read_text().splitlines()[-1])
            check(row['actual'] == 'qoder' and row['forward_host'] is None and 'tokens' not in row, row)
        # 执行端回传的 rollout 用量并进本机账本；格式不对就不写，不估算。
        usage = dict(input=1000, cached=600, cache_write=20, output=100, reasoning=30, calls=2, source='rollout')
        r = sh(cmd, env=dict(env, FORWARD_META=json.dumps(dict(actual='codex', host='remote', tokens=usage))))
        row = json.loads((Path(env['AIW_STATE']) / 'seat-ledger.jsonl').read_text().splitlines()[-1])
        check(r.returncode == 0 and row['executor'] == 'forward' and row['actual'] == 'codex' and row['tokens'] == usage
              and 'tokens_null_reason' not in row, row)
        for bad in [None, 'x', [], dict(usage, source='stderr'), dict(uncached_plus_output=5, source='stderr'),
                    dict(usage, input=-1), dict(usage, output=1.5), dict(usage, output=True),
                    {k: v for k, v in usage.items() if k != 'calls'}, dict(usage, extra=1)]:
            r = sh(cmd, env=dict(env, FORWARD_META=json.dumps(dict(actual='codex', host='remote', tokens=bad))))
            row = json.loads((Path(env['AIW_STATE']) / 'seat-ledger.jsonl').read_text().splitlines()[-1])
            check(r.returncode == 0 and row['actual'] == 'codex' and 'tokens' not in row, (bad, row))


def forward_fallback_and_exclusions():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); cmd, env, wd, script = fixture(t)
        for override in [dict(FORWARD_RC='69'), dict(AIW_FORWARD='["/missing/forward"]')]:
            call = cmd[:]; call[2] = 'review'
            r = sh(call, env=dict(env, **override))
            check(r.returncode == 0 and '改在本机执行' in r.stderr and '角色 review →' in r.stderr, r)
        script.chmod(0o644)
        r = sh(cmd, env=env)
        check(r.returncode == 0 and '改在本机执行' in r.stderr and 'qoder ok' in r.stdout, r)
        script.chmod(0o755)
        r = sh(cmd, env=dict(env, FORWARD_RC='3'))
        check(r.returncode == 3 and 'qoder ok' not in r.stdout and '改在本机执行' not in r.stderr, r)
        for override in [dict(AIW_FORWARD='[]'), dict(AIW_NO_FORWARD='1')]:
            (t / 'called').unlink(missing_ok=True)
            r = sh(cmd, env=dict(env, **override))
            check(r.returncode == 0 and not (t / 'called').exists() and 'qoder ok' in r.stdout, r)
        unconfigured = dict(env); unconfigured.pop('AIW_FORWARD')
        r = sh(cmd, env=unconfigured)
        check(r.returncode == 0 and not (t / 'called').exists() and 'qoder ok' in r.stdout, r)
        for source in ('AIW_PANEL_DIR', 'AIW_MAP_DIR'):
            r = sh(cmd, env=dict(env, **{source: str(t / 'batch')}))
            check(r.returncode == 0 and (t / 'called').exists() and 'forward stdout' in r.stdout, r)
            (t / 'called').unlink()
        for call in [cmd[:4] + ['0'] + cmd[5:], cmd + ['--attach', 'missing.txt'], cmd + ['--effort', 'invalid']]:
            r = sh(call, env=env)
            check(r.returncode == 64 and not (t / 'called').exists(), r)
        (t / 'called').unlink(missing_ok=True)
        check(sh(['git', 'init', '-q', str(wd)], env=env).returncode == 0, 'git init')
        check(sh(['git', '-C', str(wd), '-c', 'user.name=test', '-c', 'user.email=test@example.invalid',
                  'commit', '--allow-empty', '-qm', 'initial'], env=env).returncode == 0, 'git commit fixture')
        call = cmd[:6]; call[2] = 'codex'
        r = sh(call + ['--worktree'], env=env)
        check(r.returncode == 0 and not (t / 'called').exists() and 'codex ok' in r.stdout, r)
        local = t / 'local.json'
        write_legacy_local(local, {'main_exclude': {'claude': ['remote-only']}})
        call = cmd[:]; call[2] = 'remote-only'
        r = sh(call, env=dict(env, AIW_CONFIG=str(local)))
        check(r.returncode == 64 and 'main_exclude' in r.stderr and not (t / 'called').exists(), r)
        for override, status in [({}, '启用 forward'), ({'AIW_FORWARD': '[]'}, '关闭'), ({'AIW_NO_FORWARD': '1'}, '被 AIW_NO_FORWARD 关闭')]:
            r = sh([sys.executable, str(REPO / 'ai_worker.py'), 'doctor'], env=dict(env, **override))
            check('派单转发：' + status in r.stdout and 'fixed-arg' not in r.stdout and not (t / 'called').exists(), r)
        env.pop('AIW_FORWARD')
        write_legacy_local(local, {'forward': {'command': [str(script)]}})
        r = sh(cmd, env=dict(env, AIW_CONFIG=str(local)))
        check(r.returncode == 0 and (t / 'called').exists(), r)
        (t / 'called').unlink()
        env['AIW_FORWARD'] = '[]'
        r = sh(cmd, env=dict(env, AIW_CONFIG=str(local)))
        check(r.returncode == 0 and not (t / 'called').exists(), r)
        from seats_config import load_public, validate
        for command in ['bad', [1], None]:
            config = load_public(REPO); config['forward'] = {'command': command}
            check(any('forward.command' in error for error in validate(config)), command)


def forward_children_contract():
    from ai_worker import PANEL_FORMAT
    from support import fake_agent_use
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); cmd, env, wd, script = fixture(t)
        provider = t / 'actual.provider'
        original = (t / 'prompt.md').read_text()
        for source in ('AIW_PANEL_DIR', 'AIW_MAP_DIR'):
            provider.write_text('stale')
            child_env = dict(env, AIW_PROVIDER_FILE=str(provider), AIW_PANEL_FORMAT='1',
                             **{source: str(t / 'batch')})
            r = sh(cmd, env=child_env)
            check(r.returncode == 0 and provider.read_text() == 'remote-seat\n', r)
            data = json.loads((t / 'called').read_text())
            run = Path(data['env']['AIW_FORWARD_RUN_DIR'])
            snap = json.loads((run / 'snapshot.json').read_text())
            check('--ro' in snap['argv'] and '--no-findings' in snap['argv'], snap)
            check(snap['env']['AIW_FORWARD_GROUP'] == str((t / 'batch').resolve()), snap)
            check(not snap['stale_provider'], snap)
            flight = snap['inflight']
            check(flight['seat'] == 'qoder' and flight['run_dir'] == str(run) and
                  flight['panel'] == (str(t / 'batch') if source == 'AIW_PANEL_DIR' else None), flight)
            check(not list((Path(env['AIW_STATE']) / 'inflight').glob('*')), 'inflight 未清理')
            if source == 'AIW_PANEL_DIR':
                check(snap['env']['AIW_PANEL_FORMAT'] == '0', snap)
            check(snap['prompt'] == original + (PANEL_FORMAT if source == 'AIW_PANEL_DIR' else ''), snap)
            check((t / 'prompt.md').read_text() == original, '原 prompt 改变')
            r = sh(cmd, env=dict(child_env, FORWARD_RC='3'))
            check(r.returncode == 3 and not provider.exists(), r)
        r = sh(cmd, env=dict(env, AIW_PANEL_DIR=str(t / 'batch'), AIW_PANEL_FORMAT='1', FORWARD_RC='69'))
        check(r.returncode == 0 and 'qoder ok' in r.stdout, r)
        rows = [json.loads(line) for line in (Path(env['AIW_STATE']) / 'seat-ledger.jsonl').read_text().splitlines()]
        task = (Path(rows[-1]['run_dir']) / 'task.txt').read_text()
        check(task.count(PANEL_FORMAT) == 1 and (t / 'prompt.md').read_text() == original, task)
        au = fake_agent_use(t, errands=['courier'])
        env.update(AGENT_USE_HOME=str(au), AGENT_USE_ERRAND_HOME=str(au))
        (t / 'called').unlink()
        call = cmd[:]; call[2] = 'courier'
        r = sh(call, env=env)
        check(r.returncode == 64 and '跑腿供应商' in r.stderr and not (t / 'called').exists(), r)
        local = t / 'local.json'
        write_legacy_local(local, {'seats': {'courier': {'kind': 'agent_use', 'provider': 'courier', 'model': ''}},
                                   'fallback': {'qoder': ['courier']}})
        r = sh(cmd, env=dict(env, AIW_CONFIG=str(local)))
        check(r.returncode == 64 and '顶替链配置错误' in r.stderr and not (t / 'called').exists(), r)


def forward_runner_hosts_config():
    import contextlib
    import io
    from unittest.mock import patch
    import seats_config as c
    import doctor_config as d
    with tempfile.TemporaryDirectory() as td:
        local = Path(td) / 'local.json'
        for hosts in ('runner-host', None, {}, [1], [''], [' \t '], ['runner-host', False]):
            config = c.merge(c.load_public(REPO), {'forward': {'command': ['forward'], 'runner_hosts': hosts}})
            check(any('forward.runner_hosts' in e for e in c.validate(config)), hosts)
            local.write_text(json.dumps(config))
            with patch.dict(os.environ, AIW_CONFIG=str(local)):
                try:
                    c.load_seats(REPO)
                    check(False, '非法 runner_hosts 被接受')
                except c.ConfigError as exc:
                    check('forward.runner_hosts' in str(exc), str(exc))
        for hosts in ([], ['runner-host'], ['RUNNER-HOST.example.invalid']):
            config['forward']['runner_hosts'] = hosts
            check(c.validate(config) == [], hosts)
            with patch('socket.gethostname', return_value='runner-host.local'):
                check(c.forward_runs_here(config), hosts)
            check(c.forward_runs_here(config, hostname='runner-host.local'), hosts)
        write_legacy_local(local, {'forward': {'command': ['forward'], 'runner_hosts': ['other-host.example.invalid']}})
        with patch.dict(os.environ, AIW_CONFIG=str(local)), contextlib.redirect_stdout(io.StringIO()) as output:
            check(d.local_config(REPO) == 0, output.getvalue())
        check('未知键路径' not in output.getvalue(), output.getvalue())
        t = Path(td); cmd, env, wd, script = fixture(t)
        env['AIW_CONFIG'] = str(local)
        probe = """from unittest.mock import patch
import ai_worker as w
with patch('socket.gethostname', return_value='runner-host.local'):
    w.doctor()
"""
        for extra, skipped in (({}, True), ({'AIW_NO_FORWARD': '1'}, False), ({'AIW_FORWARD': '[]'}, False)):
            r = sh([sys.executable, '-c', probe], env=dict(env, **extra), cwd=REPO)
            line = next(line for line in r.stdout.splitlines() if line.startswith('派单转发：'))
            check(('；本机非执行端，freecheck 跳过' in line) == skipped, r)
            check('other-host' not in r.stdout and 'runner_hosts' not in r.stdout, r)


def forward_skips_disabled_seat():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); cmd, env, wd, script = fixture(t)
        local = t / 'local.json'
        write_legacy_local(local, {'seats': {'qoder': {'disabled': True}}})
        r = sh(cmd, env=dict(env, AIW_CONFIG=str(local)))
        check(r.returncode == 2 and '已被配置停用' in r.stderr and not (t / 'called').exists() and 'qoder ok' not in r.stdout, r)
        # 角色在本机跳过停用座位后转发；启用后点名座位也照旧转发。
        call = cmd[:]; call[2] = 'review'
        r = sh(call, env=dict(env, AIW_CONFIG=str(local)))
        check(r.returncode == 0 and (t / 'called').exists(), r)
        (t / 'called').unlink()
        write_legacy_local(local, {'seats': {'qoder': {'disabled': False}}})
        r = sh(cmd, env=dict(env, AIW_CONFIG=str(local)))
        check(r.returncode == 0 and (t / 'called').exists(), r)


def forward_carries_off_seats():
    """按角色转发时把本机和上游停用的座位带给执行端（AIW_SKIP_SEATS），没有就不带。"""
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); cmd, env, wd, script = fixture(t)
        local = t / 'local.json'
        call = cmd[:]; call[2] = 'review'
        write_legacy_local(local, {'seats': {'qoder': {'disabled': True}, 'opencode': {'disabled_by_user': '自己在用'}}})
        r = sh(call, env=dict(env, AIW_CONFIG=str(local), AIW_SKIP_SEATS='doubao,bad name'))
        child = json.loads((t / 'called').read_text())['env']
        check(r.returncode == 0 and child.get('AIW_SKIP_SEATS') == 'doubao,opencode,qoder', child.get('AIW_SKIP_SEATS'))
        write_legacy_local(local, {'seats': {}})
        r = sh(call, env=dict(env, AIW_CONFIG=str(local), AIW_SKIP_SEATS=''))
        child = json.loads((t / 'called').read_text())['env']
        check(r.returncode == 0 and 'AIW_SKIP_SEATS' not in child, child.get('AIW_SKIP_SEATS'))
        # 上游停用的点名座位也不转发。
        r = sh(cmd, env=dict(env, AIW_CONFIG=str(local), AIW_SKIP_SEATS='qoder'))
        check(r.returncode == 2 and '派单方停用' in r.stderr, r)


def forward_skips_local_only_seat():
    """local_only 座位依赖本机服务，留在本机跑；去掉开关照旧转发，非布尔值配置报错。"""
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); cmd, env, wd, script = fixture(t)
        local = t / 'local.json'
        write_legacy_local(local, {'seats': {'qoder': {'local_only': True}}})
        r = sh(cmd, env=dict(env, AIW_CONFIG=str(local)))
        check(r.returncode == 0 and not (t / 'called').exists() and 'qoder ok' in r.stdout, r)
        # 按角色派单时本机顺序首选的座位是 local_only，也留在本机。
        write_legacy_local(local, {'seats': {'qoder': {'local_only': True}}, 'roles': {'claude': {'draft': ['qoder']}}})
        call = cmd[:]; call[2] = 'draft'
        r = sh(call, env=dict(env, AIW_CONFIG=str(local)))
        check(r.returncode == 0 and not (t / 'called').exists() and 'qoder ok' in r.stdout, r)
        write_legacy_local(local, {'seats': {'qoder': {'local_only': False}}})
        r = sh(cmd, env=dict(env, AIW_CONFIG=str(local)))
        check(r.returncode == 0 and (t / 'called').exists(), r)
        from seats_config import load_public, validate
        config = load_public(REPO); config['seats']['codex']['local_only'] = 'yes'
        check(any('local_only' in error for error in validate(config)), 'local_only 非布尔值未报错')



def forward_stdin_prompt():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); cmd, env, wd, script = fixture(t)
        call = cmd[:]; call[5] = '-'
        r = sh(call, env=env, input='FORWARD_STDIN_MARKER')
        check(r.returncode == 0 and 'forward stdout' in r.stderr + r.stdout, r)
        data = json.loads((t / 'called').read_text()); run = Path(data['env']['AIW_FORWARD_RUN_DIR'])
        check(data['argv'][4] == str(run / 'forward-prompt.md'), data['argv'])
        snapshot = json.loads((run / 'snapshot.json').read_text())
        check(snapshot['prompt'] == 'FORWARD_STDIN_MARKER', snapshot['prompt'])
        check(snapshot['stdin_null'], '转发命令拿到了调用方的标准输入')
        check('FORWARD_STDIN_MARKER' in (run / 'task.txt').read_text(), 'task.txt 缺 prompt')

def chain_fixture(t):
    cmd, env, wd, script = fixture(t)
    cli = t / 'fakebin' / 'chain-cli'
    cli.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
seat = sys.argv[1]
with open(os.environ['CHAIN_LOG'], 'a') as log:
    log.write(seat + '\\n')
if seat in os.environ.get('CHAIN_FAIL', '').split(','):
    print('at capacity', file=sys.stderr)
    sys.exit(1)
if os.environ.get('CHAIN_REPAIR'):
    count = Path(os.environ['CHAIN_LOG']).read_text().splitlines().count(seat)
    print('invalid result' if count == 1 else '<<<AIW-RESULT\\n{"ok":true}\\nAIW-RESULT>>>')
else:
    print(seat + ' ok')
''')
    cli.chmod(0o755)
    config = {'seats': {name: {'kind': 'cli', 'bin': str(cli),
                              'argv_ro': ['{bin}', name, '{prompt}'],
                              'argv_rw': ['{bin}', name, '{prompt}']}
                        for name in ('seat-a', 'seat-b', 'seat-c')},
              'fallback': {'seat-a': ['seat-b', 'seat-a', 'seat-b']},
              'roles': {'claude': {'write': ['seat-a'], 'review': ['seat-b'], 'draft': ['seat-b']}},
              'findings': {'collect': False}}
    config['seats']['seat-b']['ro_only'] = True
    local = t / 'chain.json'
    local.write_text(json.dumps(config))
    env.update(AIW_CONFIG=str(local), HOME=str(t), AIW_STATE=str(t / 'state'), CHAIN_LOG=str(t / 'chain.log'))
    cmd[2] = 'seat-a'
    return cmd, env, local, config


def forward_resolved_chain():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); cmd, env, local, config = chain_fixture(t)
        config['fallback']['seat-b'] = ['seat-c', 'seat-b', 'seat-c']
        local.write_text(json.dumps(config))
        for name, flags, expected in (
                ('seat-a', [], 'seat-a'),
                ('seat-a', ['--no-fallback'], 'seat-a'),
                ('write', [], 'seat-a'),
                ('review', [], 'seat-b,seat-c'),
                ('draft', [], 'seat-b,seat-c'),
                ('draft', ['--no-fallback'], 'seat-b'),
                ('review', ['--chain', 'seat-c,seat-a,seat-c'], 'seat-c,seat-a'),
                ('review', ['--chain', 'seat-c,seat-a', '--no-fallback'], 'seat-c')):
            call = cmd[:6]; call[2] = name
            r = sh(call + flags, env=env)
            check(r.returncode == 0, r)
            argv = json.loads((t / 'called').read_text())['argv']
            check(argv.count('--chain') == 1 and argv[argv.index('--chain') + 1] == expected,
                  f'转发链不符合本机解析结果：{argv}')
            if name in ('review', 'draft'):
                check('--ro' in argv and ('--text' in argv) == (name == 'draft'), argv)
        config['roles']['claude']['write'] = ['seat-a', 'seat-c']
        config['seats']['seat-a']['disabled'] = True
        local.write_text(json.dumps(config))
        call = cmd[:6]; call[2] = 'write'
        r = sh(call, env=env)
        argv = json.loads((t / 'called').read_text())['argv']
        check(r.returncode == 0 and argv[argv.index('--chain') + 1] == 'seat-c',
              f'转发链不符合本机解析结果：{argv}; {r.stderr}')
        del config['seats']['seat-a']['disabled']
        local.write_text(json.dumps(config))
        # rc=69 回到本机仍使用同一次解析的链。
        r = sh(cmd, env=dict(env, FORWARD_RC='69', CHAIN_FAIL='seat-a'))
        check(r.returncode == 0 and (t / 'chain.log').read_text().splitlines() == ['seat-a', 'seat-b'], r)


def receiver_explicit_chain():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); cmd, env, local, config = chain_fixture(t)
        env.update(AIW_NO_FORWARD='1', CHAIN_FAIL='seat-a')
        config['fallback']['seat-a'] = ['seat-c']
        config['roles']['claude']['review'] = ['seat-c']
        local.write_text(json.dumps(config))
        log = t / 'chain.log'

        def run(chain, expected, rc=0, *, name='review', flags=('--ro',), extra=None):
            log.unlink(missing_ok=True)
            call = cmd[:6]; call[2] = name
            r = sh(call + list(flags) + ['--chain', chain], env=dict(env, **(extra or {})))
            actual = log.read_text().splitlines() if log.exists() else []
            check(r.returncode == rc and actual == expected,
                  f'显式链执行越界或顺序错误：{actual}; {r.stdout}{r.stderr}')
            check(not (t / 'called').exists(), '接收端再次转发')
            return r

        run('seat-a,seat-a,seat-b,seat-b', ['seat-a', 'seat-b'])
        r = run('seat-missing,seat-b', ['seat-b'])
        check('[seat-missing] 顶替跳过：本机座位表中不存在' in r.stderr, r)
        run('seat-missing', [], rc=1)
        run('seat-a,seat-b', ['seat-a'], rc=1, flags=('--ro', '--no-fallback'))
        run('seat-missing,seat-b', [], rc=1, flags=('--ro', '--no-fallback'))
        for field in ('disabled', 'disabled_by_user'):
            config['seats']['seat-a'][field] = True if field == 'disabled' else '测试停用'
            local.write_text(json.dumps(config))
            run('seat-a,seat-b', ['seat-b'])
            del config['seats']['seat-a'][field]
        local.write_text(json.dumps(config))
        run('seat-a,seat-b', ['seat-b'], extra={'AIW_SKIP_SEATS': 'seat-a'})
        config['seats']['seat-text'] = {'kind': 'agent_use', 'provider': 'seat-text'}
        local.write_text(json.dumps(config))
        run('seat-b,seat-text,seat-c', ['seat-c'], name='write', flags=())
        run('seat-text,seat-b,seat-c', ['seat-c'], name='write', flags=())
        state = Path(env['AIW_STATE'])
        import datetime
        import time
        (state / 'breaker.json').write_text(json.dumps({'seat-a': {'until': time.time() + 3600, 'reason': 'auth'}}))
        run('seat-a,seat-b', ['seat-b'])
        config['substitute_limit'] = {'seat-b': 1}
        local.write_text(json.dumps(config))
        (state / 'daily-count.json').write_text(json.dumps({'date': datetime.date.today().isoformat(), 'counts': {'seat-b': 1}}))
        run('seat-a,seat-b,seat-c', ['seat-c'])


def chain_invalid_rejected():
    import ai_worker as worker
    try:
        worker.dispatch_chain('seat-a', {'--no-fallback'}, ['seat-b'])
        check(False, '点名首项被显式链替换')
    except worker.RoleError as error:
        check('首项' in str(error), str(error))
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); cmd, env, local, config = chain_fixture(t)
        for flags in (['--chain'], ['--chain', ''], ['--chain', ',seat-a'], ['--chain', 'seat-a,'],
                      ['--chain', 'seat-a,,seat-b'], ['--chain', 'seat-a, seat-b'],
                      ['--chain', '../seat-a'], ['--chain', 'seat-a;exit'], ['--chain', '--ro'],
                      ['--chain', 'seat-a\n'], ['--chain', 'seat-a', '--chain', 'seat-b']):
            for forwarding in ('0', '1'):
                r = sh(cmd + flags, env=dict(env, AIW_NO_FORWARD=forwarding))
                check(r.returncode == 64 and '--chain' in r.stderr, r)
                check(not (t / 'called').exists() and not (t / 'chain.log').exists(), '非法链仍执行')


def chain_structured_retry():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); cmd, env, local, config = chain_fixture(t)
        schema = t / 'schema.json'
        schema.write_text(json.dumps({'type': 'object'}))
        r = sh(cmd + ['--chain', 'seat-a,seat-b', '--schema', str(schema)],
               env=dict(env, AIW_NO_FORWARD='1', CHAIN_FAIL='seat-a', CHAIN_REPAIR='1'))
        check(r.returncode == 0 and (t / 'chain.log').read_text().splitlines() == ['seat-a', 'seat-b', 'seat-b'],
              f'结构化重交没有钉在实际座位：{r.stdout}{r.stderr}')


CASES = {fn.__name__: fn for fn in (forward_protocol, forward_fallback_and_exclusions, forward_children_contract, forward_runner_hosts_config, forward_skips_disabled_seat, forward_carries_off_seats, forward_skips_local_only_seat, forward_stdin_prompt, forward_resolved_chain, receiver_explicit_chain, chain_invalid_rejected, chain_structured_retry)}

def forward_write_filters_local_readonly_backups():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); cmd, env, local, config = chain_fixture(t)
        config['seats']['seat-b'].pop('ro_only')
        config['seats']['seat-b']['force_mode'] = 'ro'
        config['fallback']['seat-a'] = ['seat-b', 'seat-c']
        local.write_text(json.dumps(config))
        cmd.remove('--ro')
        r = sh(cmd, env=env)
        check(r.returncode == 0, r.stderr)
        argv = json.loads((t / 'called').read_text())['argv']
        check(argv[argv.index('--chain') + 1] == 'seat-a,seat-c', str(argv))

CASES['forward_write_filters_local_readonly_backups'] = forward_write_filters_local_readonly_backups
