"""config errands regression cases."""
from __future__ import annotations

from support import (
    FAKE_AGENT_USE,
    Path,
    REPO,
    check,
    errand_fixture,
    errand_route_test,
    fake_agent_use,
    findings_fixture,
    json,
    os,
    inprocess_sh as sh,
    shutil,
    subprocess,
    sys,
    tempfile,
    worker_call,
    write_legacy_local,
)


def errand_registry_corrupt_rejected():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); env = errand_fixture(t)
        registry = Path(env['AGENT_USE_HOME']) / 'config/errands.json'
        valid = json.loads(registry.read_text())
        probe = "import ai_worker as w; import json; print(json.dumps(sorted(w.errand_names())))"
        r = sh([sys.executable, '-c', probe], cwd=REPO, env=env)
        check(r.returncode == 0 and json.loads(r.stdout) == ['cue', 'future-errand'],
              '合法 errand 名单读取失败')
        registry.unlink()
        r = sh([sys.executable, '-c', probe], cwd=REPO, env=env)
        check(r.returncode == 0 and json.loads(r.stdout) == [], '缺失 errand 名单不兼容')
        for name in ('route.py', 'dispatch.py', 'ai_worker.py', 'findings.py', 'harness_ids.py', 'gitenv.py', 'env_compat.py', 'seats_config.py', 'seats.json'):
            shutil.copy(REPO / name, t / name)
        (t / 'router-rules.json').write_text(json.dumps({'rules': [
            {'id': 'registry-test', 'seat': 'qoder', 'patterns': ['registry'], 'instruction': 'test'}]}))
        prompt = t / 'prompt'; prompt.write_text('test')
        commands = [
            ([sys.executable, str(t / 'route.py'), '--check'], 1, '跑腿配置无效'),
            ([sys.executable, str(t / 'route.py'), 'registry'], 1, '跑腿配置无效'),
            ([sys.executable, str(REPO / 'panel.py'), str(t), '10', str(prompt), 'qoder'], 64, '跑腿配置无效'),
            ([sys.executable, str(REPO / 'ai_worker.py'), 'qoder', str(t), '10', str(prompt), '--ro'], 64, '跑腿配置无效'),
            ([sys.executable, str(REPO / 'freecheck.py')], 1, 'StateError'),
        ]
        corrupt = [dict(valid, version=v) for v in (2, True, 1.0, '1', None)]
        corrupt += [dict(valid, providers=p) for p in ({}, [], {'Bad': {'argv': []}},
                    {'a' * 33: {'argv': []}}, {'bad\n': {'argv': []}}, {'cue': {}}, {'cue': []})]
        corrupt += [{'providers': valid['providers']}, {'version': 1}, None, []]
        for data in corrupt:
            registry.write_text(json.dumps(data))
            r = sh([sys.executable, '-c', probe], cwd=REPO, env=env)
            check(r.returncode != 0 and 'StateError' in r.stderr,
                  '损坏 errand 名单未拒绝: ' + repr(data))
            for cmd, rc, error in commands:
                r = sh(cmd, cwd=t, env=env)
                check(r.returncode == rc and error in r.stderr and not r.stdout,
                      '损坏 errand 名单入口未拒绝: ' + repr((cmd, data, r)))
        check(not (t / '.provider').exists() and not (t / 'state/panels').exists(),
              '损坏 errand 名单产生派单副作用')



def errand_dispatch():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); env = errand_fixture(t)
        for command in ('submit', 'wait', 'status', 'confirm', 'answer', 'stop', 'result', 'recover', 'list'):
            args = [command, '--task-file', '含 空格.md', '', 'a"b', '$HOME', '--wait', '3']
            r = sh([str(REPO / 'bin/aiw'), 'errand', *args], env=env)
            check(r.returncode == 20 and json.loads(r.stdout) == args, 'errand 参数或退出码未透传')
        # 环境变量未设置/空值时读取本地配置，~ 按假 HOME 展开。
        default = t / 'errands/scripts'
        default.mkdir(parents=True)
        shutil.copy(Path(env['AGENT_USE_HOME']) / 'scripts/errand_use.py', default)
        env['HOME'] = str(t)
        local = t / 'local.json'
        write_legacy_local(local, {'errand': {'home': '~/errands'}})
        env['AIW_CONFIG'] = str(local)
        for value in (None, '', '~/errands'):
            env.pop('AGENT_USE_ERRAND_HOME', None)
            if value is not None: env['AGENT_USE_ERRAND_HOME'] = value
            r = sh([str(REPO / 'bin/aiw'), 'errand', 'status', 'job'], env=env)
            check(r.returncode == 20, 'errand 配置路径不一致: ' + r.stderr)



def errand_nested_rejected():
    with tempfile.TemporaryDirectory() as td:
        env = errand_fixture(Path(td)); env['AIW_NESTED'] = '1'
        r = sh([str(REPO / 'bin/aiw'), 'errand', 'submit', '--allow-nested'], env=env)
        check(r.returncode == 64 and not r.stdout and '跑腿' in r.stderr, 'errand 嵌套未拒绝')



def errand_route_check_rejects():
    errand_route_test(False)



def errand_route_runtime_rejects():
    errand_route_test(True)



def errand_panel_rejects():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); env = errand_fixture(t)
        script = '''import sys
import panel as p
p.SEATS['seats']['cue'] = {'kind': 'agent_use'}
p.SEATS['panel_default'] = ['cue']
p.ask = lambda *args: (_ for _ in ()).throw(AssertionError('backend called'))
sys.argv = ['panel', '.', '10', 'text'] + sys.argv[1:]
sys.exit(p.main())
'''
        for names in ([], ['cue'], ['qoder', 'cue']):
            r = sh([sys.executable, '-c', script, *names], cwd=REPO, env=env)
            check(r.returncode == 64 and 'aiw errand' in r.stderr,
                  'errand panel 未拒绝: ' + r.stderr)
        check(not (t / 'state/panels').exists(), '拒绝前创建了 panel')



def errand_worker_rejects():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); env = errand_fixture(t)
        prompt = t / 'prompt'; prompt.write_text('test')
        for seat in ('cue', 'future-errand'):
            r = sh([str(REPO / 'bin/aiw'), seat, str(t), '10', str(prompt), '--ro'], env=env)
            check(r.returncode == 64 and 'aiw errand' in r.stderr, 'errand worker 未拒绝')
        script = '''import sys
import ai_worker as w
w.SEATS['seats']['cue'] = {'kind': 'agent_use'}
if sys.argv[1] == 'fallback': w.SEATS['fallback']['qoder'] = ['cue']
w.run_seat = lambda *args: (_ for _ in ()).throw(AssertionError('backend called'))
sys.argv = ['worker', 'qoder' if sys.argv[1] == 'fallback' else 'cue', *sys.argv[2:]]
sys.exit(w.main())
'''
        for mode in ('seat', 'fallback'):
            r = sh([sys.executable, '-c', script, mode, str(t), '10', str(prompt), '--ro'], cwd=REPO, env=env)
            check(r.returncode == 64 and 'aiw errand' in r.stderr, 'errand worker/顶替链未拒绝')
        check(not (t / '.provider').exists(), 'errand 写入文本座位产出')



def errand_freecheck_skips():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); env = errand_fixture(t)
        script = '''import freecheck as f
f.w.SEATS['seats'] = {'cue': {'free_guard': True}, 'qoder': {'free_guard': True}}
f.w.retry_notifications = lambda: None
f.w.disabled_seat = lambda seat: None
f.w.ledger = lambda row: None
calls = []
def run(seat, *args, **kwargs):
    calls.append(seat)
    return {'kind': 'ok', 'rc': 0, 'secs': 0}
f.w.run_seat = run
assert f.check() == 0
assert calls == ['qoder'], 'errand freecheck 未跳过'
'''
        r = sh([sys.executable, '-c', script], cwd=REPO, env=env)
        check(r.returncode == 0, 'errand freecheck 未跳过: ' + r.stderr)



def local_config_contract():
    import contextlib
    import hashlib
    import io
    from unittest.mock import patch
    import seats_config as c
    import doctor_config as d
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        default = Path.home() / '.config/aiw/seats.local.json'
        default.parent.mkdir(parents=True)
        default.write_text('{"breaker":{"quota_minutes":2}}')
        explicit = root / 'override.json'
        explicit.write_text('{"breaker":{"auth_minutes":3}}')
        with patch.dict(os.environ, AIW_CONFIG=str(explicit)):
            merged = c.load_seats()
            check(merged['breaker']['auth_minutes'] == 3 and merged['breaker']['quota_minutes'] != 2, '叠加了两份覆盖')
            check(c.merge({'a': {'x': 1, 'y': 2}, 'b': [1], 'c': 1},
                          {'a': {'x': None}, 'b': [2], 'c': 'v'}) ==
                  {'a': {'x': None, 'y': 2}, 'b': [2], 'c': 'v'}, '合并规则')
            before = c.effective_hash(merged)
            role_override = c.merge(merged, {'roles': {'codex': {'draft': ['claude']}}})
            check(role_override['roles']['codex']['draft'] == ['claude']
                  and role_override['roles']['claude'] == merged['roles']['claude'], '角色覆盖合并错误')
            check(c.effective_hash(role_override) != before, '角色未计入有效哈希')
            merged['notify'] = {'discord_channel_id': 'changed'}
            merged['seats']['codex']['login_hint'] = 'changed'
            check(before == c.effective_hash(merged), '说明/通知影响哈希')
            explicit.unlink()
            try:
                c.load_seats()
                check(False, '缺失文件未报错')
            except c.ConfigError as error:
                check(str(explicit) in str(error), str(error))
        with patch.dict(os.environ, AIW_CONFIG=''):
            check(c.local_config_path() == default, '默认路径')
        default.unlink()
        f = findings_fixture(root)
        with patch.dict(os.environ, AIW_CONFIG=str(explicit)):
            explicit.write_text('{}')
            before = f.common_fields(str(root))
            explicit.write_text('{"breaker":{"quota_minutes":123}}')
            after = f.common_fields(str(root))
            check(before['rules_hash'] == after['rules_hash'] and before['effective_hash'] != after['effective_hash'], '公开/有效哈希未分开')
            for invalid in ('{', '[]'):
                explicit.write_text(invalid)
                for script, args in [('ai_worker.py', []), ('panel.py', []), ('map.py', []), ('snapshot.py', []), ('findings.py', ['status']), ('route.py', ['--check'])]:
                    result = sh([sys.executable, str(REPO / script), *args])
                    check(result.returncode == 1 and str(explicit) in result.stderr and 'Traceback' not in result.stderr, repr(result))
                result = sh([sys.executable, str(REPO / 'route.py'), '--hook'], input='{"prompt":"hi"}')
                check(result.returncode == 0 and '警告' in result.stderr, repr(result))
                stream = io.StringIO()
                with contextlib.redirect_stderr(stream):
                    check(f.hint_text() is None, 'hint should skip')
                check('警告' in stream.getvalue(), 'hint 静默失败')
                stream = io.StringIO()
                with contextlib.redirect_stdout(stream):
                    check(d.local_config(REPO) == 1, 'doctor 解析失败未记失败')
                check('✗' in stream.getvalue(), stream.getvalue())
            repo = root / 'repo'; repo.mkdir()
            (repo / 'seats.json').write_text(json.dumps(c.load_public()))
            (repo / 'tracked.txt').write_text('unique-local-value')
            sh(['git', 'init', str(repo)])
            sh(['git', '-C', str(repo), 'add', 'tracked.txt', 'seats.json'])
            explicit.write_text(json.dumps({'notify': {'discord_channel_id': 'unique-local-value'},
                'unexpected': 1, 'seats': {'local-agent': {'kind': 'agent_use'}, 'local-cli': {'kind': 'cli'}},
                'fallback': {'ghost': ['absent']}, 'panel_default': ['absent'],
                'panel_free': ['absent'], 'self_seats': {'codex': ['absent']}, 'substitute_limit': {'absent': 1}}))
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                check(d.local_config(repo) >= 8, '缺少必填/悬空检查')
            output = stream.getvalue()
            for token in ('未知键路径：unexpected', '仅本地的座位', 'provider', 'argv_rw', '引用悬空', '列表整体替换', '公开版', 'notify.discord_channel_id 命中 tracked.txt'):
                check(token in output, output)
            check('unique-local-value' not in output and '未知键路径：seats.local-agent' not in output, output)
            check(output.count('⚠ 防泄漏：') == 1, output)
            check('未知键路径：fallback.ghost' in output, output)
            generic = {'seats': {'local-agent': {'kind': 'agent_use', 'provider': 'workbuddy',
                                                'model': 'generic-model', 'models': ['generic-model'],
                                                'free_guard': True, 'free_only': True}},
                       'fallback': {'local-agent': ['codex-sol']},
                       'substitute_limit': {'local-agent': 2},
                       'self_seats': {'local-agent': ['local-agent'], 'codex': ['codex']},
                       'map_default': 'local-agent', 'errand': {}}
            (repo / 'tracked.txt').write_text(json.dumps(generic))
            explicit.write_text(json.dumps(generic))
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                check(d.local_config(repo) == 0, '合法本地座位引用失败')
            check('未知键路径' not in stream.getvalue() and '防泄漏' not in stream.getvalue(), stream.getvalue())
            # Private leaves are checked even when overriding nonempty public values.
            private = {'notify': {'discord_channel_id': 'private-channel-value'},
                       'findings': {'hub': 'private-hub-value', 'owners': {'repo': 'private-owner-value'}},
                       'errand': {'home': 'private-errand-value'},
                       'seats': {'codex': {'login_hint': 'private-login-value',
                                           'disabled_by_user': 'private-disabled-value'}}}
            (repo / 'tracked.txt').write_text(json.dumps(private))
            public = c.load_public()
            public['notify']['discord_channel_id'] = 'public-channel-value'
            (repo / 'seats.json').write_text(json.dumps(public))
            explicit.write_text(json.dumps(private))
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                check(d.local_config(repo) == 0, '私人字段检查失败')
            output = stream.getvalue()
            check(output.count('⚠ 防泄漏：') == 6 and 'private-' not in output, output)
            result = sh(['git', '-C', str(repo), 'remote', 'add', 'origin',
                         'https://github.com/private-owner-value/x.git'])
            check(result.returncode == 0, result.stderr)
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                check(d.local_config(repo) == 0, '远端公开值检查失败')
            output = stream.getvalue()
            check('⚠ 防泄漏：findings.owners.repo' not in output, output)
            check('⚠ 防泄漏：notify.discord_channel_id 命中 tracked.txt' in output, output)
            check(output.count('⚠ 防泄漏：') == 5 and 'private-' not in output, output)
            for remote, exempt in (
                ('git@github.com:private-owner-value/x.git', True),
                ('github.com:private-owner-value/x.git', True),
                ('https://github.com/o/private-owner-value.git', True),
                ('https://github.com/private-owner-value-extra/x.git', False),
                ('https://private-owner-value@github.com/o/r.git', False),
                ('https://u:private-owner-value@github.com/o/r.git', False),
                ('https://private-owner-value/o/r.git', False),
                ('https://github.com/o/r.git?private-owner-value#private-owner-value', False),
                ('/private-owner-value/x.git', False),
                ('./private-owner-value/x.git', False),
                ('file:///private-owner-value/x.git', False),
            ):
                result = sh(['git', '-C', str(repo), 'remote', 'set-url', 'origin', remote])
                check(result.returncode == 0, result.stderr)
                stream = io.StringIO()
                with contextlib.redirect_stdout(stream):
                    check(d.local_config(repo) == 0, '远端路径段检查失败')
                output = stream.getvalue()
                check(('⚠ 防泄漏：findings.owners.repo' not in output) == exempt, output)
                check(output.count('⚠ 防泄漏：') == (5 if exempt else 6) and 'private-' not in output, output)
            private['findings']['owners']['repo'] = 'private-token-value'
            explicit.write_text(json.dumps(private))
            (repo / 'tracked.txt').write_text(json.dumps(private))
            for remote in ('https://private-token-value@github.com/o/r.git',
                           'https://u:private-token-value@github.com/o/r.git'):
                result = sh(['git', '-C', str(repo), 'remote', 'set-url', 'origin', remote])
                check(result.returncode == 0, result.stderr)
                stream = io.StringIO()
                with contextlib.redirect_stdout(stream):
                    check(d.local_config(repo) == 0, '远端凭据检查失败')
                output = stream.getvalue()
                check('⚠ 防泄漏：findings.owners.repo 命中 tracked.txt' in output, output)
                check(output.count('⚠ 防泄漏：') == 6 and 'private-' not in output, output)
            (repo / 'seats.json').write_text(json.dumps(c.merge(public, private)))
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                check(d.local_config(repo) == 0, '相同公开值检查失败')
            check('防泄漏' not in stream.getvalue(), stream.getvalue())
            explicit.write_text('{"seats":{"local-agent":{"kind":"agent_use","provider":"test"},"local-cli":{"kind":"cli","bin":"test","argv_rw":["{bin}"],"argv_ro":["{bin}"]}}}')
            link = root / 'link.json'; link.symlink_to(explicit)
            with patch.dict(os.environ, AIW_CONFIG=str(link)), contextlib.redirect_stdout(io.StringIO()) as stream:
                check(d.local_config(repo) == 0, '合法新座位失败')
                check(str(explicit.resolve()) in stream.getvalue(), '缺少软链目标')



def seat_execution_contract():
    from unittest.mock import patch
    import seats_config as c
    public = c.load_public()
    example = c.merge(public, c.read_config(REPO / 'seats.local.example.json'))
    check(c.validate(example) == [], '公开配置合并示例不合法')
    invalids = []
    for key in ('argv_rw', 'argv_ro'):
        invalids.append(({key: []}, f'seats.codex.{key}'))
        invalids.append(({key: ['']}, f'seats.codex.{key}[0]'))
        invalids.append(({key: ['{bin}', '']}, f'seats.codex.{key}[1]'))
        for template in ('{unknown}', '{model}', '{effort}', '{', '}', '{0}', '{}',
                         '{bin.x}', '{bin[0]}', '{bin!r}', '{bin:}', '{bin:>10}', '{bin:{prompt}}'):
            invalids.append(({key: [template]}, f'seats.codex.{key}[0]'))
    for key in ('ro_only', 'free_only', 'free_guard', 'require_no_tools', 'expects_credits'):
        for value in ('false', 0, 1, None, [], {}):
            invalids.append(({key: value}, f'seats.codex.{key}'))
    for value in (None, 1, False, [], {}):
        invalids.append(({'model': value}, 'seats.codex.model'))
    for value in (None, '', {}, [1], []):
        invalids.append(({'model_args': value}, 'seats.codex.model_args'))
    invalids.append(({'model_args': ['-m', '']}, 'seats.codex.model_args[1]'))
    for template in ('{unknown}', '{bin}', '{prompt}', '{workdir}', '{', '}', '{0}', '{}',
                     '{model.x}', '{model[0]}', '{model!r}', '{model:}', '{model:>10}', '{model:{effort}}'):
        invalids.append(({'model_args': [template]}, 'seats.codex.model_args[0]'))
    with tempfile.TemporaryDirectory() as td:
        local = Path(td) / 'local.json'
        for fields, expected in invalids:
            override = {'seats': {'codex': fields}}
            errors = c.validate(c.merge(public, override))
            check(any(expected + ' 必须是' in error for error in errors), repr((fields, errors)))
            local.write_text(json.dumps(override))
            with patch.dict(os.environ, AIW_CONFIG=str(local)):
                try:
                    c.load_seats()
                    check(False, '非法座位配置被接受: ' + repr(fields))
                except c.ConfigError as error:
                    check(all(e in str(error) for e in errors), str(error))
        # Remove rather than override the inherited public model_args.
        missing = c.merge(public, {})
        del missing['seats']['codex']['model_args']
        check(any('seats.codex.model_args 必须是' in e for e in c.validate(missing)),
              'cli model 缺 model_args 未拒绝')
        local.write_text(json.dumps(c.read_config(REPO / 'seats.local.example.json')))
        with patch.dict(os.environ, AIW_CONFIG=str(local)):
            check(c.load_seats() == example, '合法示例加载失败')
    valid = {'adapter': None, 'model': '', 'model_args': [], 'argv_rw': ['{bin}', '{prompt}', '{workdir}', '{{literal}}'],
             'argv_ro': ['{bin}'], 'ro_only': False, 'free_only': True, 'free_guard': False,
             'require_no_tools': True, 'expects_credits': False}
    check(c.validate(c.merge(public, {'seats': {'codex': valid,
          'local-agent': {'kind': 'agent_use', 'provider': 'test', 'model': 'test-model'}}})) == [],
          '合法 cli 或 agent_use model 无 model_args 被拒绝')


def config_structure_validation():
    import contextlib
    import io
    from unittest.mock import patch
    import seats_config as c
    import doctor_config as d
    public = c.load_public()
    check(c.validate(public) == [], '公开配置不合法')
    invalids = [({'seats': []}, 'seats'),
                ({'seats': {'claude': None}}, 'seats.claude'),
                ({'seats': {'claude': {'kind': []}}}, 'kind'),
                ({'seats': {'claude': {'bin': ' ', 'argv_rw': {}, 'argv_ro': None}}}, 'argv_ro'),
                ({'seats': {'local': {'kind': 'agent_use', 'provider': ''}}}, 'provider'),
                ({'fallback': []}, 'fallback'), ({'self_seats': []}, 'self_seats'),
                ({'substitute_limit': []}, 'substitute_limit'),
                ({'fallback': {'claude': [{}]}}, 'fallback.claude'),
                ({'self_seats': {'claude': [None]}}, 'self_seats.claude'),
                ({'panel_default': [{}]}, 'panel_default'), ({'panel_free': 'claude'}, 'panel_free'),
                ({'notify': []}, 'notify'), ({'findings': None}, 'findings'),
                ({'errand': False}, 'errand'), ({'map_default': []}, 'map_default'),
                ({'map_default': 'missing'}, '引用悬空'),
                ({'fallback': {'missing': ['missing']}, 'panel_default': ['missing'],
                  'panel_free': ['missing'], 'self_seats': {'claude': ['missing']},
                  'substitute_limit': {'missing': 1}}, '引用悬空')]
    for value in (0, -1, True, '2', 1.5, None):
        invalids.extend([({'max_concurrency': value}, 'max_concurrency'),
                         ({'seats': {'claude': {'max_concurrency': value}}}, 'max_concurrency')])
    with tempfile.TemporaryDirectory() as td:
        local = Path(td) / 'local.json'
        for override, expected in invalids:
            errors = c.validate(c.merge(public, override))
            check(errors and expected in ';'.join(errors), str(errors))
            local.write_text(json.dumps(override))
            with patch.dict(os.environ, AIW_CONFIG=str(local)):
                try:
                    c.load_seats()
                    check(False, '非法结构被接受')
                except c.ConfigError as error:
                    check(all(e in str(error) for e in errors), str(error))
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    check(d.local_config(REPO) == len(errors), 'doctor 与运行时校验不一致')
                check(all(e in output.getvalue() for e in errors), output.getvalue())
                for args, rc in ((['ai_worker.py'], 1), (['route.py', '--hook'], 0)):
                    result = sh([sys.executable, str(REPO / args[0]), *args[1:]], input='{}')
                    check(result.returncode == rc and expected in result.stderr and 'Traceback' not in result.stderr, repr(result))
        # A malformed public seat must not crash doctor's later unknown-key checks.
        (Path(td) / 'seats.json').write_text(json.dumps(c.merge(public, {'seats': {'claude': None}})))
        local.write_text('{"unexpected": 1}')
        with patch.dict(os.environ, AIW_CONFIG=str(local)), contextlib.redirect_stdout(io.StringIO()) as output:
            check(d.local_config(Path(td)) == 1, '公开座位未校验')
        check('未知键路径：unexpected' in output.getvalue(), output.getvalue())



def local_config_empty_defaults():
    from unittest.mock import patch
    import ai_worker as w
    import findings as f
    import seats_config as c
    with patch.dict(os.environ, AIW_CONFIG=''):
        check(c.local_config_path() is None, '未隔离 HOME')
        public = c.load_public()
        with patch.object(w, 'SEATS', public), patch.object(w, 'send_discord', side_effect=AssertionError('通知')), patch.object(w.subprocess, 'Popen', side_effect=AssertionError('同步')):
            w.retry_notifications()
        with tempfile.TemporaryDirectory() as td:
            prompt = Path(td) / 'prompt'; prompt.write_text('hello')
            result = sh([sys.executable, str(REPO / 'ai_worker.py'), 'codex', td, '10', str(prompt), '--ro', '--no-fallback'])
            check(result.returncode == 0 and '[notify]' not in result.stderr, repr(result))



def public_config_contract():
    from unittest.mock import patch
    import contextlib
    import io
    import seats_config as c
    import doctor_config as d
    import ai_worker as w
    import findings as f
    import freecheck
    public = c.load_public()
    check(sorted(public['seats']) == ['claude', 'claude-write', 'codex', 'codex-sol'], str(public['seats']))
    check(public['panel_free'] == [] and public['map_default'] == 'draft', '公开默认错误')
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        env = dict(os.environ, AIW_CONFIG='', AIW_ROUTER_RULES='', AGENT_USE_ERRAND_HOME='', HOME=str(t))
        r = sh([sys.executable, str(REPO / 'route.py'), '--check'], cwd=t, env=env)
        check(r.returncode == 0, r.stderr)
        for main, expected in (('', ['codex-sol', 'claude']), ('claude', ['codex-sol'])):
            state = t / ('panel-' + main)
            r = sh([str(REPO / 'bin/aiw'), 'panel', str(t), '10', 'test'],
                   env=dict(env, AIW_MAIN=main, AIW_STATE=str(state)))
            if len(expected) < 2:
                check(r.returncode == 1 and '会诊需要至少两家可用座位' in r.stderr, repr(r))
                check(not (state / 'panels').exists(), '单家仍派单')
                continue
            summary = json.loads(next((state / 'panels').glob('*/summary.json')).read_text())
            check([row['seat'] for row in summary['seats']] == expected, str(summary))
            sufficient = len(expected) >= 2
            check(r.returncode == (0 if sufficient else 1), repr(r))
            check(summary['required'] == 2 and summary['sufficient'] is sufficient, str(summary))
            check(('可用座位只有 1 家' in r.stderr) == (len(expected) == 1), r.stderr)
        local = t / 'seats.json'
        local.write_text(json.dumps({'seats': {'codex': {'disabled_by_user': 'test', 'ro_only': True}},
                                     'errand': {'home': '~/errands'}, 'panel_free': ['codex-sol']}))
        with patch.dict(os.environ, dict(env, AIW_CONFIG=str(local))), contextlib.redirect_stdout(io.StringIO()) as out:
            check(d.local_config(REPO) == 0, '合法可选字段失败')
        check('未知键路径' not in out.getvalue() and '列表整体替换' not in out.getvalue(), out.getvalue())
        local.write_text(json.dumps({'fallback': {'codex': ['missing']}}))
        r = sh([str(REPO / 'bin/aiw'), 'doctor'], env=dict(env, AIW_CONFIG=str(local)))
        check(r.returncode == 1 and '引用悬空' in r.stdout and 'codex-cli fake' in r.stdout and '配置检查' in r.stdout, repr(r))
        for invalid in ('{', '{"seats":{"broken":{"kind":"agent_use"}}}'):
            local.write_text(invalid)
            r = sh([str(REPO / 'bin/aiw'), 'doctor'], env=dict(env, AIW_CONFIG=str(local)))
            check(r.returncode == 1 and 'codex-cli fake' in r.stdout and '配置检查' in r.stdout, repr(r))
        with patch.object(w, 'SEATS', public), patch.object(w, 'agent_use_home', side_effect=AssertionError('不应读适配器')):
            check('跳过' in freecheck.zen_check(), '无对应座位未跳过')



def local_router_contract():
    from unittest.mock import patch
    import route as routing
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); home = t / 'home'; home.mkdir()
        local = home / '.config/aiw/router-rules.local.json'; local.parent.mkdir(parents=True)
        project = t / 'project'; (project / '.git').mkdir(parents=True)
        rule = {'id': 'draft', 'seat': 'local-text', 'patterns': ['起草'], 'instruction': 'aiw local-text test'}
        local.write_text(json.dumps({'rules': [rule], 'disable': ['logs'], 'escape': ['本机跳过']}))
        seats = t / 'seats.json'; seats.write_text(json.dumps({'seats': {'local-text': {'kind': 'agent_use', 'provider': 'test'}}}))
        env = dict(os.environ, HOME=str(home), AIW_CONFIG=str(seats), AIW_ROUTER_RULES='')
        def run(*args, **kw):
            return sh([sys.executable, str(REPO / 'route.py'), *args], cwd=project, env=env, **kw)
        check(run('--check').returncode == 0, '本地座位校验失败')
        result = json.loads(run('起草').stdout)
        check(result['source'] == 'local' and result['seat'] == 'local-text', str(result))
        check(json.loads(run('查日志').stdout)['rule'] is None, 'disable 未生效')
        # Same id replaces all original patterns, not merely prepends another match.
        check(json.loads(run('写个 commit message').stdout)['rule'] is None, '同 id 未替换')
        # Local replacements retain global priority; only new IDs go first.
        rule['patterns'].append('初稿')
        local.write_text(json.dumps({'rules': [rule]}))
        with patch.dict(os.environ, env):
            _, rules, _ = routing.load_rules(project)
            global_ids = [r['id'] for r in routing.read_rules(REPO / 'router-rules.json')[1]]
            check([r['id'] for r in rules] == global_ids, '本机覆盖改变全局顺序')
            check(next(r for r in rules if r['id'] == 'draft')['source'] == 'local', '覆盖来源错误')
            check(routing.route('写一个方案初稿', project)['rule'] == 'plan', '本机 draft 抢占 plan')
            fresh = dict(rule, id='local-first')
            local.write_text(json.dumps({'rules': [rule, fresh], 'disable': ['logs']}))
            _, rules, _ = routing.load_rules(project)
            check([r['id'] for r in rules] == ['local-first'] + [i for i in global_ids if i != 'logs'], '新 id 未前置或 disable 失效')
        local.write_text(json.dumps({'rules': [rule]}))
        (project / '.claude').mkdir()
        (project / '.claude/router-rules.json').write_text(json.dumps({'rules': [dict(rule, seat='claude', instruction='aiw claude test')]}))
        check(json.loads(run('起草').stdout)['source'] == 'project', '项目优先级错误')
        check(json.loads(run('写一个方案初稿').stdout)['rule'] == 'draft', '项目同 id 前置语义改变')
        for value in ('{', '{"disable":42}', '{"rules":[{"id":"bad"}]}'):
            local.write_text(value)
            check(run('--check').returncode == 1, '损坏本机规则未拒绝')
            r = run('--hook', input=json.dumps({'prompt': '起草', 'cwd': str(project)}))
            check(r.returncode == 0 and '忽略损坏' in r.stderr, repr(r))
        local.unlink(); local.symlink_to(home / 'missing')
        check(run('--check').returncode == 1, '断链未拒绝')
        env['AIW_ROUTER_RULES'] = str(home / 'explicit-missing')
        check(run('--check').returncode == 1, '显式缺失未拒绝')
        override = home / 'explicit'; override.write_text('{}'); env['AIW_ROUTER_RULES'] = str(override)
        check(run('--check').returncode == 0, '环境变量未覆盖默认断链')



def configurable_map_errand():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); local = t / 'local.json'; local.write_text('{}')
        env = dict(os.environ, AIW_CONFIG=str(local), AGENT_USE_ERRAND_HOME='', AIW_STATE=str(t / 'state'))
        prompt = t / 'prompt'; prompt.write_text('test')
        attachment = t / 'data.txt'; attachment.write_text('data')
        args = [str(REPO / 'bin/aiw'), 'map', str(t), '10', str(prompt), 'data.txt']
        r = sh(args, env=env)
        check(r.returncode == 0, repr(r))
        summaries = list((t / 'state/maps').glob('*/summary.json'))
        check(json.loads(summaries[-1].read_text())['seat'] == 'codex-sol', '未读取公开 map_default')
        local.write_text(json.dumps({'map_default': 'claude', 'seats': {'claude': {'max_concurrency': 2}}}))
        r = sh(args + ['--jobs', '5'], env=env)
        summary = json.loads(sorted((t / 'state/maps').glob('*/summary.json'))[-1].read_text())
        check(r.returncode == 0 and summary['seat'] == 'claude' and summary['jobs'] == 2 and 'max_concurrency' in r.stderr, repr(r))
        for value in (0, -1, True, '2'):
            local.write_text(json.dumps({'seats': {'codex-sol': {'max_concurrency': value}}}))
            result = sh(args, env=env)
            check(result.returncode == 1 and 'max_concurrency' in result.stderr, f'错误并发未拒绝: {value}: {result}')
        local.write_text('{}')
        r = sh([str(REPO / 'bin/aiw'), 'errand', 'status'], env=env)
        check(r.returncode == 1 and '未配置跑腿' in r.stderr, repr(r))
        probe = [sys.executable, '-c', 'import ai_worker as w; print(sorted(w.errand_names()))']
        check(sh(probe, cwd=REPO, env=env).stdout.strip() == '[]', '未配置供应商非空')
        home = t / 'errands'; (home / 'scripts').mkdir(parents=True); (home / 'config').mkdir()
        (home / 'scripts/errand_use.py').write_text('print("configured")')
        registry = home / 'config/errands.json'
        registry.write_text(json.dumps({'version': 1, 'providers': {'runner': {'argv': ['test']}}}))
        for via in ('env', 'config'):
            local.write_text(json.dumps({'errand': {'home': str(home)}}) if via == 'config' else '{}')
            env['AGENT_USE_ERRAND_HOME'] = str(home) if via == 'env' else ''
            check(sh(probe, cwd=REPO, env=env).stdout.strip() == "['runner']", via)
            r = sh([str(REPO / 'bin/aiw'), 'errand', 'status'], env=env)
            check(r.returncode == 0 and r.stdout.strip() == 'configured', repr(r))
        env['AGENT_USE_ERRAND_HOME'] = str(t / 'missing')
        check(sh(probe, cwd=REPO, env=env).stdout.strip() == '[]', '环境变量优先级错误')
        check(sh([str(REPO / 'bin/aiw'), 'errand', 'status'], env=env).returncode == 1, '缺失入口未拒绝')



def contract_guards():
    import contextlib
    import io
    from unittest.mock import patch
    import ai_worker as w
    import findings as f
    import seats_config as sc
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        wd = t / 'work'; wd.mkdir()
        prompt = t / 'prompt'; prompt.write_text('Reply OK')
        au = fake_agent_use(t)
        script = au / 'scripts/run_agent.py'
        local = t / 'local.json'
        write_legacy_local(local, {'findings': {'collect': True}})
        env = dict(os.environ, AIW_CONFIG=str(local), AGENT_USE_HOME=str(au))
        for index, version in enumerate((2, None, True, '1', 1)):
            source = FAKE_AGENT_USE.replace('"protocol_version": 1,',
                '' if version is None else f'"protocol_version": {version!r},')
            # Only primary emits incompatible results; backup retains version 1.
            source = source.replace('"protocol_version": '+repr(version)+',',
                                    f'"protocol_version": ({version!r} if prov == "doubao" else 1),') if version is not None else source.replace('st = {', 'st = {**({} if prov == "doubao" else {"protocol_version": 1}), ')
            script.write_text(source)
            state = t / f'state-{index}'
            r = sh([sys.executable, str(REPO / 'ai_worker.py'), 'doubao', str(wd), '10', str(prompt), '--ro'],
                   env=dict(env, AIW_STATE=str(state)))
            rows = [json.loads(line) for line in (state / 'seat-ledger.jsonl').read_text().splitlines()]
            valid = type(version) is int and version == 1
            check(r.returncode == 0 and rows[0]['kind'] == ('ok' if valid else 'incompatible'), r.stderr)
            if not valid:
                br = json.loads((state / 'breaker.json').read_text())
                check(br['doubao']['reason'] == 'not_installed', str(br))
                check(rows[-1]['actual'] == 'workbuddy' and rows[-1]['kind'] == 'ok', str(rows))
        script.write_text(FAKE_AGENT_USE)
        cfg = {'kind': 'agent_use', 'provider': 'generic', 'expects_credits': True}
        for reason, rc, credits, expects, collect, warning in (
                ('new_reason', 1, 0, False, True, True),
                ('provider_failed', 1, 0, True, True, False),
                ('new_reason', 124, 0, True, True, False),
                (None, 0, None, True, True, True),
                (None, 0, '0', True, True, True),
                (None, 0, True, True, True, True),
                (None, 0, 0, True, True, False),
                (None, 0, None, False, True, False),
                (None, 0, None, None, True, False),
                (None, 0, None, True, False, True)):
            case_cfg = dict(cfg)
            if expects is None:
                case_cfg.pop('expects_credits')
            else:
                case_cfg['expects_credits'] = expects
            state = t / f'observations-{len(list(t.glob("observations-*")))}'; state.mkdir()
            def run(argv, *args, **kwargs):
                out = Path(argv[4]); out.mkdir()
                att = {'reason': reason}
                if credits is not None:
                    att['credits'] = credits
                status = {'protocol_version': 1, 'attempts': [att],
                          'state': 'failed' if reason else 'staged_for_review', 'accepted': False}
                (out / 'status.json').write_text(json.dumps(status))
                (out / 'response.txt').write_text('answer')
                return subprocess.CompletedProcess(argv, rc, '', '')
            settings = sc.load_seats(REPO)
            settings['findings'] = {'collect': collect}
            capture = io.StringIO()
            with patch.object(w, 'SEATS', {'seats': {'generic': case_cfg}}), patch.object(w, 'STATE', state), patch.object(f, 'STATE', state), patch.object(f, '_settings', return_value=settings), patch.object(w, 'agent_use_home', return_value=au), patch.object(w, 'run_group', side_effect=run), contextlib.redirect_stderr(capture):
                result = w.run_agent_use('generic', 'prompt', str(wd), 10)
            check(bool(capture.getvalue()) == warning, capture.getvalue())
            records = state / 'findings/records.jsonl'
            check(records.exists() == (warning and collect), str(result))
            if records.exists():
                rows = [json.loads(line) for line in records.read_text().splitlines()]
                check(len(rows) == 1 and rows[0]['source'] == 'worker' and rows[0]['type'] == 'observation', str(rows))
            check(result['kind'] == ('ok' if reason is None else 'timeout' if rc == 124 else 'transient' if reason == 'provider_failed' else 'other'), str(result))
        with patch.object(f, 'config', return_value={'collect': True}), patch.object(f, 'common_fields', side_effect=OSError('write failed')), contextlib.redirect_stderr(io.StringIO()) as capture:
            w.contract_observation(str(wd), 'generic', 'notice', 'expected')
        check(capture.getvalue() == 'notice\n', capture.getvalue())
        with patch.object(f, 'config', return_value={'collect': True, 'field_max': 300}), patch.object(f, 'common_fields', return_value={}), patch.object(f, 'record', side_effect=OSError('write failed')), contextlib.redirect_stderr(io.StringIO()) as capture:
            w.contract_observation(str(wd), 'generic', 'notice', 'expected')
        check(capture.getvalue() == 'notice\n', capture.getvalue())
        settings = sc.load_seats(REPO)
        settings['seats']['generic'] = dict(cfg, expects_credits='true')
        check(any('expects_credits' in e for e in sc.validate(settings)), 'boolean validation missing')



def agent_use_versions():
    import hashlib
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        cmd, env, _ = worker_call(t)
        scheduler = {'z': 'revision-z', 'a': 'revision-a'}
        adapter = {'engine': 'revision-engine'}
        expected = hashlib.sha256(json.dumps({'scheduler': scheduler, 'adapter': adapter},
            sort_keys=True, separators=(',', ':')).encode()).hexdigest()[:12]
        for fields, digest in (
            ({'scheduler_versions': scheduler, 'attempts': [
                {'adapter_versions': {'ignored': 'old'}}, {'adapter_versions': adapter, 'credits': 0}]}, expected),
            ({'attempts': [{'credits': 0, 'adapter_versions': adapter}],
              'scheduler_versions': dict(reversed(list(scheduler.items())))}, expected),
            ({'scheduler_versions': {}}, hashlib.sha256(b'{"adapter":{},"scheduler":{}}').hexdigest()[:12]),
            ({}, None),
        ):
            r = sh(cmd, env=dict(env, FAKE_STATUS_FIELDS=json.dumps(fields)))
            check(r.returncode == 0, r.stdout + r.stderr)
            row = json.loads((Path(env['AIW_STATE']) / 'seat-ledger.jsonl').read_text().splitlines()[-1])
            check(row.get('agent_use_versions') == digest, str(row))
            check(('agent_use_versions' in row) == (digest is not None), str(row))
            check('scheduler_versions' not in row and 'adapter_versions' not in row, str(row))



def malformed_contract_metadata():
    from unittest.mock import patch
    import ai_worker as w
    import doctor_config as d
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        cmd, env, _ = worker_call(t)
        cmd[2] = 'doubao'
        env['AGENT_USE_HOME'] = str(fake_agent_use(t))
        script = Path(env['AGENT_USE_HOME']) / 'scripts/run_agent.py'
        for index, attempts in enumerate(([None], ['bad'], [42], None, 'bad', {}, [])):
            for version in (1, 2):
                fields = {'protocol_version': version, 'scheduler_versions': {}, 'attempts': attempts}
                script.write_text(FAKE_AGENT_USE.replace(
                    'st.update(json.loads(os.environ.get("FAKE_STATUS_FIELDS", "{}")))',
                    f'st.update({fields!r}) if prov == "doubao" else None'))
                state = t / f'state-malformed-{index}-{version}'
                r = sh(cmd, env=dict(env, AIW_STATE=str(state)))
                rows = [json.loads(line) for line in (state / 'seat-ledger.jsonl').read_text().splitlines()]
                check(r.returncode == 0, r.stdout + r.stderr)
                check(rows[0]['kind'] == ('ok' if version == 1 else 'incompatible'), str(rows))
                check(rows[-1]['actual'] == ('doubao' if version == 1 else 'workbuddy')
                      and rows[-1]['kind'] == 'ok', str(rows))
        # Fingerprinting is advisory, including unexpected hashing failures.
        out = t / 'result'; out.mkdir()
        (out / 'response.txt').write_text('OK')
        for version in (1, 2):
            (out / 'status.json').write_text(json.dumps({
                'protocol_version': version, 'scheduler_versions': {}, 'attempts': [None],
                'state': 'staged_for_review', 'accepted': False}))
            with patch.object(w, 'run_group', return_value=subprocess.CompletedProcess([], 0, '', '')), \
                 patch.object(w.hashlib, 'sha256', side_effect=RuntimeError('fingerprint failed')):
                result = w.run_agent_use_files({'provider': 'generic'}, script, t / 'task', out, None, str(t), 10)
            check(result['kind'] == ('ok' if version == 1 else 'incompatible'), str(result))
            check('agent_use_versions' not in result, str(result))
        for malformed in (None, 'bad', 42, []):
            for seats in (malformed, {'seats': malformed}):
                with patch.object(d, 'warn') as warn:
                    d.registry_check(seats, lambda: t / 'missing')
                check(not warn.called, str(seats))
            seats = {'seats': {'broken': malformed,
                               'valid': {'kind': 'agent_use', 'provider': 'missing'}}}
            registry = t / 'registry.json'; registry.write_text('{"providers":{}}')
            with patch.object(d, 'warn') as warn:
                d.registry_check(seats, lambda: registry)
            check(warn.call_count == 1 and 'valid' in warn.call_args.args[0], str(warn.call_args_list))



def tool_templates_expand_and_reject_unknown():
    import seats_config as c
    from unittest.mock import patch
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        public = c.load_public()
        (root / 'seats.json').write_text(json.dumps(public))
        (root / 'tools.json').write_text(json.dumps({'version': 1, 'tools': {'coding': public['seats']['codex']}}))
        local = root / 'local.json'
        local.write_text(json.dumps({'seats': {'generated': {'template': 'coding', 'model': 'example-model', 'force_mode': 'ro'}}}))
        with patch.dict(os.environ, {'AIW_CONFIG': str(local)}):
            row = c.load_seats(root)['seats']['generated']
            import doctor_config
            check(doctor_config.local_config(root) == 0, 'doctor rejected template')
            check(row['adapter'] == 'codex' and row['model'] == 'example-model' and row['force_mode'] == 'ro', str(row))
            local.write_text(json.dumps({'seats': {'generated': {'template': 'missing'}}}))
            try:
                c.load_seats(root)
                check(False, 'unknown template accepted')
            except c.ConfigError:
                pass


def generated_runtime_is_complete_and_integrity_checked():
    import hashlib
    import seats_config as c
    public = c.load_public()
    data = {'capabilities': ['agent-runtime-v1'], 'seats': {'example': public['seats']['codex']}, 'fallback': {'example': []}}
    data['seats']['example']['label'] = '示例'
    digest = hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    local = {**data, 'version': digest}
    merged = c.combine(public, local)
    check(set(merged['seats']) == {'example'}, 'public seats leaked into runtime')
    check(merged == local, 'runtime changed during combine')
    local['seats']['example']['model'] = 'changed'
    try:
        c.combine(public, local)
        check(False, 'damaged runtime accepted')
    except c.ConfigError:
        pass


CASES = {case.__name__: case for case in (
    errand_registry_corrupt_rejected,
    errand_dispatch,
    errand_nested_rejected,
    errand_route_check_rejects,
    errand_route_runtime_rejects,
    errand_panel_rejects,
    errand_worker_rejects,
    errand_freecheck_skips,
    tool_templates_expand_and_reject_unknown,
    generated_runtime_is_complete_and_integrity_checked,
    local_config_contract,
    seat_execution_contract,
    config_structure_validation,
    local_config_empty_defaults,
    public_config_contract,
    local_router_contract,
    configurable_map_errand,
    contract_guards,
    agent_use_versions,
    malformed_contract_metadata,
)}

def shipped_tool_templates_validate_readonly_recipes():
    import seats_config as c
    import ai_worker as w
    from unittest.mock import patch
    public = c.load_public()
    rows = {name: {'template': name, **({'provider': 'example'} if name == 'agent-use' else {})}
            for name in ('codex', 'claude-code', 'agent-use')}
    expanded = c.expand_tools(c.combine(public, {'seats': rows}), REPO)
    check(not c.validate(expanded), c.validate(expanded))
    with patch.dict(w.SEATS['seats'], expanded['seats']):
        for name in ('codex', 'claude-code'):
            row = expanded['seats'][name]
            argv = [a.format(bin=row['bin'], prompt='example', workdir='/example') for a in row['argv_ro']]
            check(w.readonly_argv_error(name, argv, 'example') is None, name)

CASES['shipped_tool_templates_validate_readonly_recipes'] = shipped_tool_templates_validate_readonly_recipes


def capability_query_isolated_and_argument_free():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        (t / 'json.py').write_text("raise RuntimeError('cwd import must not execute')")
        env = dict(os.environ, PYTHONPATH=str(t), AIW_CONFIG=str(t / 'missing.json'))
        result = subprocess.run([str(REPO / 'bin/aiw'), 'capabilities'], cwd=t, env=env, capture_output=True, text=True)
        check(result.returncode == 0, result)
        check(set(['tool-templates-v1', 'agent-runtime-v1']).issubset(json.loads(result.stdout)['capabilities']), result)
        bad = subprocess.run([str(REPO / 'bin/aiw'), 'capabilities', 'codex'], cwd=t, env=env, capture_output=True, text=True)
        check(bad.returncode == 64 and not bad.stdout, bad)

CASES['capability_query_isolated_and_argument_free'] = capability_query_isolated_and_argument_free
