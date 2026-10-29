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
            'stale_provider': bool(os.environ.get('AIW_PROVIDER_FILE') and Path(os.environ['AIW_PROVIDER_FILE']).exists())}
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
            check(row['actual'] == 'qoder' and row['forward_host'] is None, row)


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
        # 角色由执行端按它自己的配置选座位，照旧转发；启用后点名座位也照旧转发。
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


CASES = {fn.__name__: fn for fn in (forward_protocol, forward_fallback_and_exclusions, forward_children_contract, forward_runner_hosts_config, forward_skips_disabled_seat, forward_carries_off_seats)}
