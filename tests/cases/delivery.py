"""临时目录、能力声明和安全交付的离线验收。"""
import hashlib
import tomllib
from support import Path, REPO, check, git_template, json, os, sh, sys, tempfile


def scratch_readonly(argv):
    configs = '\n'.join(argv[i + 1] for i, arg in enumerate(argv[:-1]) if arg == '-c')
    policy = tomllib.loads(configs)['sandbox_workspace_write']
    return (policy['writable_roots'] == []
            and argv[argv.index('--sandbox') + 1] == 'read-only'
            and not policy['network_access']
            and policy['exclude_slash_tmp'] and policy['exclude_tmpdir_env_var'])


def scratch_delivery():
    with tempfile.TemporaryDirectory(dir=Path.home()) as td:
        root = Path(td).resolve()
        work = root / 'repo'
        git_template(work)
        prompt = root / 'prompt.txt'
        prompt.write_text('生成成果。')
        backend = root / 'fake-codex'
        backend.write_text('''#!/usr/bin/env python3
import json, os, pathlib, sys
s = pathlib.Path(os.environ['AIW_SCRATCH'])
assert str(s) == os.environ['TMPDIR'] and s.is_dir()
(s / 'deliver/sub').mkdir(parents=True)
(s / 'deliver/sub/report.txt').write_bytes(b'result\\x00content')
(s / 'deliver/summary.txt').write_bytes(b'summary')
(s.parent / 'observed.json').write_text(json.dumps(dict(argv=sys.argv[1:], cwd=os.getcwd())))
print('结果：成功')
''')
        backend.chmod(0o755)
        target = root / 'destination'
        for index, flags in enumerate(((), ('--no-net',))):
            state = root / f'state-{index}'
            env = dict(os.environ, AIW_STATE=str(state), CODEX_BIN=str(backend))
            cmd = ['bash', str(REPO / 'bin/aiw'), 'codex', str(work), '10', str(prompt),
                   '--no-fallback', '--no-findings', '--deliver', str(target), *flags]
            r = sh(cmd, env=env)
            check(r.returncode == 0, r.stderr)
            row = json.loads((state / 'seat-ledger.jsonl').read_text().splitlines()[-1])
            run = Path(row['run_dir'])
            scratch = run.resolve() / 'scratch'
            observed = json.loads((scratch.parent / 'observed.json').read_text())
            argv = observed['argv']
            configs = '\n'.join(argv[i + 1] for i, arg in enumerate(argv[:-1]) if arg == '-c')
            config = tomllib.loads(configs)
            policy = config['sandbox_workspace_write']
            check(policy['writable_roots'] == [str(scratch), str(state / 'cache/go-build')], configs)
            readonly = '--ro' in flags or '--text' in flags
            check(policy['network_access'] == (not readonly and '--no-net' not in flags), configs)
            check('exclude_slash_tmp' not in policy and 'exclude_tmpdir_env_var' not in policy, configs)
            check(argv[argv.index('--cd') + 1] == str(scratch if readonly else work), str(argv))
            check(observed['cwd'] == str(scratch if readonly else work), str(observed))
            check(config['shell_environment_policy']['set']['TMPDIR'] == str(scratch), configs)
            expected = hashlib.sha256(b'result\x00content').hexdigest()
            check(hashlib.sha256((target / 'sub/report.txt').read_bytes()).hexdigest() == expected, '复制内容不同')
            result = json.loads((run / 'result.json').read_text())
            check(row['capabilities'] == result['capabilities'] == dict(
                scratch_writable=True, network=policy['network_access'], deliver=True), str(result))
            check(result['delivery']['delivered'] == [
                {'path': 'sub/report.txt', 'sha256': expected},
                {'path': 'summary.txt', 'sha256': hashlib.sha256(b'summary').hexdigest()}], str(result))
            check((target / 'summary.txt').read_bytes() == b'summary', '第二个文件未交付')
            check([p.name for p in scratch.iterdir()] == ['deliver'], '临时内容未清理或交付源丢失')
            check(json.loads((run / 'changes.json').read_text())['delivery'] == result['delivery'], '缺交付 changes')
            check('capabilities:' in r.stderr and 'delivery:' in r.stderr, r.stderr)
            check('[aiw delivery]' in (run / 'output.md').read_text(), '缺交付输出')
            check('$AIW_SCRATCH/deliver/' in (run / 'task.txt').read_text(), '缺交付约定')
        (target / 'sub/report.txt').write_text('existing')
        r = sh(cmd, env=env)
        check(r.returncode == 1 and '内容不同' in r.stderr, r.stderr)
        check((target / 'sub/report.txt').read_text() == 'existing', '覆盖了冲突目标')
        row = json.loads((state / 'seat-ledger.jsonl').read_text().splitlines()[-1])
        check(row['delivery']['error'] and not row['delivery']['delivered'], str(row))
        for path in (Path.home() / '.ssh/out', Path.home() / '.pal-peer/out', Path('/tmp/out'), Path('relative')):
            r = sh(cmd[:cmd.index('--deliver')] + ['--deliver', str(path)], env=env)
            check(r.returncode == 64, repr(r))


def delivery_boundaries():
    from delivery import deliver_files, validate_target, PRIVATE
    with tempfile.TemporaryDirectory(dir=Path.home()) as td:
        root = Path(td)
        scratch = root / 'scratch'
        source = scratch / 'deliver'
        source.mkdir(parents=True)
        dest = root / 'dest'
        for private in PRIVATE:
            for spelling in (private, private.upper()):
                try:
                    validate_target(str(Path.home() / spelling / 'out'))
                except ValueError:
                    pass
                else:
                    raise AssertionError(f'未拒绝私密目录 {spelling}')
        alias = root / 'alias'
        alias.symlink_to(Path.home() / '.ssh')
        check(deliver_files(scratch, str(alias))['error'], '目标软链未拒绝')
        (source / 'link').symlink_to(root / 'missing')
        check(deliver_files(scratch, str(dest))['error'], '源软链未拒绝')
        (source / 'link').unlink()
        payload = root / 'payload'
        payload.write_text('private')
        os.link(payload, source / 'hardlink')
        check(deliver_files(scratch, str(dest))['error'], '源硬链未拒绝')
        (source / 'hardlink').unlink()
        os.mkfifo(source / 'fifo')
        check(deliver_files(scratch, str(dest))['error'], 'FIFO 未拒绝')
        (source / 'fifo').unlink()
        (source / 'a').write_text('new')
        (source / 'z').write_text('different')
        dest.mkdir(exist_ok=True)
        (dest / 'z').write_text('old')
        result = deliver_files(scratch, str(dest))
        check(result['error'] and not (dest / 'a').exists(), '预检冲突前已写入')
        (dest / 'z').unlink()
        (dest / 'z').symlink_to(payload)
        check(deliver_files(scratch, str(dest))['error'] and payload.read_text() == 'private', '目标软链未拒绝')
        (source / 'a').unlink()
        (source / 'z').unlink()
        (source / '.pal-peer').mkdir()
        (source / '.pal-peer/out').write_text('forbidden')
        check(deliver_files(scratch, str(Path.home()))['error'], 'home 目标的私密子路径未拒绝')
        check(not (Path.home() / '.pal-peer/out').exists(), '写入私密子路径')


def scratch_policy_override():
    import ai_worker as worker
    from unittest.mock import patch
    with tempfile.TemporaryDirectory() as td:
        scratch = Path(td).resolve() / 'scratch'
        with patch.object(worker.shutil, 'which', return_value='/fake/codex'):
            argv = worker.build_argv('codex', 'TASK', td, False)
        argv[2:2] = ['-c', 'sandbox_workspace_write = {network_access=true, writable_roots=["/tmp"]}',
                     '-c', 'sandbox_workspace_write.network_access = true']
        argv = worker.scratch_argv('codex', argv, scratch, td, False, False, 'TASK')
        configs = '\n'.join(argv[i + 1] for i, arg in enumerate(argv[:-1]) if arg == '-c')
        policy = tomllib.loads(configs)['sandbox_workspace_write']
        check(policy['writable_roots'] == [str(scratch), str(worker.STATE.resolve() / 'cache/go-build')] and policy['network_access'] is False, policy)


def scratch_cache_environment():
    import ai_worker as worker
    from unittest.mock import patch
    caches = ('GOCACHE', 'npm_config_cache', 'XDG_CACHE_HOME', 'PIP_CACHE_DIR')
    with tempfile.TemporaryDirectory() as td:
        root = Path(td).resolve()
        prompt = root / 'prompt'; prompt.write_text('检查缓存和编译名额')
        backend = root / 'fake-cli'
        backend.write_text('''#!/usr/bin/env python3
import json, os, pathlib, subprocess, sys
s = pathlib.Path(os.environ['AIW_SCRATCH'])
cache_write = os.environ['EXPECT_CACHE'] == '1'
keys = ('GOCACHE', 'npm_config_cache', 'XDG_CACHE_HOME', 'PIP_CACHE_DIR')
if cache_write:
    for key in keys:
        p = pathlib.Path(os.environ[key])
        assert p.is_dir(), (key, p)
        (p / 'probe').write_text(key)
else:
    assert all(key not in os.environ for key in keys), dict(os.environ)
(pathlib.Path(os.environ['GOTMPDIR']) / 'compile.tmp').write_text('temporary')
(s / 'temporary').write_text('temporary')
r = subprocess.run(['go', 'test', './...'], capture_output=True, text=True)
assert r.returncode == 0, r.stderr
keys += ('AIW_SCRATCH', 'TMPDIR', 'GOTMPDIR', 'GOMODCACHE')
(s.parent / 'observed.json').write_text(json.dumps(dict(env={key: os.environ.get(key) for key in keys}, argv=sys.argv[1:], go=r.stdout)))
print('结果：成功')
''')
        backend.chmod(0o755)
        fakebin = root / 'bin'; fakebin.mkdir()
        go = fakebin / 'go'
        go.write_text('#!/bin/sh\nprintf "%s\\n" "held=$AIW_SLOT_HELD $*"\n')
        go.chmod(0o755)
        env = {key: value for key, value in os.environ.items() if key not in caches}
        env.update(GOMODCACHE='/shared/modules', AIW_BUILD_JOBS='2',
                   CODEX_BIN=str(backend), CLAUDE_BIN=str(backend),
                   PATH=f"{REPO / 'shims'}:{fakebin}:{os.environ['PATH']}")
        with patch.dict(os.environ, dict(env, **{key: '/old-cache' for key in caches}), clear=True):
            check(all(worker.child_env(root)[key] == '/old-cache' for key in caches),
                  '其它适配器的已有缓存设置被覆盖')
        for seat, flags in (('codex', ()), ('claude-write', ()), ('codex', ('--ro',))):
            state = root / (seat + ('-ro' if flags else ''))
            cache_write = seat == 'codex' and not flags
            # 连续两单必须复用同一共享缓存，而非清掉上一单的缓存。
            for attempt in range(2 if cache_write else 1):
                result = sh(['bash', str(REPO / 'bin/aiw'), seat, str(root), '10', str(prompt),
                             '--no-fallback', '--no-findings', *flags],
                            env=dict(env, AIW_STATE=str(state), EXPECT_CACHE=str(int(cache_write))))
                check(result.returncode == 0, result.stderr)
                row = json.loads((state / 'seat-ledger.jsonl').read_text().splitlines()[-1])
                scratch = Path(row['run_dir']).resolve() / 'scratch'
                observed = json.loads((scratch.parent / 'observed.json').read_text())
                check(not list(scratch.iterdir()), '临时文件或每单缓存没有清理')
                check(observed['env']['GOTMPDIR'] == str(scratch / 'go-tmp'), observed)
                check(observed['env']['GOMODCACHE'] == '/shared/modules', '模块缓存被改写')
                check(observed['go'].strip() == 'held=1 test -p 2 ./...', observed['go'])
                if cache_write:
                    expected = dict(GOCACHE=str(state / 'cache/go-build'),
                                    npm_config_cache=str(scratch / 'cache/npm'),
                                    XDG_CACHE_HOME=str(scratch / 'cache/xdg'),
                                    PIP_CACHE_DIR=str(scratch / 'cache/pip'))
                    check(all(observed['env'][key] == value for key, value in expected.items()), observed)
                    shared = state / 'cache/go-build'
                    if attempt:
                        check((shared / 'retained').read_text() == 'keep', '共享缓存被清理')
                    (shared / 'retained').write_text('keep')
                else:
                    check(all(observed['env'][key] is None for key in caches), observed)
                    check(not (state / 'cache').exists(), '非 workspace-write 创建了共享缓存')
                if seat == 'codex':
                    argv = observed['argv']
                    config = tomllib.loads('\n'.join(argv[i + 1] for i, arg in enumerate(argv[:-1]) if arg == '-c'))
                    values = config['shell_environment_policy']['set']
                    if cache_write:
                        check(all(values[key] == value for key, value in expected.items()), values)
                        check(config['sandbox_workspace_write']['writable_roots'] == [str(scratch), str(shared)], config)
                    else:
                        check(not set(caches).intersection(values) and scratch_readonly(argv), config)
                    check('GOMODCACHE' not in values and 'PATH' not in values, values)


def scratch_cleanup_boundaries():
    import ai_worker as worker
    from unittest.mock import patch
    with tempfile.TemporaryDirectory() as td:
        root = Path(td).resolve()
        state = root / 'state'
        external = root / 'external'; external.mkdir()
        (external / 'keep').write_text('untouched')
        for outcome in ('ok', 'error', 'timeout', 'interrupt', 'exception'):
            run = root / outcome
            def execute(*args, **kwargs):
                scratch = kwargs['run_dir'] / 'scratch'
                (scratch / 'deliver').mkdir()
                (scratch / 'deliver/report').write_text('retain')
                (scratch / 'temporary').write_text('remove')
                (scratch / 'link').symlink_to(external, target_is_directory=True)
                (scratch / 'nested').mkdir()
                (scratch / 'nested/link').symlink_to(external, target_is_directory=True)
                if outcome == 'interrupt':
                    raise worker.RunInterrupted(130)
                if outcome == 'exception':
                    raise RuntimeError('injected failure')
                return dict(rc=0 if outcome == 'ok' else 124 if outcome == 'timeout' else 1,
                            kind=outcome, out='', err='', secs=0)
            with patch.object(worker, 'STATE', state), patch.object(worker, '_run_seat', execute):
                try:
                    worker.run_seat('codex', 'task', str(root), 10, False, run_dir=run)
                except (worker.RunInterrupted, RuntimeError):
                    check(outcome in ('interrupt', 'exception'), outcome)
                else:
                    check(outcome not in ('interrupt', 'exception'), '异常被吞掉')
            check([p.name for p in (run / 'scratch').iterdir()] == ['deliver'], outcome)
            check((run / 'scratch/deliver/report').read_text() == 'retain', outcome)
            check((external / 'keep').read_text() == 'untouched', '清理跟随了软链')
        # 不清理未由本次创建的 scratch，也不通过缓存软链开放其它目录。
        existing = root / 'existing'; (existing / 'scratch').mkdir(parents=True)
        (existing / 'scratch/keep').write_text('existing')
        with patch.object(worker, 'STATE', state), patch.object(worker, '_run_seat') as execute:
            result = worker.run_seat('codex', 'task', str(root), 10, False, run_dir=existing)
            check(result['kind'] == 'scratch' and not execute.called, result)
            check((existing / 'scratch/keep').read_text() == 'existing', '清理了已有 scratch')
        for name in ('cache', 'cache/go-build'):
            blocked = root / ('blocked-' + name.replace('/', '-')); blocked.mkdir()
            link = blocked / name; link.parent.mkdir(exist_ok=True)
            link.symlink_to(external, target_is_directory=True)
            run = root / ('run-' + blocked.name)
            with patch.object(worker, 'STATE', blocked), patch.object(worker, '_run_seat') as execute:
                result = worker.run_seat('codex', 'task', str(root), 10, False, run_dir=run)
                check(result['kind'] == 'scratch' and not execute.called, result)
            check(not list((run / 'scratch').iterdir()), '初始化失败未清临时目录')
            check(list(external.iterdir()) == [external / 'keep'], '缓存软链修改了外部目录')


def delivery_forward_local():
    import ai_worker as worker
    from contextlib import redirect_stderr
    from io import StringIO
    from unittest.mock import patch
    with patch.object(worker, 'SEATS', {'seats': {'local': {'local_only': True}, 'remote': {}}}), \
            patch.object(worker.subprocess, 'run') as run, \
            patch.object(worker.subprocess, 'Popen') as popen:
        for seat in ('local', 'remote'):
            stderr = StringIO()
            with redirect_stderr(stderr):
                code = worker.forward_dispatch(['unused'], seat, '/repo', '10', '/prompt', set(), [],
                                               {'deliver': '/home/out'}, {}, chain=[seat])
            check(code is None, '带 --deliver 的派单应留在本机执行')
            if seat == 'remote':
                check(stderr.getvalue() == '[ai-worker] --deliver 只在本机执行，不转发\n', stderr.getvalue())
            run.assert_not_called()
            popen.assert_not_called()


def delivery_failure_contract():
    from capabilities import capabilities
    from background import DISPATCH_FLAGS
    from structured import split_args
    check('--deliver' in capabilities()['dispatch']['values'] and '--deliver' in DISPATCH_FLAGS,
          '入口未声明交付参数')
    args = ['codex', '/repo', '10', 'prompt', '--deliver', '/home/out', '--schema', 'schema']
    rest, schema, _, positions = split_args(args)
    check(schema == 'schema' and positions == [0, 1, 2, 3] and rest[-2:] == ['--deliver', '/home/out'], rest)
    with tempfile.TemporaryDirectory(dir=Path.home()) as td:
        root = Path(td).resolve()
        prompt = root / 'prompt'; prompt.write_text('task')
        script = root / 'cli'
        script.write_text('''#!/usr/bin/env python3
import os, pathlib, sys
p = pathlib.Path(os.environ['AIW_SCRATCH']) / 'deliver'
p.mkdir()
(p / 'incomplete').write_text('must not be delivered')
(p / 'second').write_text('must not be delivered either')
print('unknown failure', file=sys.stderr)
sys.exit(1)
''')
        script.chmod(0o755)
        state = root / 'state'
        target = root / 'out'
        env = dict(os.environ, AIW_STATE=str(state), CODEX_BIN=str(script))
        cmd = ['bash', str(REPO / 'bin/aiw'), 'codex', str(root), '10', str(prompt),
               '--no-fallback', '--no-findings', '--deliver', str(target)]
        result = sh(cmd, env=env)
        check(result.returncode == 1 and not target.exists(), result.stderr)
        row = json.loads((state / 'seat-ledger.jsonl').read_text().splitlines()[-1])
        check(row['kind'] == 'other' and row['delivery']['error'] and not row['delivery']['delivered'], row)
        result_json = json.loads((Path(row['run_dir']) / 'result.json').read_text())
        check(result_json['delivery'] == row['delivery'], result_json)
        check(len(list((Path(row['run_dir']) / 'scratch/deliver').iterdir())) == 2,
              '假执行方未留下两个待交付文件')
        # 没有独立 scratch 写权限的适配器不伪报支持，也不启动执行方。
        cmd[2] = 'claude'
        result = sh(cmd, env=dict(env, CLAUDE_BIN=str(script)))
        check(result.returncode == 1, result.stderr)
        row = json.loads((state / 'seat-ledger.jsonl').read_text().splitlines()[-1])
        check(row['capabilities'] == dict(scratch_writable=False, network=None, deliver=False), row)
        check(not (Path(row['run_dir']) / 'scratch/deliver').exists(), '不支持仍运行执行方')
        blocked = root / 'blocked'; blocked.mkdir()
        (blocked / 'runs').write_text('not a directory')
        cmd[2] = 'codex'
        result = sh(cmd, env=dict(env, AIW_STATE=str(blocked)))
        check(result.returncode == 1 and 'scratch 创建失败' in result.stderr and 'Traceback' not in result.stderr,
              result.stderr)
        row = json.loads((blocked / 'seat-ledger.jsonl').read_text().splitlines()[-1])
        check(row['kind'] == 'scratch' and row['delivery']['error'], row)


def scratch_readonly_paths():
    import ai_worker as worker
    # A 的原始复现，以及等价拼写；直接调用和执行入口均须拒绝。
    for tail in (['-C', '/repo'], ['-C/repo'], ['--cd=/repo'],
                 ['--cd', '/scr', '--cd', '/repo'], ['--add-dir', '/repo'], ['--add-dir=/repo']):
        argv = ['codex', 'exec', '--sandbox', 'read-only', *tail, '{prompt}']
        check(worker.readonly_argv_error('codex', argv, '{prompt}'), argv)
        try:
            worker.scratch_argv('codex', argv, Path('/scr'), '/repo', True, True, '{prompt}')
        except ValueError:
            pass
        else:
            raise AssertionError(f'未拒绝路径覆盖：{argv}')
    argv = ['codex', 'exec', '--sandbox', 'read-only', '--cd', '/repo', '{prompt}']
    actual = worker.scratch_argv('codex', argv, Path('/scr'), '/repo', True, True, '{prompt}')
    check(scratch_readonly(actual) and actual[actual.index('--cd') + 1] == '/repo', actual)


def scratch_write_modes():
    import ai_worker as worker
    from unittest.mock import patch
    from support import write_legacy_local, fake_home
    with tempfile.TemporaryDirectory() as td:
        root = Path(td).resolve()
        _, env = fake_home(root)
        work = root / 'work'; work.mkdir()
        prompt = root / 'prompt'; prompt.write_text('task')
        wrapper = root / 'runtime.py'
        wrapper.write_text('import json, sys\n'
                           f'sys.path.insert(0, {str(REPO)!r})\n'
                           'import ai_worker as worker\n'
                           'worker.SEATS["seats"]["codex"]["argv_rw"] = json.loads(sys.argv.pop(1))\n'
                           'raise SystemExit(worker.main())\n')
        for index, sandbox in enumerate((['--sandbox', 'danger-full-access'],
                                         ['--dangerously-bypass-approvals-and-sandbox'],
                                         ['--sandbox', 'read-only'])):
            argv = ['{bin}', 'exec', *sandbox, '--cd', '{workdir}', '{prompt}']
            # 使用现有 codex 座位和复现 argv，无 --sandbox 时也不能 list.index 崩溃。
            check(worker.scratch_argv('codex', argv, Path('/scr'), '/repo', False, True, '{prompt}') == argv,
                  '复现 argv 被改写')
            cfg = dict(worker.SEATS['seats']['codex'], argv_rw=argv)
            seats = {**worker.SEATS, 'seats': {**worker.SEATS['seats'], 'codex': cfg}}
            with patch.object(worker, 'SEATS', seats):
                check(worker.scratch_argv('codex', argv, Path('/scr'), '/repo', False, True, '{prompt}') == argv,
                      '非 workspace-write 写单改动了 argv')
            local = root / f'config-{index}.json'
            write_legacy_local(local, {'seats': {'codex': {'argv_rw': argv}}})
            args = root / f'args-{index}'
            cmd = [sys.executable, str(REPO / 'ai_worker.py'), 'codex', str(work), '10', str(prompt), '--no-fallback']
            r = sh(cmd + ['--no-net'], env=dict(env, AIW_CONFIG=str(local), FAKE_CODEX_ARGS=str(args)))
            expected_rc = 64 if sandbox == ['--sandbox', 'read-only'] else 1
            check(r.returncode == expected_rc and not args.exists(), r.stderr)
            # 直接入口的复现输入也要返回 64，不能仅依赖配置加载拒绝。
            runtime_cmd = [sys.executable, str(wrapper), json.dumps(argv), *cmd[2:]]
            r = sh(runtime_cmd + ['--no-net'], env=dict(env, FAKE_CODEX_ARGS=str(args)))
            check(r.returncode == 64 and not args.exists(), r.stderr)
            r = sh(runtime_cmd, env=dict(env, FAKE_CODEX_ARGS=str(args)))
            check(r.returncode == 0, r.stderr)
            actual = args.read_text().splitlines()
            check(actual[actual.index('--cd') + 1] == str(work), actual)
            check(not any('sandbox_workspace_write.' in a for a in actual), actual)
            args.unlink()
            # 无效配置由现有配置守卫拒绝；不放宽守卫。有效的 read-only 写单核对 cwd。
            if sandbox == ['--sandbox', 'read-only']:
                r = sh(cmd, env=dict(env, AIW_CONFIG=str(local), FAKE_CODEX_ARGS=str(args)))
                check(r.returncode == 0 and 'Traceback' not in r.stderr, r.stderr)
                actual = args.read_text().splitlines()
                check(actual[actual.index('--cd') + 1] == str(work), actual)
                check(not any('sandbox_workspace_write.' in a for a in actual), actual)
                row = json.loads((root / 'state/seat-ledger.jsonl').read_text().splitlines()[-1])
                check(row['capabilities'] == dict(scratch_writable=False, network=None, deliver=False), row)


def scratch_readonly_context():
    from cases.findings import findings_worker_fixture
    with tempfile.TemporaryDirectory() as td:
        run, work, _, _, _ = findings_worker_fixture(Path(td))
        backend = Path(td) / 'cli'
        backend.write_text('#!/usr/bin/env python3\nimport os, json, sys\nprint(json.dumps(dict(cwd=os.getcwd(), argv=sys.argv[1:])))\n')
        backend.chmod(0o755)
        for flags in (('--ro',), ('--text',), ('--ro', '--no-net')):
            r, row, _ = run(flags=(*flags, '--no-fallback'), CODEX_BIN=str(backend))
            check(r.returncode == 0, r.stderr)
            observed = json.loads(r.stdout)
            check(observed['cwd'] == str(work.resolve()) and scratch_readonly(observed['argv']), observed)
            check(row['capabilities'] == dict(scratch_writable=False, network=False, deliver=False), row)
            check('scratch_writable=false' in (Path(row['run_dir']) / 'task.txt').read_text(), row)
            destination = (Path(td) / 'private/home/results').resolve()
            r, row, _ = run(flags=(*flags, '--no-fallback', '--deliver', str(destination)), CODEX_BIN=str(backend))
            check(r.returncode == 1 and row['kind'] == 'delivery' and not r.stdout, r.stderr)


def delivery_execution_paths():
    from delivery import deliver_files, PROTECTED, validate_target
    import shutil
    with tempfile.TemporaryDirectory(dir=Path.home()) as td:
        root = Path(td)
        source = root / 'scratch/deliver'
        source.mkdir(parents=True)
        paths = ('Library/LaunchAgents/x.plist', '.zshenv', 'sub/.hidden/file',
                 'sub/Library/LaunchDaemons/x.plist', '.config/autostart/x', '.local/bin/x')
        for name in paths:
            path = source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('blocked')
            for target in (Path.home(), root / 'out'):
                result = deliver_files(root / 'scratch', str(target))
                check(result['error'] and not result['delivered'], result)
            check(not (root / 'out').exists(), '拦截前创建了目标')
            shutil.rmtree(source)
            source.mkdir()
        for name in (*PROTECTED, 'sub/.anything', 'sub/Library/LaunchAgents'):
            for base in (Path.home(), root):
                try:
                    validate_target(str(base / name / 'out'))
                except ValueError:
                    pass
                else:
                    raise AssertionError(f'未拒绝目标：{name}')


def delivery_rollback():
    import delivery
    from unittest.mock import patch
    with tempfile.TemporaryDirectory(dir=Path.home()) as td:
        root = Path(td)
        source = root / 'scratch/deliver'
        (source / 'sub').mkdir(parents=True)
        (source / 'sub/a').write_text('new')
        (source / 'sub/z').write_text('new')
        real_link = delivery.os.link
        def fail_second(src, dst, **kwargs):
            if dst == 'z':
                raise OSError('injected link failure')
            return real_link(src, dst, **kwargs)
        for existing in (False, True):
            target = root / ('existing' if existing else 'new/target')
            if existing:
                target.mkdir()
                (target / 'keep').write_text('untouched')
            with patch.object(delivery.os, 'link', side_effect=fail_second):
                result = delivery.deliver_files(root / 'scratch', str(target))
            check('injected link failure' in result['error'] and not result['delivered'], result)
            check(not (target / 'sub').exists(), '失败未回滚新目录和文件')
            if existing:
                check((target / 'keep').read_text() == 'untouched', '回滚碰了原文件')
            else:
                check(not (root / 'new').exists(), '目标祖先目录未回滚')
        # 预检的冲突不能先建任何目录。
        (target / 'sub').mkdir()
        (target / 'sub/z').write_text('old')
        with patch.object(delivery.os, 'mkdir', side_effect=AssertionError('预检之前 mkdir')):
            result = delivery.deliver_files(root / 'scratch', str(target))
        check('内容不同' in result['error'], result)
        # 发布后被其它写入者替换：不能把别人的文件当作本批文件回滚。
        race_target = root / 'race'; race_target.mkdir()
        def replace_after_link(src, dst, **kwargs):
            real_link(src, dst, **kwargs)
            parent = kwargs['dst_dir_fd']
            os.unlink(dst, dir_fd=parent)
            fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=parent)
            with os.fdopen(fd, 'w') as stream:
                stream.write('external')
        with patch.object(delivery.os, 'link', side_effect=replace_after_link):
            result = delivery.deliver_files(root / 'scratch', str(race_target))
        check('目标发布后被替换' in result['error'] and '回滚失败' in result['error'], result)
        check((race_target / 'sub/a').read_text() == 'external', '回滚删除了并发替换文件')


def delivery_size_and_hash():
    import delivery
    from unittest.mock import patch
    with tempfile.TemporaryDirectory(dir=Path.home()) as td:
        root = Path(td)
        source = root / 'scratch/deliver'
        source.mkdir(parents=True)
        (source / 'a').write_bytes(b'abc')
        (source / 'b').write_bytes(b'def')
        target = root / 'out'
        with patch.object(delivery, 'MAX_BYTES', 5):
            result = delivery.deliver_files(root / 'scratch', str(target))
        check('2 GiB' in result['error'] and not target.exists(), result)
        check(delivery.MAX_BYTES == 2 * 1024 ** 3, '总大小限额错误')
        with patch.object(delivery, 'digest', side_effect=AssertionError('新文件不应整读复核')):
            result = delivery.deliver_files(root / 'scratch', str(target))
        check(not result['error'] and len(result['delivered']) == 2, result)
        for item in result['delivered']:
            check(hashlib.sha256((target / item['path']).read_bytes()).hexdigest() == item['sha256'], item)
        # 稀疏文件实测 2 GiB 总限额，不分配或读取大内容。
        for name in ('a', 'b'):
            with (source / name).open('wb') as stream:
                stream.truncate(1024 ** 3 + 1)
        result = delivery.deliver_files(root / 'scratch', str(root / 'oversize'))
        check('2 GiB' in result['error'] and not (root / 'oversize').exists(), result)


CASES = {f.__name__: f for f in (scratch_delivery, delivery_boundaries, delivery_forward_local, delivery_failure_contract,
    scratch_policy_override, scratch_cache_environment, scratch_cleanup_boundaries, scratch_readonly_paths, scratch_write_modes, scratch_readonly_context,
    delivery_execution_paths, delivery_rollback, delivery_size_and_hash)}
