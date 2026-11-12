"""codex home regression cases."""
from __future__ import annotations

from support import (
    inprocess_sh,
    Path,
    REPO,
    check,
    codex_home_fixture,
    codex_home_run_case,
    os,
    sh,
    sys,
    tempfile,
)


def codex_home_whitelist():
    import tomllib
    from unittest.mock import patch
    import ai_worker as worker
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        source = codex_home_fixture(root)
        with patch.object(worker, 'STATE', root / 'state'), patch.dict(os.environ, CODEX_HOME=str(source)):
            home, reason = worker.prepare_codex_home()
        check(home is not None and not reason, reason)
        config = tomllib.loads((home / 'config.toml').read_text())
        original = tomllib.loads((source / 'config.toml').read_text())
        check(set(config) == {'model_provider', 'model_providers'}, '白名单外键泄漏')
        check(config['model_providers'] == {'X': original['model_providers']['X']}, 'provider 嵌套表或转义丢失')
        check(home.stat().st_mode & 0o777 == 0o700, 'home 权限错误')
        check((home / 'config.toml').stat().st_mode & 0o777 == 0o600, 'config 权限错误')



def codex_home_auth_symlink():
    from unittest.mock import patch
    import ai_worker as worker
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        source = codex_home_fixture(root)
        with patch.object(worker, 'STATE', root / 'state'), patch.dict(os.environ, CODEX_HOME=str(source)):
            home, reason = worker.prepare_codex_home()
            check(home is not None, reason)
            link = home / 'auth.json'
            check(link.is_symlink() and os.readlink(link) == str(source.resolve() / 'auth.json'), 'auth 应软链到源文件')
            before = link.lstat()
            worker.prepare_codex_home()
            after = link.lstat()
            check((before.st_ino, before.st_mtime_ns) == (after.st_ino, after.st_mtime_ns), '正确软链不应重建')
            (source / 'auth.json').unlink()
            worker.prepare_codex_home()
            check(not link.is_symlink(), '源 auth 消失时应清除旧链')



def codex_home_unchanged():
    from unittest.mock import patch
    import ai_worker as worker
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        source = codex_home_fixture(root)
        with patch.object(worker, 'STATE', root / 'state'), patch.dict(os.environ, CODEX_HOME=str(source)):
            home, reason = worker.prepare_codex_home()
            check(home is not None, reason)
            target = home / 'config.toml'
            before = target.stat()
            worker.prepare_codex_home()
            after = target.stat()
            check((before.st_ino, before.st_mtime_ns) == (after.st_ino, after.st_mtime_ns), '内容不变不应重写')



def codex_home_fallback():
    codex_home_run_case('fallback')



def codex_home_shared_switch():
    codex_home_run_case('shared')



def codex_home_exec_env():
    codex_home_run_case('normal')



def codex_home_claude_unchanged():
    codex_home_run_case('claude')



def codex_home_write_failure():
    from unittest.mock import patch
    import ai_worker as worker
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        source = codex_home_fixture(root)
        blocked = root / 'blocked'
        blocked.write_text('not a directory')
        with patch.object(worker, 'STATE', blocked), patch.dict(os.environ, CODEX_HOME=str(source)):
            home, reason = worker.prepare_codex_home()
            check(home is None and '写入执行方 home' in reason, '写入失败应安全返回原因')
        default = root / '.codex'
        source.rename(default)
        with patch.object(worker, 'STATE', root / 'state'), patch.dict(os.environ, HOME=str(root)):
            os.environ.pop('CODEX_HOME', None)
            home, reason = worker.prepare_codex_home()
            check(home is not None and not reason, '默认源应取 HOME/.codex')



def codex_home_doctor():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        source = codex_home_fixture(root)
        env = dict(os.environ, CODEX_HOME=str(source), AIW_STATE=str(root / 'state'))
        for mode in ('normal', 'bad', 'shared'):
            if mode == 'bad':
                (source / 'config.toml').write_text('broken = [ "sensitive-fake-value"')
            if mode == 'shared':
                env['AIW_CODEX_SHARED_HOME'] = '1'
            r = inprocess_sh([sys.executable, str(REPO / 'ai_worker.py'), 'doctor'], env=env)
            check(r.returncode == 0, r.stderr)
            line = next((v for v in r.stdout.splitlines() if 'Codex 执行方 home:' in v), '')
            check(str(root / 'state/codex-home') in line, 'doctor 未显示路径')
            check({'normal': '可生成', 'bad': '不可生成：', 'shared': '已关闭'}[mode] in line, 'doctor 状态错误')
            for value in ('fake-provider', 'https://fake.invalid', 'fake-test-only', 'sensitive-fake-value'):
                check(value not in r.stdout + r.stderr, 'doctor 泄漏配置值')
            check(not (root / 'state').exists(), 'doctor 不应创建 STATE')



def codex_adapter_validation():
    import copy
    import json
    import seats_config as config
    public = config.load_public(REPO)
    for adapter in ('unknown', 1, True, [], {}):
        merged = config.merge(public, {'seats': {'codex': {'adapter': adapter}}})
        check(any('seats.codex.adapter' in e for e in config.validate(merged)), str(adapter))
        with tempfile.TemporaryDirectory() as td:
            local = Path(td) / 'local.json'
            local.write_text(json.dumps({'seats': {'codex': {'adapter': adapter}}}))
            from unittest.mock import patch
            with patch.dict(os.environ, AIW_CONFIG=str(local)):
                try:
                    config.load_seats(REPO)
                except config.ConfigError as e:
                    check('seats.codex.adapter' in str(e), str(e))
                else:
                    check(False, '加载未拒绝非法 adapter')
    merged = config.merge(public, {'seats': {'text': {'kind': 'agent_use', 'provider': 'test', 'adapter': 'codex'}}})
    check(any('seats.text.adapter' in e for e in config.validate(merged)), 'agent_use adapter 未拒绝')
    for key in ('argv_rw', 'argv_ro'):
        for argv in (['echo', 'exec', '--sandbox', 'workspace-write', '{prompt}'],
                     ['{bin}', 'other', '--sandbox', 'read-only'], ['{bin}', 'exec'],
                     ['{bin}', 'exec', '--sandbox'], ['{bin}', 'exec', '--sandbox', 'bad'],
                     ['{bin}', 'exec', '--sandbox', 'read-only', '--sandbox', 'workspace-write']):
            merged = config.merge(public, {'seats': {'codex': {key: argv}}})
            check(any(f'seats.codex.{key}' in e for e in config.validate(merged)), str(argv))
            with tempfile.TemporaryDirectory() as td:
                local = Path(td) / 'local.json'
                local.write_text(json.dumps({'seats': {'codex': {key: argv}}}))
                from unittest.mock import patch
                with patch.dict(os.environ, AIW_CONFIG=str(local)):
                    try:
                        config.load_seats(REPO)
                    except config.ConfigError as e:
                        check(f'seats.codex.{key}' in str(e), str(e))
                    else:
                        check(False, '加载未拒绝非法 adapter argv')
    for override, error_path in (({'argv_rw': ['{bin}', '{prompt}']}, 'seats.codex.argv_rw'),
                                 ({'kind': 'agent_use', 'provider': 'test'}, 'seats.codex.adapter')):
        with tempfile.TemporaryDirectory() as td:
            local = Path(td) / 'local.json'
            local.write_text(json.dumps({'seats': {'codex': override}}))
            from unittest.mock import patch
            with patch.dict(os.environ, AIW_CONFIG=str(local)):
                try:
                    config.load_seats(REPO)
                except config.ConfigError as e:
                    check(error_path in str(e) and '"adapter": null' in str(e), str(e))
                else:
                    check(False, '加载未拒绝不兼容的继承 adapter')
                local.write_text(json.dumps({'seats': {'codex': dict(override, adapter=None)}}))
                loaded = config.load_seats(REPO)['seats']['codex']
                check(loaded['adapter'] is None and all(loaded[key] == value for key, value in override.items()),
                      str(loaded))
    for override, expected in (({'effort': 'high'}, 'codex'), ({'adapter': None}, None)):
        merged = config.merge(public, {'seats': {'codex': override}})
        check(not config.validate(merged) and merged['seats']['codex'].get('adapter') == expected,
              'adapter 继承/清除错误')
    merged = copy.deepcopy(public)
    merged['seats']['claude']['adapter'] = None
    merged['seats']['text'] = {'kind': 'agent_use', 'provider': 'test', 'adapter': None}
    check(not config.validate(merged), 'cli null 非法')
    import ai_worker as worker
    from unittest.mock import patch
    with patch.object(worker, 'SEATS', merged):
        check(worker.seat_adapter('text') is None, '非 cli adapter 未忽略')


def codex_adapter_alias():
    import copy
    import json
    from unittest.mock import patch
    import ai_worker as worker
    import seats_config as config
    from support import fake_bins
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        bins = fake_bins(root)
        source = codex_home_fixture(root)
        cli = bins / 'codex'
        cli.write_text(cli.read_text().replace('case "$FAKE_CODEX" in',
                       'printf "%s\\n" "$CODEX_HOME" > "$FAKE_HOME_LOG"\ncase "$FAKE_CODEX" in'))
        work = root / 'repo'; work.mkdir()
        for cmd in (['git', 'init', '-q'], ['git', '-c', 'user.name=t', '-c', 'user.email=t@t',
                                          'commit', '--allow-empty', '-qm', 'init']):
            r = sh(cmd, cwd=work); check(r.returncode == 0, r.stderr)
        linked = root / 'linked'
        r = sh(['git', 'worktree', 'add', '-qb', 'linked', str(linked)], cwd=work)
        check(r.returncode == 0, r.stderr)
        seats = config.load_public(REPO)['seats']
        alias = copy.deepcopy(seats['codex'])
        plain = copy.deepcopy(alias); plain.pop('adapter')
        readonly = copy.deepcopy(alias); readonly['argv_rw'] = readonly['argv_ro']
        local = root / 'local.json'
        local.write_text(json.dumps({'seats': {'codex-high': alias, 'plain': plain, 'readonly': readonly,
                                              'alias-ro': dict(alias, ro_only=True),
                                              'codex': {'adapter': None}},
                                    'fallback': {'plain': ['codex-high']}}))
        env = dict(os.environ, AIW_CONFIG=str(local), HOME=str(root), CODEX_HOME=str(source),
                   AIW_STATE=str(root / 'state'), PATH=f"{bins}:{os.environ['PATH']}",
                   FAKE_CODEX_ARGS=str(root / 'args'), FAKE_HOME_LOG=str(root / 'home'),
                   FAKE_CODEX='tokens', FAKE_CODEX_STDERR=str(root / 'stderr'))
        (root / 'stderr').write_text('tokens used\n123\n')
        prompt = root / 'prompt'; prompt.write_text('reply OK')
        def run(seat, flags=(), script=None, **extra):
            cmd = [sys.executable, str(REPO / 'ai_worker.py'), seat, str(linked), '30', str(prompt),
                   '--no-findings', *flags]
            if script:
                cmd = [sys.executable, '-c', script, *cmd[2:]]
            return sh(cmd, env=dict(env, **extra), cwd=REPO)
        def row():
            return json.loads((root / 'state/seat-ledger.jsonl').read_text().splitlines()[-1])
        merged = config.merge(config.load_public(REPO), json.loads(local.read_text()))
        with patch.object(worker, 'SEATS', merged):
            check(worker.codex_workspace_write('codex-high', False), '别名写单判定失效')
            for seat, ro in (('codex-high', True), ('readonly', False), ('plain', False), ('codex', False)):
                check(not worker.codex_workspace_write(seat, ro), f'{seat} 误判写单')
        for seat, flags, adapted in (('codex-high', ('--net',), True), ('codex-high', ('--ro',), True),
                                      ('codex-sol', ('--ro',), True), ('plain', (), False),
                                      ('codex', (), False), ('claude', ('--ro',), False)):
            r = run(seat, flags); check(r.returncode == 0, r.stderr)
            record = row(); task = (Path(record['run_dir']) / 'task.txt').read_text()
            check(('派单里对规则' in task) == adapted, f'{seat} 提醒错误')
            check(('tokens' in record) == (adapted or seat == 'claude'), f'{seat} token 字段错误: {record}')
            if seat == 'claude':
                continue
            argv = (root / 'args').read_text().splitlines()
            check(('shell_environment_policy.set.AIW_NESTED="1"' in argv) == adapted, f'{seat} nested 参数错误')
            check(('skills.include_instructions=false' in argv) == adapted, f'{seat} skills 参数错误')
            check((root / 'home').read_text().strip() == (str((root / 'state/codex-home').resolve()) if adapted else str(source)),
                  f'{seat} CODEX_HOME 错误')
            write = adapted and '--ro' not in flags
            check(any(a.startswith('sandbox_workspace_write.writable_roots=') for a in argv) == write,
                  f'{seat} worktree 参数错误')
            check(('本目录是 linked worktree' in task) == write, f'{seat} worktree 提醒错误')
            if write:
                import tomllib
                roots_arg = next(a for a in argv if a.startswith('sandbox_workspace_write.writable_roots='))
                check(tomllib.loads(roots_arg)['sandbox_workspace_write']['writable_roots'] ==
                      worker.worktree_writable_roots(str(linked)), '别名 worktree roots 错误')
                check('sandbox_workspace_write.network_access=true' in argv and '已开放网络' in task,
                      '别名 net 覆盖错误')
        for seat, flags in (('codex-high', ('--ro', '--net')), ('codex-sol', ('--net',)),
                            ('readonly', ('--net',)), ('alias-ro', ('--net',)), ('plain', ('--net',)), ('claude', ('--net',))):
            r = run(seat, flags); check(r.returncode == 64, r.stderr)
        r = run('codex-high', (), AIW_CODEX_SHARED_HOME='1')
        check(r.returncode == 0 and 'skills.include_instructions=false' not in (root / 'args').read_text(), r.stderr)
        script = ('import ai_worker as w\n'
                  'def run(*a, **k):\n'
                  ' raise w.subprocess.TimeoutExpired(a[0], a[1], output="", stderr="tokens used\\n123\\n")\n'
                  'w.run_group=run\nraise SystemExit(w.main())')
        for seat, adapted in (('codex-high', True), ('plain', False)):
            r = run(seat, script=script)
            check(r.returncode == 124 and row()['err_tail'] == 'timeout', r.stderr)
            check((row().get('tokens') == {'source': 'stderr', 'uncached_plus_output': 123}) == adapted,
                  '别名超时 token 解析错误')
        # plain 认证失败后由别名执行，提醒与 token 必须按 actual 判断。
        r = run('plain', FAKE_CODEX='auth_sol', FAKE_CODEX_AUTH_MARKER=str(root / 'marker'))
        check(r.returncode == 0 and row()['actual'] == 'codex-high', r.stderr)
        record = row(); task = (Path(record['run_dir']) / 'task.txt').read_text()
        check('派单里对规则' in task and '本目录是 linked worktree' in task and 'tokens' in record,
              '顶替未按实际 adapter 处理')
        r = sh([sys.executable, str(REPO / 'ai_worker.py'), 'doctor'], env=env)
        check(r.returncode == 0, r.stderr)
        warning = '⚠ seats.plain.adapter：看起来是 Codex CLI，但未声明 adapter，不享有 codex 专属行为'
        check(warning in r.stdout and '⚠ seats.codex.adapter' in r.stdout,
              '缺少 adapter doctor 警告: ' + r.stdout)


CASES = {case.__name__: case for case in (
    codex_adapter_validation,
    codex_adapter_alias,
    codex_home_whitelist,
    codex_home_auth_symlink,
    codex_home_unchanged,
    codex_home_fallback,
    codex_home_shared_switch,
    codex_home_exec_env,
    codex_home_claude_unchanged,
    codex_home_write_failure,
    codex_home_doctor,
)}
