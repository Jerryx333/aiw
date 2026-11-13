"""Offline agent-use workspace seat contracts."""
from support import (FINDINGS_BLOCK, Path, REPO, check, fake_agent_use, git_template, json, os,
                     sys, tempfile, worker_call, inprocess_sh as sh)
from unittest.mock import patch


def fixture(t, **extra):
    local = t / 'local.json'
    local.write_text(json.dumps({'seats': {
        'workspace': {'kind': 'agent_use', 'provider': 'generic', 'workspace': True},
        'text': {'kind': 'agent_use', 'provider': 'text', 'require_no_tools': True}},
        'roles': {'default': {'write': ['workspace'], 'review': ['workspace'], 'draft': ['workspace']}},
        'fallback': {'workspace': [], 'codex': ['text', 'workspace']}}))
    cmd, env, wd = worker_call(t, AIW_CONFIG=str(local), AIW_STATE=str(t / 'state'),
                               AGENT_USE_HOME=str(fake_agent_use(t)),
                               FAKE_AU_LOG=str(t / 'calls'), FAKE_AU_WORKSPACE_LOG='1', AIW_MAIN='', **extra)
    cmd[2] = 'workspace'
    return cmd, env, wd


def calls(t):
    return [json.loads(line) for line in (t / 'calls').read_text().splitlines()]


def workspace_role_write():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); cmd, env, wd = fixture(t, FAKE_AU_GENERIC='write')
        git_template(wd, {'tracked': 'initial'})
        cmd[2] = 'write'; cmd.remove('--ro')
        r = sh(cmd + ['--no-findings'], env=env)
        check(r.returncode == 0 and '角色 write → 座位 workspace' in r.stderr, r.stderr)
        row = json.loads((t / 'state/seat-ledger.jsonl').read_text().splitlines()[-1])
        check(row['changed_files'] == 2 and row['actual'] == 'workspace' and row['ro'] is False, row)
        check((Path(row['run_dir']) / 'baseline-status.txt').exists(), row)
        check('tracked' in r.stderr and 'created' in r.stderr, r.stderr)
        call = calls(t)[0]
        check(call['workdir'] == str(wd.resolve()) and call['access'] == 'rw' and not call['no_tools'], call)


def workspace_access_ro_rw():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); cmd, env, wd = fixture(t)
        (wd / 'code.txt').write_text('repository material')
        for role, readonly in [('workspace', True), ('review', True), ('workspace', False)]:
            cmd[2] = role
            flags = ['--ro'] if readonly and role == 'workspace' else []
            r = sh(cmd[:-1] + flags + ['--net', '--attach', 'code.txt', '--no-findings'], env=env)
            check(r.returncode == 0 and '--net 不生效' in r.stderr, r.stderr)
            run_dir = sorted((t / 'state/runs').iterdir(), key=lambda p: p.stat().st_mtime_ns)[-1]
            prompt = (run_dir / 'task.txt').read_text()
            check('code.txt |' in prompt and 'repository material' not in prompt, prompt)
            call = calls(t)[-1]; argv = call['argv']
            check(call['access'] == ('ro' if readonly else 'rw') and call['workdir'] == str(wd.resolve()), call)
            check('--workdir' in argv and '--access' in argv and '--require-no-tools' not in argv, call)
        import ai_worker as w
        with patch.object(w, 'SEATS', {'seats': {'workspace': {'kind': 'agent_use', 'workspace': True, 'ro_only': True}}}):
            check(w.role_candidate_reason('write', 'workspace') is not None, 'ro_only allowed write')
        local = Path(env['AIW_CONFIG']); cfg = json.loads(local.read_text())
        cfg['seats']['workspace']['ro_only'] = True; local.write_text(json.dumps(cfg))
        r = sh(cmd[:-1] + ['--no-findings'], env=env)
        check(r.returncode == 0 and calls(t)[-1]['access'] == 'ro', r.stderr)
        run_dir = sorted((t / 'state/runs').iterdir(), key=lambda p: p.stat().st_mtime_ns)[-1]
        prompt = (run_dir / 'task.txt').read_text()
        check('code.txt |' not in prompt, prompt)  # last call has no attachments


def workspace_text_unchanged():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); cmd, env, wd = fixture(t); cmd[2] = 'text'
        r = sh(cmd, env=env)
        check(r.returncode == 0, r.stderr)
        call = calls(t)[0]
        check(call['no_tools'] and call['access'] == 'none' and call['workdir'] is None, call)
        check('--workdir' not in call['argv'] and '--access' not in call['argv'], call)
        r = sh(cmd[:-1], env=env)
        check(r.returncode == 64 and '只生成文本' in r.stderr and len(calls(t)) == 1, r.stderr)
        import ai_worker as w
        with patch.object(w, 'SEATS', {'seats': {'text': {'kind': 'agent_use'}}}):
            result = w._run_seat('text', 'test', str(wd), 10, False)
            check(result['kind'] == 'invalid_argv', result)


def assert_text_call(call):
    check(call['no_tools'] and call['access'] == 'none' and call['workdir'] is None, call)
    check('--require-no-tools' in call['argv'] and '--workdir' not in call['argv']
          and '--access' not in call['argv'], call)


def workspace_draft_text():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); cmd, env, wd = fixture(t)
        (wd / 'code.txt').write_text('repository material')
        cmd[2] = 'draft'; cmd.remove('--ro')
        r = sh(cmd + ['--attach', 'code.txt', '--no-findings'], env=env)
        check(r.returncode == 0, r.stderr)
        assert_text_call(calls(t)[0])
        row = json.loads((t / 'state/seat-ledger.jsonl').read_text().splitlines()[-1])
        check(row['text'] is True and row['ro'] is True, row)
        prompt = (Path(row['run_dir']) / 'task.txt').read_text()
        check('repository material' in prompt, prompt)


def workspace_explicit_text():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); cmd, env, _ = fixture(t); cmd.remove('--ro')
        r = sh(cmd + ['--text', '--no-findings'], env=env)
        check(r.returncode == 0, r.stderr)
        assert_text_call(calls(t)[0])
        row = json.loads((t / 'state/seat-ledger.jsonl').read_text().splitlines()[-1])
        check(row['text'] is True and row['ro'] is True, row)
        # Text does not add a tool restriction to a non-workspace seat.
        local = Path(env['AIW_CONFIG']); cfg = json.loads(local.read_text())
        cfg['seats']['text']['require_no_tools'] = False; local.write_text(json.dumps(cfg))
        cmd[2] = 'text'
        r = sh(cmd + ['--text'], env=env)
        check(r.returncode == 0 and not calls(t)[-1]['no_tools'], r.stderr)
        cmd[2] = 'codex'; marker = t / 'cli-argv'; env['FAKE_CODEX_ARGS'] = str(marker)
        r = sh(cmd + ['--text'], env=env)
        check(r.returncode == 0 and 'read-only' in marker.read_text()
              and '--require-no-tools' not in marker.read_text(), r.stderr)


def workspace_text_net_conflict():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); cmd, env, _ = fixture(t); cmd.remove('--ro')
        for seat, flags in [('workspace', ['--text']), ('draft', []),
                            ('text', ['--text']), ('codex', ['--text'])]:
            cmd[2] = seat
            r = sh(cmd + flags + ['--net'], env=env)
            check(r.returncode == 64 and '不能与 --ro' in r.stderr, r.stderr)
        check(not (t / 'calls').exists(), 'conflicting dispatch executed')


def workspace_text_fallback():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); cmd, env, _ = fixture(t, FAKE_CODEX='quota', FAKE_AU_TEXT='quota')
        local = Path(env['AIW_CONFIG']); cfg = json.loads(local.read_text())
        cfg['seats']['workspace2'] = dict(cfg['seats']['workspace'], provider='generic2')
        cfg['fallback']['codex'] = ['text', 'workspace', 'workspace2']
        local.write_text(json.dumps(cfg)); env['FAKE_AU_GENERIC'] = 'quota'
        cmd[2] = 'codex'; cmd.remove('--ro')
        r = sh(cmd + ['--text', '--no-findings'], env=env)
        check(r.returncode == 0, r.stderr)
        check([c['order'] for c in calls(t)] == ['text', 'generic', 'generic2'], calls(t))
        for call in calls(t):
            assert_text_call(call)
        rows = [json.loads(line) for line in (t / 'state/seat-ledger.jsonl').read_text().splitlines()]
        check(len(rows) == 4 and all(row['text'] is True and row['ro'] is True for row in rows), rows)


def workspace_config_conflict():
    import seats_config as c
    cfg = c.load_public()
    cfg['seats']['workspace'] = {'kind': 'agent_use', 'provider': 'generic', 'workspace': True, 'require_no_tools': True}
    check(any('workspace 与 require_no_tools' in e for e in c.validate(cfg)), c.validate(cfg))
    cfg['seats']['workspace']['require_no_tools'] = False
    for value in ('true', 1, None):
        cfg['seats']['workspace']['workspace'] = value
        check(any('workspace 必须是布尔值' in e for e in c.validate(cfg)), value)
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); cmd, env, _ = fixture(t)
        local = Path(env['AIW_CONFIG']); cfg = json.loads(local.read_text())
        cfg['seats']['workspace']['require_no_tools'] = True; local.write_text(json.dumps(cfg))
        r = sh(cmd, env=env)
        check(r.returncode != 0 and 'workspace 与 require_no_tools' in r.stderr and not (t / 'calls').exists(), r.stderr)
        r = sh([sys.executable, str(REPO / 'ai_worker.py'), 'doctor'], env=env)
        check(r.returncode != 0 and 'workspace 与 require_no_tools' in r.stdout + r.stderr, r.stdout + r.stderr)


def workspace_fallback_skips_text():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); cmd, env, wd = fixture(t, FAKE_CODEX='quota', FAKE_AU_GENERIC='write')
        git_template(wd, {'tracked': 'initial'}); cmd[2] = 'codex'; cmd.remove('--ro')
        r = sh(cmd + ['--no-findings'], env=env)
        check(r.returncode == 0 and '[text] 顶替跳过：文本座位不能改文件' in r.stderr, r.stderr)
        check([call['order'] for call in calls(t)] == ['generic'] and calls(t)[0]['access'] == 'rw', calls(t))
        rows = [json.loads(line) for line in (t / 'state/seat-ledger.jsonl').read_text().splitlines()]
        check(rows[-1]['actual'] == 'workspace' and rows[-1]['changed_files'] == 2, rows)


def workspace_write_fallback_skips_ro_only():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); cmd, env, wd = fixture(t, FAKE_CODEX='quota', FAKE_AU_GENERIC='write')
        local = Path(env['AIW_CONFIG']); cfg = json.loads(local.read_text())
        cfg['seats']['workspace-ro'] = dict(cfg['seats']['workspace'], provider='readonly', ro_only=True)
        cfg['fallback']['codex'] = ['workspace-ro', 'workspace']
        local.write_text(json.dumps(cfg))
        git_template(wd, {'tracked': 'initial'}); cmd[2] = 'codex'; cmd.remove('--ro')
        r = sh(cmd + ['--no-findings'], env=env)
        check(r.returncode == 0, r.stderr)
        check([c['order'] for c in calls(t)] == ['generic'], '写单调用了 ro_only workspace 顶替')
        # Candidates are filtered before forwarding or execution; the call log is
        # the invariant, not a fallback-loop message from the old implementation.
        rows = [json.loads(line) for line in (t / 'state/seat-ledger.jsonl').read_text().splitlines()]
        check([row['actual'] for row in rows] == ['codex', 'workspace']
              and rows[-1]['changed_files'] == 2 and rows[-1]['ro'] is False, rows)
        # Read-only dispatch still permits the same fallback seat.
        r = sh(cmd + ['--ro', '--no-findings'], env=env)
        check(r.returncode == 0 and calls(t)[-1]['order'] == 'readonly'
              and calls(t)[-1]['access'] == 'ro', r.stderr)


def check_panel_attachment_scope(seats, text_mode=False):
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); cmd, env, wd = fixture(t)
        local = Path(env['AIW_CONFIG']); cfg = json.loads(local.read_text())
        cfg['seats']['workspace2'] = dict(cfg['seats']['workspace'], provider='generic2')
        local.write_text(json.dumps(cfg))
        (wd / 'code.txt').write_text('repository material')
        flags = ['--text'] if text_mode else []
        r = sh([sys.executable, str(REPO / 'panel.py'), str(wd), '10', cmd[5],
                *seats, '--attach', 'code.txt', '--full', *flags], env=env)
        check(r.returncode == 0, r.stdout + r.stderr)
        prompt = next((t / 'state/panels').glob('*/prompt.md')).read_text()
        check('repository material' in prompt, prompt)
        limited = text_mode or 'text' in seats or 'codex' in seats
        check(('不要假设还能读到其它文件' in prompt) == limited, 'panel 附件范围提示错误: ' + prompt)
        if text_mode:
            for call in calls(t):
                assert_text_call(call)


def workspace_panel_mixed_attachment_scope():
    check_panel_attachment_scope(['workspace', 'text'])
    check_panel_attachment_scope(['workspace', 'codex'])


def workspace_panel_all_workspace_attachment_scope():
    check_panel_attachment_scope(['workspace', 'workspace2'])


def workspace_panel_text_attachment_scope():
    check_panel_attachment_scope(['workspace', 'workspace2'], text_mode=True)


def workspace_panel_map():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); cmd, env, wd = fixture(t)
        (wd / 'code.txt').write_text('repository material')
        r = sh([sys.executable, str(REPO / 'panel.py'), str(wd), '10', cmd[5],
                'workspace', 'text', '--attach', 'code.txt', '--full'], env=env)
        check(r.returncode == 0, r.stdout + r.stderr)
        check(all(c['access'] == ('ro' if c['order'] == 'generic' else 'none') for c in calls(t)), calls(t))
        prompt = next((t / 'state/panels').glob('*/prompt.md')).read_text()
        check('repository material' in prompt and '不要假设还能读到其它文件' in prompt, prompt)
        r = sh([sys.executable, str(REPO / 'map.py'), str(wd), '10', cmd[5],
                'code.txt', '--seat', 'workspace', '--jobs', '1'], env=env)
        check(r.returncode == 0, r.stdout + r.stderr)
        assert_text_call(calls(t)[-1])
        row = json.loads((t / 'state/seat-ledger.jsonl').read_text().splitlines()[-1])
        check(row['via'] == 'map' and row['text'] is True and row['ro'] is True, row)
        check('repository material' in (Path(row['run_dir']) / 'task.txt').read_text(), row)
        r = sh([sys.executable, str(REPO / 'ai_worker.py'), 'doctor'], env=env)
        check('可读写仓库' in r.stdout and 'seats.workspace.workspace' not in r.stdout, r.stdout + r.stderr)


def workspace_findings_context():
    import findings
    import snapshot
    cfg = {'seats': {'text': {'kind': 'agent_use'},
                     'workspace': {'kind': 'agent_use', 'workspace': True}}}
    with tempfile.TemporaryDirectory() as td, patch.object(findings, '_settings', return_value=cfg):
        check(not findings.common_fields(td, seat='text', actual='workspace')['partial_context'], 'workspace partial context')
        check(findings.common_fields(td, seat='workspace', actual='text')['partial_context'], 'text full context')
    check(snapshot.seat_shape({'kind': 'agent_use', 'provider': 'generic', 'workspace': True})['workspace'] is True, 'snapshot missing workspace')
    check('workspace' not in snapshot.seat_shape({'kind': 'agent_use', 'provider': 'text'}), 'text snapshot changed')



def workspace_sync_core_failure():
    import shutil
    with tempfile.TemporaryDirectory() as td:
        t = Path(td).resolve(); repo = t / 'aiw'; (repo / 'bin').mkdir(parents=True)
        for name in ('bin/aiw', 'agent_use_setup.sh', 'env_compat.py'):
            shutil.copyfile(REPO / name, repo / name)
        core = t / 'core'; core.mkdir()
        adapters = t / 'adapters'; adapters.mkdir()
        bins = t / 'bin'; bins.mkdir(); log = t / 'git-calls'
        git = bins / 'git'
        git.write_text('''#!/bin/sh
if [ "$3" = rev-parse ]; then
  [ "$2" = "$AGENT_USE_HOME" ] && [ "$CORE_MODE" = nongit ] && exit 1
  echo "$2"; exit 0
fi
printf '%s\\n' "$2" >> "$GIT_CALLS"
[ "$2" = "$AGENT_USE_HOME" ] && [ "$CORE_MODE" = fail ] && exit 1
exit 0
''')
        git.chmod(0o755)
        env = dict(os.environ, PATH=str(bins) + os.pathsep + os.environ['PATH'],
                   AGENT_USE_HOME=str(core), AIW_ADAPTERS_HOME=str(adapters),
                   AIW_PYTHON=sys.executable, GIT_CALLS=str(log))
        for mode, expected in [('fail', [repo, core]), ('ok', [repo, core, adapters]),
                               ('nongit', [repo, adapters])]:
            env['CORE_MODE'] = mode
            log.write_text('')
            r = sh(['bash', str(repo / 'bin/aiw'), 'sync'], env=env)
            check(r.returncode == 0, r.stdout + r.stderr)
            check(log.read_text().splitlines() == list(map(str, expected)),
                  'sync 更新顺序错误: ' + log.read_text())
            check(('核心更新失败，跳过适配器更新以免版本错配' in r.stdout) == (mode == 'fail'), r.stdout)


def workspace_old_agent_use_fallback():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); cmd, env, _ = fixture(t)
        script = Path(env['AGENT_USE_HOME']) / 'scripts/run_agent.py'
        script.write_text(script.read_text().replace('"--access", choices=', '"--legacy-access", dest="access", choices='))
        local = Path(env['AIW_CONFIG']); cfg = json.loads(local.read_text())
        cfg['fallback']['workspace'] = ['text']; local.write_text(json.dumps(cfg))
        r = sh(cmd + ['--no-findings'], env=env)
        check(r.returncode == 0 and 'agent-use 版本过旧，不支持 workspace' in r.stderr, r.stderr)
        rows = [json.loads(line) for line in (t / 'state/seat-ledger.jsonl').read_text().splitlines()]
        check([(row['actual'], row['kind']) for row in rows] ==
              [('workspace', 'incompatible_agent_use'), ('text', 'ok')], rows)
        check([c['order'] for c in calls(t)] == ['text'], calls(t))
        breaker = t / 'state/breaker.json'
        check(not breaker.exists() or 'workspace' not in json.loads(breaker.read_text()), '旧版本触发熔断')
        # The same old script still accepts text mode on a workspace seat.
        r = sh(cmd + ['--text', '--no-findings'], env=env)
        check(r.returncode == 0 and calls(t)[-1]['order'] == 'generic', r.stderr)


def workspace_capability_cache():
    import ai_worker as w
    with tempfile.TemporaryDirectory() as td:
        script = Path(td) / 'run_agent.py'; script.write_text('old')
        check(not w.agent_use_supports_workspace(script), 'old script accepted')
        with patch.object(Path, 'read_text', side_effect=AssertionError('cache missed')):
            check(not w.agent_use_supports_workspace(script), 'cached old script accepted')
        mtime = script.stat().st_mtime_ns
        script.write_text('--access')
        os.utime(script, ns=(mtime + 1000000000, mtime + 1000000000))
        check(w.agent_use_supports_workspace(script), 'mtime change not detected')
        other = Path(td) / 'other.py'; other.write_text('old')
        os.utime(other, ns=(mtime + 1000000000, mtime + 1000000000))
        check(not w.agent_use_supports_workspace(other), 'cache ignored path')


def workspace_findings_execution_mode():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); cmd, env, wd = fixture(t)
        output = t / 'output'; output.write_text('正文\n' + FINDINGS_BLOCK)
        env['FAKE_FINDINGS_OUTPUT'] = str(output)
        (wd / 'code.py').write_text('print(1)')
        for seat, flags, partial in [('draft', [], True), ('workspace', ['--text'], True),
                                      ('review', [], False)]:
            cmd[2] = seat
            r = sh(cmd + flags + ['--attach', 'code.py'], env=env)
            check(r.returncode == 0, r.stderr)
            rows = [json.loads(line) for line in (t / 'state/findings/records.jsonl').read_text().splitlines()]
            latest = [row for row in rows if row['run_dir'] == rows[-1]['run_dir']]
            check(latest and all(row['partial_context'] is partial for row in latest),
                  f'{seat} partial_context 错误: {latest}')


CASES = {fn.__name__: fn for fn in (workspace_role_write, workspace_access_ro_rw,
    workspace_text_unchanged, workspace_draft_text, workspace_explicit_text,
    workspace_text_net_conflict, workspace_text_fallback, workspace_config_conflict, workspace_fallback_skips_text,
    workspace_write_fallback_skips_ro_only, workspace_panel_mixed_attachment_scope,
    workspace_panel_all_workspace_attachment_scope, workspace_panel_text_attachment_scope,
    workspace_panel_map, workspace_findings_context, workspace_sync_core_failure,
    workspace_old_agent_use_fallback, workspace_capability_cache, workspace_findings_execution_mode)}

def workspace_forced_text_backup_keeps_attachment_body():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); cmd, env, wd = fixture(t, FAKE_CODEX='quota')
        local = Path(env['AIW_CONFIG']); cfg = json.loads(local.read_text())
        cfg['seats']['workspace']['force_mode'] = 'text'
        cfg['fallback']['codex'] = ['workspace']; local.write_text(json.dumps(cfg))
        (wd / 'material.txt').write_text('UNIQUE_ATTACHMENT_CONTENT')
        cmd[2] = 'codex'
        r = sh(cmd + ['--attach', str(wd / 'material.txt'), '--no-findings'], env=env)
        check(r.returncode == 0, r.stderr)
        call = calls(t)[0]
        check('UNIQUE_ATTACHMENT_CONTENT' in Path(call['argv'][0]).read_text(), Path(call['argv'][0]).read_text())
        rows = [json.loads(line) for line in (t / 'state/seat-ledger.jsonl').read_text().splitlines()]
        check(rows[-1].get('text') is True and rows[-1]['task_class'] == 'draft', rows[-1])

CASES['workspace_forced_text_backup_keeps_attachment_body'] = workspace_forced_text_backup_keeps_attachment_body
