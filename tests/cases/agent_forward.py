"""Pure offline checks of forwarding identity, no model/remote process."""
import json
import sys
import tempfile
from pathlib import Path
from unittest import mock

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


def forwarding_runs_here_when_it_cannot_carry_the_request():
    # #867: an --effort the receiver's rows would not carry, or a forwarder that cannot check
    # identities, keeps the dispatch here (None) instead of failing it with 64.
    import ai_worker as w
    row = {'kind': 'cli', 'bin': 'tool', 'argv_rw': ['{bin}', '{prompt}'], 'argv_ro': ['{bin}', '{prompt}'],
           'model': 'm', 'effort': 'medium', 'model_args': ['--effort', '{effort}']}
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        prompt = t / 'p.md'; prompt.write_text('x')
        marker = t / 'called'
        def forwarder(protocols):
            script = t / f'forward-{len(protocols)}.py'
            script.write_text('import json,sys,pathlib\n'
                              'if sys.argv[1:] == ["capabilities"]:\n'
                              f'    print(json.dumps({{"protocols": {json.dumps(protocols)}}})); sys.exit(0)\n'
                              f'pathlib.Path({str(marker)!r}).write_text("x"); sys.exit(0)\n')
            return [sys.executable, str(script)]
        with mock.patch.dict(w.SEATS, {'capabilities': ['agent-runtime-v1']}), mock.patch.dict(w.SEATS['seats'], {'seatx': row}):
            for command, values in ((forwarder(['agent-desc-v1']), {'effort': 'high'}), (forwarder([]), {})):
                marker.unlink(missing_ok=True)
                rc = w.forward_dispatch(command, 'seatx', td, '60', str(prompt), {'--ro'}, [], values, {}, chain=['seatx'])
                check(rc is None, f'expected local execution, got {rc!r}')
                check(not marker.exists(), 'forwarder was asked to run it')


def forwarding_named_seat_requires_the_same_name():
    """点名座位带 exact：远端没有同名行时不得换成等价别名。"""
    row = {'kind': 'cli', 'bin': 'tool', 'argv_rw': ['w'], 'argv_ro': ['r'], 'model': 'm'}
    request = describe({'seats': {'picked': row}}, ['picked'], 'ro', '/a', exact={'picked'})
    check(request['entries'][0].get('exact') is True, request)
    try:
        resolve({'seats': {'alias': row}}, request, '/b')
        check(False, '点名座位被换成远端别名')
    except ValueError:
        pass
    check(resolve({'seats': {'picked': row, 'alias': row}}, request, '/b') == {'picked': 'picked'}, '同名行未保留')
    check('exact' not in describe({'seats': {'picked': row}}, ['picked'], 'ro', '/a')['entries'][0], '角色链不应要求同名')


CASES = {f.__name__: f for f in (forwarding_identity_preserves_account_model_and_names, forwarding_identity_rejects_ambiguity_and_write_relaxation,
                                 forwarding_named_seat_requires_the_same_name,
                                 forwarding_runs_here_when_it_cannot_carry_the_request)}


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
