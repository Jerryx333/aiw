"""Pure offline checks of forwarding identity, no model/remote process."""
from support import check
from agent_forward import describe, resolve


def forwarding_identity_preserves_account_model_and_names():
    row = {'kind': 'cli', 'adapter': 'codex', 'bin': '/home/a/tool', 'argv_rw': ['w'], 'argv_ro': ['r'],
           'model': 'm', 'account': {'codex_home': '/home/a/account'}}
    source = {'seats': {'agent-one': row}}
    remote = {'seats': {'old-name': {**row, 'bin': '/home/b/tool', 'account': {'codex_home': '/home/b/account'}}}}
    request = describe(source, ['agent-one'], 'ro', '/home/a')
    check(resolve(remote, request, '/home/b') == {'agent-one': 'old-name'}, 'remote name not retained')
    for change in ({'model': 'other'}, {'account': {'codex_home': '/home/b/other'}}, {'local_only': True}):
        try:
            resolve({'seats': {'old-name': {**remote['seats']['old-name'], **change}}}, request, '/home/b')
            check(False, 'mismatched identity accepted')
        except ValueError:
            pass
    check('account' not in str(request) and '/home' not in str(request), 'raw recipe leaked')


def forwarding_identity_rejects_ambiguity_and_write_relaxation():
    row = {'kind': 'cli', 'bin': 'tool', 'argv_rw': ['w'], 'argv_ro': ['r'], 'model': 'm'}
    request = describe({'seats': {'agent-one': row}}, ['agent-one'], 'rw', '/a')
    for seats in ({'one': row, 'two': row}, {'one': {**row, 'ro_only': True}}):
        try:
            resolve({'seats': seats}, request, '/b')
            check(False, 'ambiguous/readonly match accepted')
        except ValueError:
            pass
    try:
        describe({'seats': {'one': row, 'two': {**row, 'local_only': True}}}, ['one', 'two'], 'rw', '/a')
        check(False, 'local-only backup forwarded')
    except ValueError:
        pass


CASES = {f.__name__: f for f in (forwarding_identity_preserves_account_model_and_names, forwarding_identity_rejects_ambiguity_and_write_relaxation)}


def changed_runtime_rejected_before_any_execution():
    from support import tempfile, Path, worker_call, sh
    with tempfile.TemporaryDirectory() as td:
        cmd, env, wd = worker_call(Path(td))
        env['AIW_EXPECT_CONFIG_HASH'] = 'not-current'
        result = sh(cmd, env=env)
        check(result.returncode != 0 and '核对后配置变化' in result.stderr, result)

CASES['changed_runtime_rejected_before_any_execution'] = changed_runtime_rejected_before_any_execution


def forwarding_remote_name_keeps_disabled_state():
    from support import tempfile, Path, worker_call, sh, json
    import os
    with tempfile.TemporaryDirectory() as td:
        cmd, env, wd = worker_call(Path(td))
        state = Path(env['AIW_STATE'])
        state.mkdir(exist_ok=True)
        (state / 'disabled.json').write_text(json.dumps({'qoder': {'disabled_by_user': 'offline test'}}))
        result = sh(cmd + ['--chain', 'qoder', '--no-fallback'], env=env)
        check(result.returncode != 0 and 'qoder ok' not in result.stdout, result)

CASES['forwarding_remote_name_keeps_disabled_state'] = forwarding_remote_name_keeps_disabled_state


def forwarding_preserves_disabled_original_and_strict_backup():
    row = {'kind': 'cli', 'bin': 'tool', 'argv_rw': ['w'], 'argv_ro': ['r']}
    request = describe({'seats': {'a': dict(row, disabled=True)}}, ['a'], 'ro', '/a')
    check(resolve({'seats': {'a': dict(row, disabled=True), 'alias': row}}, request, '/a') == {'a': 'a'}, 'disabled identity bypassed')
    request = describe({'seats': {'a': row, 'b': dict(row, force_mode='text')}}, ['a', 'b'], 'ro', '/a')
    check(resolve({'seats': {'a': row, 'b': dict(row, force_mode='text')}}, request, '/a') == {'a': 'a', 'b': 'b'}, 'strict fallback rejected')
    try:
        resolve({'seats': {'a': row, 'b': row}}, request, '/a')
        check(False, 'text ceiling relaxed')
    except ValueError:
        pass

CASES['forwarding_preserves_disabled_original_and_strict_backup'] = forwarding_preserves_disabled_original_and_strict_backup
