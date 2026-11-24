"""Pure offline checks of forwarding identity, no model/remote process."""
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

from support import REPO, check, sh
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


def agent_runtime_table(seats):
    table = {'capabilities': ['agent-runtime-v1'], 'seats': seats,
             'fallback': {}, 'self_seats': {}, 'substitute_limit': {},
             'panel_default': [], 'panel_free': []}
    table['version'] = hashlib.sha256(json.dumps(
        table, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return table


def resolve_entry_returns_66_only_for_missing_equivalent():
    """入口 main 只把"没有同名等价座位"映射成明确拒收 66，其他错误仍是 64。"""
    row = {'kind': 'cli', 'bin': 'tool', 'argv_rw': ['w'], 'argv_ro': ['r']}
    request = describe({'seats': {'picked': row}}, ['picked'], 'ro', '/a', exact={'picked'})
    with tempfile.TemporaryDirectory() as td:
        config = Path(td) / 'receiver.json'

        def run(seats, payload=None):
            config.write_text(json.dumps(agent_runtime_table(seats)))
            return sh([sys.executable, str(REPO / 'agent_forward.py'), 'resolve'],
                      env=dict(os.environ, AIW_CONFIG=str(config)),
                      input=json.dumps(request if payload is None else payload))

        for seats in ({}, {'alias': row}):
            r = run(seats)
            check(r.returncode == 66 and '未执行' in r.stderr, (seats, r))
        r = run({'picked': row})
        check(r.returncode == 0 and json.loads(r.stdout)['mapping'] == {'picked': 'picked'}, r)
        r = run({'picked': row}, dict(request, mode='bogus'))
        check(r.returncode == 64, r)


def forwarding_named_seat_matches_same_recipe_same_mode_across_names():
    """#1332：两台账号叫法不同，同一座位名字不同；内容和权限档都相同的那一行可以接。"""
    base = {'kind': 'cli', 'bin': 'claude', 'argv_rw': ['w'], 'argv_ro': ['r'], 'model': 'm', 'effort': 'medium'}
    rows = lambda prefix: {f'{prefix}-{m}': dict(base, force_mode=m) for m in ('rw', 'ro', 'text')}
    sender, receiver = rows('here'), rows('there')
    for m in ('rw', 'ro', 'text'):
        request = describe({'seats': sender}, [f'here-{m}'], m, '/a', exact={f'here-{m}'})
        check(resolve({'seats': receiver}, request, '/b') == {f'here-{m}': f'there-{m}'}, m)
    request = describe({'seats': sender}, ['here-ro'], 'ro', '/a', exact={'here-ro'})
    for seats in ({'there-rw': receiver['there-rw'], 'there-text': receiver['there-text']},
                  {'there-ro': dict(receiver['there-ro'], model='other')},
                  {'there-ro': receiver['there-ro'], 'twin-ro': receiver['there-ro']}):
        try:
            resolve({'seats': seats}, request, '/b')
            check(False, f'wrong mode/recipe or ambiguous accepted: {sorted(seats)}')
        except ValueError:
            pass


def forwarding_digest_tracks_effective_effort_and_same_name_first():
    """评审 #1332：强度写空（工具默认）和没写（按 low）执行不同，摘要须不同；
    点名座位在执行端有同名行时只认它，不因内容变了改借别名。"""
    from agent_forward import fingerprint
    base = {'kind': 'cli', 'bin': 'tool', 'argv_rw': ['w'], 'argv_ro': ['r'], 'model': 'm', 'force_mode': 'ro',
            'model_args': ['-m', '{model}', '--effort', '{effort}']}
    generated = dict(base, effort='', agent_pick='a/b:m')
    check(fingerprint(generated, '/a') != fingerprint(dict(base, effort='<cli-default>'), '/a'), 'marker collides with a literal')
    plain = {k: v for k, v in base.items() if k != 'model_args'}
    check(fingerprint(dict(plain, effort='high'), '/a') == fingerprint(plain, '/a'), 'unused effort changes digest')
    check(fingerprint(generated, '/a') != fingerprint(base, '/a'), 'cli default equals implicit low')
    check(fingerprint(base, '/a') == fingerprint(dict(base, effort='low'), '/a'), 'implicit low differs from explicit low')
    check(fingerprint(dict(base, effort='high'), '/a') == fingerprint(dict(base, effort='high', agent_pick='x'), '/a'), 'explicit effort depends on pick')
    request = describe({'seats': {'src': base}}, ['src'], 'ro', '/a', exact={'src'})
    try:
        resolve({'seats': {'src': dict(base, model='changed', disabled=True), 'dst': base}}, request, '/b')
        check(False, 'same-name row bypassed through alias')
    except ValueError:
        pass
    check(resolve({'seats': {'dst': base}}, request, '/b') == {'src': 'dst'}, 'cross-name same mode rejected')
    try:
        resolve({'seats': {'src': dict(base, model='changed', disabled=True), 'dst': base}},
                describe({'seats': {'src': base}}, ['src'], 'ro', '/a'), '/b')
        check(False, 'role chain bypassed same-name row through alias')
    except ValueError:
        pass
    # 角色链（非点名）多候选不再筛权限档，维持原来的拒绝。
    loose = describe({'seats': {'r': base}}, ['r'], 'ro', '/a')
    try:
        resolve({'seats': {'x': base, 'y': dict(base, force_mode='rw')}}, loose, '/b')
        check(False, 'role chain narrowed by mode')
    except ValueError:
        pass


def runnable_lists_digests_per_requested_mode():
    from agent_forward import runnable
    base = {'kind': 'cli', 'bin': 'tool', 'argv_rw': ['w'], 'argv_ro': ['r'], 'model': 'm'}
    seats = {'a-rw': dict(base, force_mode='rw'), 'a-ro': dict(base, force_mode='ro'),
             'gone': dict(base, model='x', local_only=True), 'w-only': {'kind': 'cli', 'bin': 't', 'argv_rw': ['w']}}
    got = runnable({'seats': seats}, '/a')
    digest = describe({'seats': seats}, ['a-ro'], 'ro', '/a')['entries'][0]['digest'][:16]
    check(digest in got['ro'] and digest in got['text'] and digest in got['rw'], got)
    wonly = describe({'seats': seats}, ['w-only'], 'rw', '/a')['entries'][0]['digest'][:16]
    check(wonly in got['rw'] and wonly not in got['ro'], got)
    check(len(set(got['rw'])) == 2, got)


CASES = {f.__name__: f for f in (forwarding_identity_preserves_account_model_and_names,
                                 forwarding_named_seat_matches_same_recipe_same_mode_across_names,
                                 runnable_lists_digests_per_requested_mode,
                                 forwarding_digest_tracks_effective_effort_and_same_name_first, forwarding_identity_rejects_ambiguity_and_write_relaxation,
                                 forwarding_named_seat_requires_the_same_name,
                                 forwarding_runs_here_when_it_cannot_carry_the_request,
                                 resolve_entry_returns_66_only_for_missing_equivalent)}


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
