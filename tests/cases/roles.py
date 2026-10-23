"""Role selection and single CLI installation contracts."""
from support import Path, REPO, check, json, os, sys, tempfile, worker_call, inprocess_sh as sh
from support import write_legacy_local
from unittest.mock import patch


def role_resolve_matrix():
    import ai_worker as w
    for family in ('codex', 'claude', 'both'):
        for main in ('claude', 'codex', 'unknown'):
            env = {'AIW_MAIN': main, 'CLAUDECODE': '',
                   'CODEX_BIN': 'codex' if family != 'claude' else '/missing/codex',
                   'CLAUDE_BIN': 'claude' if family != 'codex' else '/missing/claude'}
            with patch.dict(os.environ, env):
                for role in ('write', 'review', 'draft'):
                    if role == 'write' and main == 'codex':
                        try:
                            w.resolve_role(role)
                            check(False, 'Codex write accepted')
                        except w.RoleError:
                            continue
                    expected = ('claude-write' if family == 'claude' else 'codex') if role == 'write' else (
                        'claude' if family == 'claude' or family == 'both' and main == 'codex' and role == 'review' else 'codex-sol')
                    check(w.resolve_role(role) == expected, (family, main, role))
    with patch.dict(os.environ, {'AIW_MAIN': 'claude'}), patch.object(w, 'breaker_open', return_value='auth until 23:00'):
        try:
            w.resolve_role('review')
            check(False, 'auth accepted')
        except w.RoleError as e:
            check('codex-sol' in str(e) and 'claude' in str(e) and 'agent-use' in str(e), str(e))
    with patch.object(w, 'disabled_seat', return_value={'evidence': 'test'}):
        check('disabled' in w.seat_unavailable('codex'), 'disabled accepted')


def role_write_skips_readonly():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); local = t / 'local.json'
        cmd, env, _ = worker_call(t, AIW_CONFIG=str(local), AIW_MAIN='claude')
        cmd[2] = 'write'; cmd.remove('--ro')
        reason = '只读或文本座位，不能写'
        for seat in ('codex-sol', 'text'):
            config = {'seats': {'text': {'kind': 'agent_use', 'provider': 'qoder'}},
                      'roles': {'claude': {'write': [seat, 'codex']}}}
            local.write_text(json.dumps(config))
            r = sh(cmd + ['--no-findings'], env=env)
            check(r.returncode == 0 and '角色 write → 座位 codex' in r.stderr,
                  r.stdout + r.stderr)
            check(f'角色 write 跳过 {seat}: {reason}' in r.stderr, r.stderr)
            r = sh([sys.executable, str(REPO / 'ai_worker.py'), 'doctor'], env=env)
            check('角色 write → 座位 codex' in r.stdout
                  and f'{seat}: {reason}' in r.stdout, r.stdout + r.stderr)
            config['roles']['claude']['write'] = [seat]
            local.write_text(json.dumps(config))
            r = sh(cmd + ['--no-findings'], env=env)
            check(r.returncode == 2 and reason in r.stderr, r.stdout + r.stderr)


def builtin_commands_match_dispatch():
    import re
    from seats_config import BUILTIN_COMMANDS
    source = (REPO / 'bin/aiw').read_text().split('case "$cmd" in', 1)[1]
    commands = {name for arm in re.findall(r'^  ([a-zA-Z0-9_|-]+)\)', source, re.M)
                for name in arm.split('|')} - {'-h', '--help'}
    check(commands == BUILTIN_COMMANDS, str(commands ^ BUILTIN_COMMANDS))


def role_name_conflict():
    import seats_config as c
    for name in ('write', 'review', 'draft'):
        d = c.load_public(); d['seats'][name] = d['seats']['claude']
        check(any('冲突' in e for e in c.validate(d)), name)
    for name in c.BUILTIN_COMMANDS:
        d = c.load_public(); d['roles']['default'][name] = ['claude']
        check(any('冲突' in e for e in c.validate(d)), name)
    for candidate in ('codex', 'claude-write', 'missing'):
        d = c.load_public(); d['roles']['default']['review'] = [candidate]
        check(bool(c.validate(d)) == (candidate == 'missing'), candidate)


def role_review_forces_ro():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); marker = t / 'argv'
        cmd, env, _ = worker_call(t, AIW_MAIN='claude', FAKE_CODEX_ARGS=str(marker))
        cmd[2] = 'review'; cmd.remove('--ro')
        r = sh(cmd + ['--no-findings'], env=env)
        check(r.returncode == 0 and '角色 review → 座位 codex-sol' in r.stderr, r.stderr)
        check('read-only' in marker.read_text(), marker.read_text())
        cli = t / 'replacement-cli'
        cli.write_text('#!' + sys.executable + '\nimport json,sys\n'
                       'if any("effort=\\\"low\\\"" in a for a in sys.argv):\n'
                       ' print("quota exceeded", file=sys.stderr); sys.exit(1)\n'
                       'print(json.dumps(sys.argv[1:]))\n')
        cli.chmod(0o755)
        local = t / 'local.json'
        local.write_text(json.dumps({'fallback': {'codex-sol': ['codex']}}))
        env.update(AIW_CONFIG=str(local), CODEX_BIN=str(cli), AIW_STATE=str(t / 'fallback-state'))
        r = sh(cmd + ['--no-findings'], env=env)
        check(r.returncode == 0 and 'read-only' in json.loads(r.stdout), r.stdout+r.stderr)
        rows = [json.loads(line) for line in (t/'fallback-state/seat-ledger.jsonl').read_text().splitlines()]
        check(rows[-1]['actual'] == 'codex' and rows[-1]['ro'] is True, rows)
        check(all(row['role'] == 'review' and row['task_class'] == 'review' for row in rows), rows)


def role_write_codex_main_refused():
    with tempfile.TemporaryDirectory() as td:
        cmd, env, _ = worker_call(Path(td), AIW_MAIN='codex'); cmd[2] = 'write'
        r = sh(cmd, env=env)
        check(r.returncode == 2 and 'Codex 主会话改文件请自己做' in r.stderr, r.stderr)


def role_doctor_mapping():
    r = sh([sys.executable, str(REPO / 'ai_worker.py'), 'doctor'],
           env=dict(os.environ, AIW_MAIN='codex', CLAUDE_BIN='/missing/claude'))
    check('角色 review → 座位 codex-sol' in r.stdout and 'bin 不可找到' in r.stdout
          and 'Codex 主会话改文件请自己做' in r.stdout, r.stdout)


def claude_write_argv():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        cli = t / 'claude'
        cli.write_text('#!' + sys.executable + '\nimport json,os,sys\nprint(json.dumps({"argv":sys.argv[1:],"cwd":os.getcwd()}))\n')
        cli.chmod(0o755)
        cmd, env, wd = worker_call(t, CLAUDE_BIN=str(cli))
        cmd[2] = 'claude-write'; cmd.remove('--ro')
        r = sh(cmd + ['--no-findings'], env=env)
        check(r.returncode == 0, r.stderr)
        record = json.loads(r.stdout); argv = record['argv']
        check('--restricted' in argv and '--strict-mcp-config' in argv, argv)
        check(argv[argv.index('--permission-prompts')+1] == 'none', argv)
        check(argv[argv.index('--permission-mode')+1] == 'acceptEdits', argv)
        check(argv[argv.index('--tools')+1] == 'Read,Grep,Glob,Edit,Write,Bash', argv)
        check(json.loads(argv[argv.index('--settings')+1]) == {'sandbox': {
            'enabled': True, 'allowUnsandboxedCommands': False, 'failIfUnavailable': True}}, argv)
        check(argv.count('--model') == 1 and argv[argv.index('--model')+1] == 'opus', argv)
        check(record['cwd'] == str(wd.resolve()), record)


def claude_model_opus():
    import ai_worker as w
    for seat in ('claude', 'claude-write'):
        for ro in (True, False):
            argv = w.build_argv(seat, 'test', str(Path.cwd()), ro)
            check(argv.count('--model') == 1 and argv[argv.index('--model')+1] == 'opus', argv)


def route_role_seats():
    import route
    import seats_config as c
    for role in ('write', 'review', 'draft'):
        _, rules = route.check_rules({'rules': [{'id': role, 'seat': role,
            'patterns': ['test'], 'instruction': f'aiw {role} . 10 prompt'}]}, Path('rules.json'))
        route.check_seats(rules, c.load_public())


def panel_skips_unavailable():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); local = t / 'local.json'
        local.write_text(json.dumps({'panel_default': ['codex-sol', 'claude', 'text'],
            'seats': {'text': {'kind': 'agent_use', 'provider': 'qoder'}}}))
        env = dict(os.environ, AIW_CONFIG=str(local), AIW_STATE=str(t / 'state'),
            CLAUDE_BIN='/missing/claude', AIW_MAIN='', CLAUDECODE='')
        r = sh([sys.executable, str(REPO / 'panel.py'), str(t), '10', 'test'], env=env)
        check(r.returncode == 0 and '跳过不可用座位 claude' in r.stderr, r.stdout+r.stderr)
        summary = json.loads(next((t / 'state/panels').glob('*/summary.json')).read_text())
        check([x['seat'] for x in summary['seats']] == ['codex-sol', 'text'], summary)


def panel_single_family_hint():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); local = t / 'local.json'; local.write_text('{}')
        for main, missing in (('claude', 'CLAUDE_BIN'), ('codex', 'CODEX_BIN'), ('', 'CLAUDE_BIN')):
            env = dict(os.environ, AIW_CONFIG=str(local), AIW_STATE=str(t / 'state'),
                       AIW_MAIN=main, CLAUDECODE='', **{missing: '/missing/binary'})
            r = sh([sys.executable, str(REPO / 'panel.py'), str(t), '10', 'test'], env=env)
            check(r.returncode == 1 and '会诊需要至少两家可用座位；单家安装可接入 agent-use 文本座位' in r.stderr, r.stderr)
            check(not (t / 'state/panels').exists(), '单家派单')


def map_default_role():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); local = t / 'local.json'; local.write_text('{}')
        (t / 'prompt').write_text('test'); (t / 'data').write_text('data')
        env = dict(os.environ, AIW_CONFIG=str(local), AIW_STATE=str(t / 'state'),
                   CODEX_BIN='/missing/codex', AIW_MAIN='codex')
        r = sh([sys.executable, str(REPO / 'map.py'), str(t), '10', str(t/'prompt'), 'data'], env=env)
        check(r.returncode == 0 and '角色 draft → 座位 claude' in r.stderr, r.stdout+r.stderr)
        check(json.loads(next((t/'state/maps').glob('*/summary.json')).read_text())['seat'] == 'claude', r.stdout)

def role_text_seat_write_hint():
    with tempfile.TemporaryDirectory() as td:
        cmd, env, _ = worker_call(Path(td))
        cmd[2] = 'workbuddy'; cmd.remove('--ro')
        r = sh(cmd, env=env)
        check(r.returncode == 64 and '要改文件请用 `aiw write`' in r.stderr,
              r.stdout + r.stderr)
        check('要改文件请用 codex' not in r.stderr, r.stderr)


def role_ro_argv_enforced():
    import ai_worker as w
    import seats_config as c
    import copy
    public = c.load_public()
    bad = copy.deepcopy(public)
    bad['seats']['codex-sol']['argv_ro'][5] = 'workspace-write'
    check(any('argv_ro' in e for e in c.validate(bad)), 'Codex writable argv_ro accepted')
    for seat, flags in (('codex-sol', ['--sandbox', 'workspace-write']),
                        ('claude', ['--allowedTools', 'Read,Edit']),
                        ('claude', ['--tools=Read,Write']),
                        ('claude', ['task', '--tools=Read,Bash']),
                        ('claude', ['--allowedTools', 'Read', 'Bash(git *)']),
                        ('claude', ['--permission-mode', 'acceptEdits']),
                        ('claude', ['--permission-mode=bypassPermissions'])):
        with patch.object(w, 'build_argv', return_value=['cli', *flags, 'task']), \
             patch.object(w, 'run_group') as run:
            result = w._run_seat(seat, 'task', str(REPO), 10, True)
            check(result['rc'] == 64 and '只读' in result['err'], result)
            run.assert_not_called()
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); local = t / 'local.json'
        local.write_text(json.dumps({'seats': {'claude': {'argv_ro':
            ['{bin}', '-p', '--allowedTools', 'Read,Edit', '--', '{prompt}']}}}))
        cmd, env, wd = worker_call(t, AIW_CONFIG=str(local), AIW_MAIN='codex')
        (wd / 'data').write_text('data')
        for seat in ('claude', 'review'):
            cmd[2] = seat
            r = sh(cmd + ['--no-findings'], env=env)
            check(r.returncode == 64 and '只读' in r.stderr, r.stderr)
        local.write_text(json.dumps({'fallback': {'codex-sol': ['claude']},
            'seats': {'claude': {'argv_ro': ['{bin}', '-p', '--tools', 'Write', '{prompt}']}}}))
        cmd[2] = 'codex-sol'
        r = sh(cmd + ['--no-findings'], env=dict(env, FAKE_CODEX='quota'))
        check(r.returncode == 64 and '只读' in r.stderr, r.stderr)
        r = sh([sys.executable, str(REPO/'panel.py'), str(wd), '10', 'test', 'claude'], env=env)
        check(r.returncode == 1 and '只读' in r.stdout, r.stdout+r.stderr)
        r = sh([sys.executable, str(REPO/'map.py'), str(wd), '10', cmd[5], 'data',
                '--seat', 'claude', '--jobs', '1'], env=env)
        check(r.returncode != 0, r.stdout+r.stderr)
        archived = list((Path(env['AIW_STATE'])/'maps').glob('*/*.stderr.txt'))
        check(any('只读' in f.read_text() for f in archived), archived)


def seat_disabled_everywhere():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); local = t/'local.json'
        local.write_text(json.dumps({'seats': {'codex-sol': {'disabled': True}},
                                     'fallback': {'codex': ['codex-sol']}}))
        cmd, env, wd = worker_call(t, AIW_CONFIG=str(local), AIW_MAIN='claude')
        (wd/'data').write_text('data')
        for seat in ('codex-sol', 'review', 'codex'):
            cmd[2] = seat
            r = sh(cmd + ['--no-findings'], env=dict(env, FAKE_CODEX='quota') if seat == 'codex' else env)
            if seat == 'review':
                check(r.returncode == 0 and '座位 claude' in r.stderr, r.stderr)
            else:
                check(r.returncode != 0 and '配置停用' in r.stderr, r.stderr)
            check('实际执行方：codex-sol' not in r.stderr, r.stderr)
        r = sh([sys.executable, str(REPO/'panel.py'), str(wd), '10', 'test', 'codex-sol', 'claude'], env=env)
        check(r.returncode == 1 and '跳过不可用座位 codex-sol' in r.stderr, r.stderr)
        r = sh([sys.executable, str(REPO/'map.py'), str(wd), '10', cmd[5], 'data',
                '--seat', 'codex-sol', '--jobs', '1'], env=env)
        check(r.returncode != 0, r.stdout+r.stderr)
        archived = list((Path(env['AIW_STATE'])/'maps').glob('*/*.stderr.txt'))
        check(any('配置停用' in f.read_text() for f in archived), archived)


def legacy_local_config_roles():
    import seats_config as c
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); local = t/'local.json'
        local.write_text(json.dumps({'seats': {'codex-sol': {'ro_only': False}}}))
        with patch.dict(os.environ, {'AIW_CONFIG': str(local)}):
            check(c.load_seats()['seats']['codex-sol']['ro_only'] is False, 'legacy rejected')
        cmd, env, _ = worker_call(t, AIW_CONFIG=str(local), AIW_MAIN='claude')
        cmd[2] = 'review'
        r = sh(cmd + ['--no-findings'], env=env)
        check(r.returncode == 0 and '座位 claude' in r.stderr, r.stderr)
        r = sh([sys.executable, str(REPO/'ai_worker.py'), 'doctor'], env=env)
        check('codex-sol: 非只读座位' in r.stdout, r.stdout)
        public = c.load_public(); public['seats']['review'] = public['seats']['claude']
        check(any('重命名' in e for e in c.validate(public)), c.validate(public))


def panel_explicit_unavailable():
    import time
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); local = t/'local.json'; state = t/'state'; state.mkdir()
        local.write_text(json.dumps({'seats': {'claude-write': {'disabled': True}}}))
        for reason in ('auth', 'not_installed'):
            (state/'breaker.json').write_text(json.dumps({'codex-sol': {'reason': reason, 'until': time.time()+600}}))
            env = dict(os.environ, AIW_CONFIG=str(local), AIW_STATE=str(state), CLAUDE_BIN='/missing/claude')
            r = sh([sys.executable, str(REPO/'panel.py'), str(t), '10', 'test',
                    'codex-sol', 'claude', 'claude-write'], env=env)
            check(r.returncode == 1 and reason in r.stderr and 'bin 不可找到' in r.stderr
                  and 'disabled' in r.stderr, r.stderr)
            check(not (state/'panels').exists(), 'unavailable seats executed')
        env.update(CLAUDE_BIN='claude')
        r = sh([sys.executable, str(REPO/'panel.py'), str(t), '10', 'test', 'claude'], env=env)
        check(r.returncode == 1 and 'claude ok' in r.stdout, r.stdout+r.stderr)


def role_preselect_readonly():
    import ai_worker as w
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); state = t/'absent'; local = t/'local.json'
        local.write_text('{}')
        cmd, env, _ = worker_call(t, AIW_CONFIG=str(local), AIW_STATE=str(state),
                                 CODEX_BIN='/missing/codex', CLAUDE_BIN='/missing/claude')
        cmd[2] = 'review'
        r = sh(cmd, env=env)
        check(r.returncode == 2 and not state.exists(), r.stderr)
        with patch.object(w, 'selection_snapshot', wraps=w.selection_snapshot) as snapshot, \
             patch.object(w, 'state_lock', side_effect=AssertionError('preselection locked')):
            w.resolve_role('draft')
            check(snapshot.call_count == 1, snapshot.call_count)


CASES = {f.__name__: f for f in (role_resolve_matrix, role_write_skips_readonly, role_name_conflict, builtin_commands_match_dispatch, role_review_forces_ro,
    role_write_codex_main_refused, role_doctor_mapping, claude_write_argv, claude_model_opus,
    route_role_seats, panel_skips_unavailable, panel_single_family_hint, map_default_role, role_text_seat_write_hint, role_ro_argv_enforced, seat_disabled_everywhere,
    legacy_local_config_roles, panel_explicit_unavailable, role_preselect_readonly)}


def main_agent_priority():
    import ai_worker as w
    base = {'CLAUDECODE': '1', 'PI_CODING_AGENT': 'true', 'OMPCODE': '1'}
    for main in ('claude', 'codex', 'pi', 'omp'):
        check(w.main_agent(dict(base, AIW_MAIN=' ' + main.upper() + ' ')) == main, main)
    check(w.main_agent(dict(base, AIW_MAIN='unknown')) == 'omp', 'omp priority')
    check(w.main_agent({'PI_CODING_AGENT': 'true', 'CLAUDECODE': '1'}) == 'pi', 'pi priority')
    check(w.main_agent({'CLAUDECODE': '1'}) == 'claude', 'claude')
    for env in ({}, {'AIW_MAIN': 'unknown'}, {'OMPCODE': 'true', 'PI_CODING_AGENT': '1', 'CLAUDECODE': 'true'}):
        check(w.main_agent(env) is None, env)


def main_exclude_config():
    import seats_config as c
    import contextlib
    import io
    import doctor_config as d
    public = c.load_public()
    check(c.validate(public) == [], c.validate(public))
    for main in ('pi', 'omp'):
        check(public['self_seats'][main] == [main], main)
        check(public['roles'][main] == dict(write=[], review=[], draft=[]), main)
        check(public['main_exclude'][main] == ['codex', 'codex-sol', 'claude', 'claude-write'], main)
    for override, expected in (({'main_exclude': []}, 'main_exclude'),
                               ({'main_exclude': {'pi': 'codex'}}, 'main_exclude.pi'),
                               ({'main_exclude': {'omp': [None]}}, 'main_exclude.omp')):
        check(any(expected in e for e in c.validate(c.merge(public, override))), override)
    override = {'self_seats': {'pi': ['optional']}, 'main_exclude': {'pi': ['optional']}}
    merged = c.merge(public, override)
    check(not c.validate(merged) and merged['main_exclude']['pi'] == ['optional']
          and merged['main_exclude']['omp'] == public['main_exclude']['omp'], merged)
    check(c.effective_hash(public) != c.effective_hash(merged), 'hash excludes main_exclude')
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); (t/'seats.json').write_text(json.dumps(public))
        local = t/'local.json'; local.write_text(json.dumps(override))
        out = io.StringIO()
        with patch.dict(os.environ, AIW_CONFIG=str(local)), contextlib.redirect_stdout(out):
            check(d.local_config(t) == 0, out.getvalue())
        check('未知键路径' not in out.getvalue(), out.getvalue())


def main_exclude_dispatch():
    import ai_worker as w
    import seats_config as c
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); local = t/'local.json'
        for main in ('pi', 'omp'):
            overrides = {'roles': {main: {r: ['codex-sol', 'claude'] for r in ('write', 'review', 'draft')}},
                         'fallback': {'qoder': ['codex-sol', 'workbuddy']}}
            write_legacy_local(local, overrides)
            cmd, env, _ = worker_call(t, AIW_CONFIG=str(local), AIW_MAIN=main, CLAUDECODE='1', AIW_STATE=str(t/main))
            for seat in ('codex', 'codex-sol', 'claude', 'claude-write'):
                cmd[2] = seat
                r = sh(cmd + ['--no-findings'], env=env)
                check(r.returncode == 64 and 'main_exclude' in r.stderr and main in r.stderr, r.stderr)
            for role in ('write', 'review', 'draft'):
                cmd[2] = role
                r = sh(cmd + ['--no-findings'], env=env)
                check(r.returncode == 2 and '无可用座位' in r.stderr and 'main_exclude' in r.stderr, r.stderr)
            # Candidate filtering selects the next allowed seat for all three roles.
            config = c.merge(w.SEATS, overrides)
            for role, allowed in (('write', 'codex'), ('review', 'workbuddy'), ('draft', 'workbuddy')):
                config['roles'][main][role] = ['claude-write', allowed]
                config['main_exclude'][main] = ['claude-write']
                with patch.dict(os.environ, env), patch.object(w, 'SEATS', config):
                    check(w.resolve_role(role) == allowed, role)
            cmd[2] = 'qoder'
            env['FAKE_AU_QODER'] = 'quota'
            r = sh(cmd + ['--no-findings'], env=env)
            check(r.returncode == 0 and '顶替跳过 codex-sol' in r.stderr
                  and '实际执行方：workbuddy' in r.stderr, r.stdout+r.stderr)
            records = [json.loads(line) for line in (Path(env['AIW_STATE'])/'seat-ledger.jsonl').read_text().splitlines()]
            check(not any(row.get('actual') == 'codex-sol' for row in records), records)
            # An all-excluded fallback cannot execute a blocked seat.
            overrides['fallback']['qoder'] = ['codex-sol', 'claude']
            write_legacy_local(local, overrides)
            r = sh(cmd + ['--no-findings'], env=env)
            check(r.returncode != 0 and '顶替跳过 claude' in r.stderr, r.stderr)
            r = sh([sys.executable, str(REPO/'ai_worker.py'), 'doctor'], env=env)
            check(f'主会话 {main}' in r.stdout and '主会话排除（main_exclude）' in r.stdout
                  and 'main_exclude 禁止' in r.stdout, r.stdout)
        # Unknown identity and existing main sessions retain their default behavior.
        for main in ('unknown', 'claude', 'codex'):
            cmd[2] = 'codex-sol'
            env.update(AIW_MAIN=main, FAKE_AU_QODER='ok', CLAUDECODE='', AIW_STATE=str(t / ('unchanged-' + main)))
            r = sh(cmd + ['--no-findings'], env=env)
            check(r.returncode == 0, main + ': ' + r.stderr)


def main_exclude_panel():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); local = t/'local.json'
        for main in ('pi', 'omp'):
            write_legacy_local(local, {'panel_default': ['codex-sol', 'claude', 'qoder', 'workbuddy', 'opencode'],
                'panel_free': ['codex-sol', 'qoder', 'workbuddy', 'opencode'],
                'self_seats': {main: ['opencode']}})
            for flags in ([], ['--free'], ['codex-sol', 'claude', 'qoder', 'workbuddy']):
                state = t/(main + '-' + str(len(flags)))
                env = dict(os.environ, AIW_CONFIG=str(local), AIW_STATE=str(state), AIW_MAIN=main)
                r = sh([sys.executable, str(REPO/'panel.py'), str(t), '10', 'test', *flags], env=env)
                check(r.returncode == 0, r.stdout+r.stderr)
                summary = json.loads(next((state/'panels').glob('*/summary.json')).read_text())
                check([row['seat'] for row in summary['seats']] == ['qoder', 'workbuddy'], summary)
            r = sh([sys.executable, str(REPO/'panel.py'), str(t), '10', 'test', 'codex-sol', 'claude'], env=env)
            check(r.returncode == 1 and '没有可用座位' in r.stderr, r.stderr)


CASES.update({f.__name__: f for f in (main_agent_priority, main_exclude_config,
                                   main_exclude_dispatch, main_exclude_panel)})
