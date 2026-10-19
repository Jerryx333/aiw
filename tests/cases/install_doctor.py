"""install doctor regression cases."""
from __future__ import annotations

from support import (
    install_phase,
    inprocess_sh,
    git_template,
    Path,
    REPO,
    check,
    codex_home_fixture,
    fake_agent_use,
    fake_bins,
    fake_home,
    free_guard_fixture,
    json,
    os,
    sh,
    shutil,
    sys,
    tempfile,
    write_legacy_local,
)


def agent_use_default_paths():
    from unittest.mock import patch
    from env_compat import agent_use_home
    import ai_worker
    import snapshot
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        default = t / '.local/share/agent-use'
        legacy = t / 'code/ai/agent-use'
        explicit = t / 'explicit/agent-use'
        env = dict(os.environ, HOME=str(t))
        env.pop('AGENT_USE_HOME', None)
        env.pop('AIW_ADAPTERS_HOME', None)

        def verify(expected, adapters=None):
            with patch.dict(os.environ, env, clear=True):
                check(agent_use_home() == expected, str(agent_use_home()))
                check(ai_worker.agent_use_home() == expected, 'worker 路径不一致')
                check(snapshot.agent_use_home() == expected, 'snapshot 路径不一致')
            # Run outside the repository to verify module lookup in the sourced helper.
            command = ('H="$1"; PY="$2"; source "$H/agent_use_setup.sh"; '
                       'aiw_agent_use_paths; printf "%s\\n" "$AU" "$ADAPTERS"')
            result = sh(['bash', '-c', command, 'paths', str(REPO), sys.executable], env=env, cwd=t)
            check(result.returncode == 0 and result.stdout.splitlines() ==
                  [str(expected), str(adapters or expected.parent / 'agent-use-adapters')],
                  result.stdout + result.stderr)

        verify(default)  # Neither exists.
        env['AGENT_USE_HOME'] = ''
        verify(default)  # Empty is also unset.
        legacy.mkdir(parents=True)
        verify(legacy)
        default.mkdir(parents=True)
        verify(default)  # Both exist; new default wins.
        env['AGENT_USE_HOME'] = '~/explicit/agent-use'
        verify(explicit)  # Explicit paths need not exist.
        env['AGENT_USE_HOME'] = 'relative/agent-use'
        verify(Path('relative/agent-use'))  # Preserve relative overrides.
        env['AGENT_USE_HOME'] = str(explicit)
        env['AIW_ADAPTERS_HOME'] = '~/custom-adapters'
        verify(explicit, t / 'custom-adapters')
        env.pop('AGENT_USE_HOME')
        env.pop('AIW_ADAPTERS_HOME')
        default.rmdir()
        legacy.rmdir()
        target = t / 'installed-core'
        target.mkdir()
        legacy.symlink_to(target, target_is_directory=True)
        verify(legacy)
        target.rmdir()  # A dangling legacy symlink is still an existing installation path.
        verify(legacy)
        default.symlink_to(t / 'missing-new', target_is_directory=True)
        verify(default)


def install_agent_use_default_location():
    with tempfile.TemporaryDirectory() as td:
        home, env, _, _ = ecosystem_fixture(Path(td))
        env.pop('AGENT_USE_HOME')
        env.pop('AIW_ADAPTERS_HOME')
        ecosystem_install(home, env, '--with-agent-use')
        check((home / '.local/share/agent-use/.git').is_dir(), '核心未克隆到新默认位置')
        check((home / '.local/share/agent-use-adapters/.git').is_dir(), '适配器未克隆到默认同级位置')


def python_requirement():
    from unittest.mock import patch
    import contextlib
    import io
    import ai_worker

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        home, env = fake_home(root)
        repo = home / '.claude/aiw'
        calls = root / 'python-calls'
        fake = root / 'old-python'
        fake.write_text('#!' + sys.executable + '\n' +
                        'import pathlib, sys\n' +
                        'with pathlib.Path(' + repr(str(calls)) + ').open("a") as log: log.write("call\\n")\n' +
                        'assert sys.argv[1] == "-c", "门禁后仍启动了子命令"\n' +
                        'code = sys.argv[2]\n' +
                        'sys.argv = ["-c", *sys.argv[3:]]\n' +
                        'sys.version_info = (3, 10, 9)\nsys.version = "3.10.9 (fake)"\n' +
                        'exec(code)\n')
        fake.chmod(0o755)
        env['AIW_PYTHON'] = str(fake)

        def snapshot():
            return {str(p.relative_to(home)): (p.lstat().st_mode,
                    os.readlink(p) if p.is_symlink() else p.read_bytes() if p.is_file() else None)
                    for p in home.rglob('*')}

        before = snapshot()
        result = sh(['bash', str(repo / 'install.sh')], env=env)
        message = '需要 Python 3.11 及以上；可用 AIW_PYTHON 指定解释器'
        check(result.returncode != 0 and '3.10.9' in result.stderr and message in result.stderr,
              result.stdout + result.stderr)
        check(len(result.stderr.splitlines()) == 1, '安装低版本提示应只有一行')
        check(snapshot() == before, '低版本安装改动了文件或权限')
        for command in ('help', 'sync', 'board', 'statusline', 'doctor', 'route', 'write'):
            calls.unlink(missing_ok=True)
            result = sh(['bash', str(repo / 'bin/aiw'), command], env=env)
            check(result.returncode == 1 and '3.10.9' in result.stderr and message in result.stderr,
                  command + ': ' + result.stdout + result.stderr)
            check(calls.read_text().splitlines() == ['call'], '入口门禁不应多起 Python 进程')
            if command == 'doctor':
                check('✗ Python 版本' in result.stdout, '低版本 doctor 缺版本检查行')
        check(snapshot() == before, '低版本入口改动了文件或权限')
        result = sh(['bash', str(repo / 'bin/aiw'), 'help'],
                    env=dict(env, AIW_PYTHON=sys.executable))
        check(result.returncode == 0 and '统一入口' in result.stdout, '正常 Python 入口受影响')

    output = io.StringIO()
    with patch.object(ai_worker.sys, 'version_info', (3, 10, 9)), \
            patch.object(ai_worker.sys, 'version', '3.10.9 (fake)'), \
            contextlib.redirect_stdout(output):
        check(ai_worker.doctor() == 1, '低版本 doctor 应返回 1')
    check('✗ Python 版本' in output.getvalue() and message in output.getvalue(), output.getvalue())


def doctor():
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        env = dict(os.environ, PATH=f"{fake_bins(t)}:{os.environ['PATH']}", AIW_STATE=str(t / "state"),
                   AGENT_USE_HOME=str(fake_agent_use(t)))
        versions = t / 'versions'
        codex = t / 'fakebin/codex'
        codex.write_text(codex.read_text().replace('echo "codex-cli fake"',
            'echo version >> "$FAKE_VERSION_LOG"; echo "codex-cli fake"'))
        env['FAKE_VERSION_LOG'] = str(versions)
        p = sh(["bash", str(REPO / "bin" / "aiw"), "doctor"], env=env)
        check(p.returncode == 0, f"doctor 应成功: {p.stdout}{p.stderr}")
        check("✓ Python 版本：" + sys.version.split()[0] in p.stdout, "doctor 缺 Python 版本行")
        for s in ("qoder", "workbuddy", "antigravity", "codex", "host:", "agent-use:qoder", "agent-use:doubao", "codex-cli fake"):
            check(s in p.stdout, f"doctor 输出缺 {s}: {p.stdout}")
        check("仅免费模型" in p.stdout and "硬禁工具" in p.stdout, "doctor 应标出免费模型与工具策略")
        check(versions.read_text().splitlines() == ['version'], '同次 doctor 重复探测同一 CLI')
        p = sh(["bash", str(REPO / "bin" / "aiw"), "doctor"], env=dict(env, AGENT_USE_HOME=str(t / "nope")))
        check(p.returncode == 1 and "AGENT_USE_HOME" in p.stdout, f"agent-use 缺失时应 rc=1 并提示: {p.stdout}")
        check(versions.read_text().splitlines() == ['version', 'version'], 'CLI 版本缓存跨 doctor 运行泄漏')
        p = sh(["bash", str(REPO / "bin" / "aiw"), "doctor"], env=dict(env, CODEX_BIN="/nonexistent/codex"))
        check(p.returncode == 1 and "未找到" in p.stdout, f"缺 CLI 时应报未找到并 rc=1: {p.stdout}")



def doctor_config():
    import importlib.util
    spec = importlib.util.spec_from_file_location('doctor_config_checks', REPO / 'doctor_config.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        home, env = fake_home(t)
        env.pop('AIW_NO_CODEX', None)
        env.pop('CODEX_BIN', None)
        repo = home / '.claude/aiw'
        # 隔离 PATH，避免本机已安装的看板影响测试。
        bins = t / 'fakebin'
        for name in ('bash', 'dirname', 'readlink', 'python3', 'git', 'mkdir', 'chmod', 'ln', 'grep', 'cp', 'date'):
            path = shutil.which(name)
            if path and not (bins / name).exists():
                (bins / name).symlink_to(path)
        env['PATH'] = str(bins)
        p = sh(['bash', str(repo / 'install.sh')], env=env)
        check(p.returncode == 0, p.stdout + p.stderr)
        settings = home / '.claude/settings.json'
        hp = home / '.codex/hooks.json'
        config = home / '.codex/config.toml'
        md = home / '.claude/CLAUDE.md'
        agents = home / '.codex/AGENTS.md'
        original = {f: f.read_text() for f in (settings, hp, md, agents)}

        def run(*expected, **overrides):
            # Configuration variants exercise the same production checks directly;
            # CLI installation/version/exit behavior is covered by doctor().
            probe = "from pathlib import Path; import doctor_config as d, sys; d.check_config(Path(sys.argv[1]), include_local=False)"
            p = inprocess_sh([sys.executable, '-c', probe, str(repo)], cwd=repo, env=dict(env, **overrides))
            check(p.returncode == 0, f'配置改变返回码：{p.stdout}{p.stderr}')
            for text in expected:
                check(text in p.stdout, f'缺少 {text}: {p.stdout}')
            check('PRIVATE_SENTINEL' not in p.stdout + p.stderr, '泄露配置内容')
            return p.stdout

        def trusted(path=hp, g=0, i=0, enabled=True):
            key = f'{path}:user_prompt_submit:{g}:{i}'
            config.write_text(f'[hooks.state.{json.dumps(key)}]\ntrusted_hash = "PRIVATE_SENTINEL"\nenabled = {str(enabled).lower()}\n')

        for path in (hp, hp.resolve()):
            trusted(path)
            run('✓ Claude 派单钩子', '✓ CLAUDE.md 引入', '✓ 安装链接 aiw',
                '✓ 安装链接 aiw', '✓ Codex 信任 [0:0]', '✓ Codex AGENTS 标记块',
                '无法确认是否过期', '· 仓库状态：不是 git 仓库', '⚠ aiw-board 转发',
                '未安装 aiw-board（可选组件）：见 https://github.com/Jerryx333/aiw-board ，安装后确保 aiw-board 在 PATH 中')
        config.write_text('')
        run('⚠ Codex 信任', 'Codex 会跳过这个钩子')
        trusted(enabled=False)
        run('⚠ Codex 信任', '被禁用')
        trusted()
        config.write_text(config.read_text() + '\n[features]\nhooks = false\n')
        run('Codex 关闭了钩子功能')
        trusted()
        data = json.loads(original[hp])
        data['hooks']['UserPromptSubmit'].insert(0, {'hooks': [{'command': 'echo other'}]})
        hp.write_text(json.dumps(data))
        run('⚠ Codex 信任 [1:0]')
        trusted(g=1)
        run('✓ Codex 信任 [1:0]')
        hp.write_text(original[hp])
        data = json.loads(original[hp])
        data['hooks']['UserPromptSubmit'][0]['hooks'].insert(0, {'command': 'echo other'})
        hp.write_text(json.dumps(data))
        trusted()
        run('⚠ Codex 信任 [0:1]')
        trusted(i=1)
        run('✓ Codex 信任 [0:1]')
        data['hooks']['UserPromptSubmit'][0]['hooks'][1]['timeout'] = 99
        hp.write_text(json.dumps(data))
        run('⚠ Codex 派单钩子', '命令或 timeout')
        hp.write_text(original[hp])
        trusted()
        data = json.loads(original[settings])
        data['hooks']['UserPromptSubmit'][0]['hooks'][0]['command'] += ' --changed'
        settings.write_text(json.dumps(data))
        md.write_text('# missing import\n')
        agents.write_text(original[agents].replace(module.BEGIN + '\n', module.BEGIN + '\nchanged\n'))
        run('⚠ Claude 派单钩子', '⚠ CLAUDE.md 引入', '⚠ Codex AGENTS 标记块')
        out = run('· 跳过 Codex', AIW_NO_CODEX='1')
        check('⚠ Codex' not in out and '✓ Codex' not in out, out)
        for f, text in original.items():
            f.write_text(text)
        data = json.loads(original[settings])
        script = home / 'aiw-board/bin/aiw-statusline'
        data['statusLine'] = {'command': f'"{script}"'}
        settings.write_text(json.dumps(data))
        run('⚠ 状态栏命令', '状态栏会静默失效')
        script.parent.mkdir(parents=True)
        script.write_text('#!/bin/sh\nexit 0\n')
        script.chmod(0o755)
        (bins / 'aiw-board').symlink_to(script)
        run('✓ 状态栏命令', '✓ aiw-board 转发')
        script.chmod(0o644)
        run('⚠ 状态栏命令', '⚠ aiw-board 转发')
        settings.write_text(original[settings])
        # 缺文件、坏 JSON、重复和缺失钩子均只提示。
        for text, expected in (('{bad', 'JSONDecodeError'), ('{}', '没有 aiw 钩子'),
                               ('{"hooks": {"UserPromptSubmit": null}}', 'ValueError')):
            settings.write_text(text)
            run('⚠ Claude 派单钩子', expected)
        data = json.loads(original[settings])
        data['hooks']['UserPromptSubmit'] *= 2
        settings.write_text(json.dumps(data))
        run('有多个 aiw 钩子')
        settings.unlink()
        run('⚠ Claude 派单钩子：文件不存在')
        settings.write_text(original[settings])
        # Plugin-managed Claude hooks need no settings hook; duplicate sources warn.
        plugin_name = 'aiw@test-marketplace'
        plugin_dir = home / '.claude/plugins/cache/test-marketplace/aiw/0.1.0'
        plugin_hooks = plugin_dir / 'hooks/hooks.json'
        plugin_hooks.parent.mkdir(parents=True)
        plugin_data = {'hooks': {'UserPromptSubmit': [{'hooks': [{'type': 'command',
            'command': '"${CLAUDE_PLUGIN_ROOT}/scripts/route-hook.sh"', 'timeout': 10}]}]}}
        plugin_hooks.write_text(json.dumps(plugin_data))
        installed = home / '.claude/plugins/installed_plugins.json'
        records = {plugin_name: [{'scope': 'user', 'installPath': str(plugin_dir)}]}
        data = json.loads(original[settings])
        data['enabledPlugins'] = {plugin_name: True}
        settings.write_text(json.dumps(data))
        installed.write_text(json.dumps({'plugins': records}))
        out = run(f'⚠ Claude 派单钩子：settings.json 与插件 {plugin_name} 都有派单钩子，会重复执行',
                  '重跑 install.sh --claude-hook=plugin')
        check('✓ Claude 派单钩子' not in out, out)
        data['hooks']['UserPromptSubmit'] = []
        settings.write_text(json.dumps(data))
        for metadata in ({'plugins': records}, records):
            installed.write_text(json.dumps(metadata))
            out = run(f'✓ Claude 派单钩子（由插件 {plugin_name} 提供）')
            check('⚠ Claude 派单钩子' not in out, out)
        plugin_hooks.write_text(json.dumps({'hooks': {'UserPromptSubmit': [
            {'hooks': [{'type': 'command', 'command': 'echo other'}]}]}}))
        run('⚠ Claude 派单钩子：没有 aiw 钩子。修：重跑 install.sh')
        plugin_hooks.write_text(json.dumps({'hooks': {'UserPromptSubmit': [
            {'hooks': [{'type': 'command', 'command': module.HOOK_COMMAND}]}]}}))
        run(f'✓ Claude 派单钩子（由插件 {plugin_name} 提供）')
        plugin_hooks.write_text(json.dumps(plugin_data))
        multiple_names = ['aiw@a', 'aiw@b']
        multiple_records = {name: records[plugin_name] * 2 for name in multiple_names}
        data['enabledPlugins'] = {name: True for name in reversed(multiple_names)}
        settings.write_text(json.dumps(data))
        installed.write_text(json.dumps({'plugins': multiple_records}))
        out = run('⚠ Claude 派单钩子：多个插件（aiw@a、aiw@b）都有派单钩子，会重复执行。修：在 Claude Code 中只保留一个 aiw 插件启用')
        check('✓ Claude 派单钩子' not in out, out)
        data['hooks'] = json.loads(original[settings])['hooks']
        settings.write_text(json.dumps(data))
        out = run('⚠ Claude 派单钩子：settings.json 与插件 aiw@a、aiw@b 都有派单钩子，会重复执行',
                  '重跑 install.sh --claude-hook=plugin')
        check('✓ Claude 派单钩子' not in out, out)
        data['hooks']['UserPromptSubmit'] = []
        data['enabledPlugins'] = {plugin_name: True}
        installed.write_text(json.dumps({'plugins': records}))
        data['enabledPlugins'][plugin_name] = False
        settings.write_text(json.dumps(data))
        run('⚠ Claude 派单钩子：没有 aiw 钩子。修：重跑 install.sh')
        data['enabledPlugins'][plugin_name] = True
        settings.write_text(json.dumps(data))
        for broken in ('{PRIVATE_SENTINEL', '[]', '{"hooks": {"UserPromptSubmit": {}}}',
                       '{"hooks": {"UserPromptSubmit": [{"hooks": null}]}}',
                       '{"hooks": {"UserPromptSubmit": [{"hooks": [null]}]}}'):
            plugin_hooks.write_text(broken)
            run('⚠ Claude 派单钩子：没有 aiw 钩子。修：重跑 install.sh')
        plugin_hooks.unlink()
        run('⚠ Claude 派单钩子：没有 aiw 钩子。修：重跑 install.sh')
        plugin_hooks.write_text(json.dumps(plugin_data))
        for broken in ('{PRIVATE_SENTINEL', '[]', '{"plugins": []}',
                       json.dumps({plugin_name: {'installPath': str(plugin_dir)}})):
            installed.write_text(broken)
            run('⚠ Claude 派单钩子：没有 aiw 钩子。修：重跑 install.sh')
        installed.unlink()
        run('⚠ Claude 派单钩子：没有 aiw 钩子。修：重跑 install.sh')
        settings.write_text(original[settings])
        config.unlink()
        run('⚠ Codex 配置：文件不存在')
        config.write_text('secret = "PRIVATE_SENTINEL"\nbroken = [')
        run('⚠ Codex 配置', 'TOMLDecodeError')
        hp.unlink()
        run('· 未接入 Codex')
        hp.write_text(original[hp])
        trusted()
        link = home / '.local/bin/aiw'
        link.unlink()
        link.symlink_to(t / 'wrong')
        run('⚠ 安装链接 aiw', '实际指向')
        link.unlink()
        link.symlink_to(repo / 'bin/aiw')
        # 本地提交和上游比较无需联网。
        def git(*args):
            p = sh(['git', '-C', str(repo), *args], env=env)
            check(p.returncode == 0, p.stderr)
        git('init')
        git('config', 'user.email', 'test@example.invalid')
        git('config', 'user.name', 'Test')
        git('add', '.')
        git('commit', '-m', 'initial')
        run('✓ 仓库状态（无未提交改动）', '· 仓库上游')
        git('branch', 'upstream-test')
        git('branch', '--set-upstream-to=upstream-test')
        run('领先 0 / 落后 0（按上次 fetch）')
        (repo / 'README.md').write_text('changed\n')
        run('⚠ 仓库状态：未提交改动 1 项')
        git('add', 'README.md')
        git('commit', '-m', 'ahead')
        run('领先 1 / 落后 0', '还没 push')
        branch = sh(['git', '-C', str(repo), 'branch', '--show-current'], env=env).stdout.strip()
        git('checkout', 'upstream-test')
        git('branch', '--set-upstream-to=' + branch)
        run('领先 0 / 落后 1', 'aiw sync')
        p = sh(['bash', str(repo / 'bin/aiw'), 'doctor'], env=dict(env, CODEX_BIN='/nonexistent/codex'))
        check(p.returncode == 1, '座位失效应仍返回 1')



def install_fresh():
    with tempfile.TemporaryDirectory() as t:
        home, env = fake_home(Path(t))
        p = sh(["bash", str(home / ".claude/aiw/install.sh")], env=env)
        check(p.returncode == 0, f"安装失败: {p.stdout}{p.stderr}")
        install_out = p.stdout
        md = (home / ".claude/CLAUDE.md").read_text()
        check("@~/.claude/aiw/CLAUDE.md" in md, "CLAUDE.md 未引入")
        st = json.loads((home / ".claude/settings.json").read_text())
        cmds = [h["command"] for g in st["hooks"]["UserPromptSubmit"] for h in g["hooks"]]
        check(any("$HOME/.claude/aiw/route.py" in c for c in cmds), f"钩子未写入或路径写死: {cmds}")
        aiw = home / ".local/bin/aiw"
        check(aiw.is_symlink() and os.access(aiw, os.X_OK), "aiw 软链不存在或不可执行")
        # 通过软链调用（真实使用方式）
        p = sh([str(aiw), "route", "帮我批量重命名所有文件"], env=env, cwd=home)
        check(p.returncode == 0 and '"bulk"' in p.stdout, f"通过软链调用 aiw route 失败: {p.stdout}{p.stderr}")
        # 不改 shell 配置：PATH 缺 ~/.local/bin 时只提示
        check(not (home / ".zshrc").exists(), ".zshrc 不应被创建或修改")
        check("不在 PATH" in install_out and "不改 shell 配置" in install_out, f"缺少 PATH 提示: {install_out}")


def install_minimal():
    with tempfile.TemporaryDirectory() as t:
        home, env = fake_home(Path(t))
        (home / ".claude/CLAUDE.md").write_text("# mine\n")
        (home / ".claude/settings.json").write_text('{"model": "opus"}\n')
        (home / ".codex").mkdir()
        (home / ".codex/AGENTS.md").write_text("# codex mine\n")
        inst = str(home / ".claude/aiw/install.sh")
        p = sh(["bash", inst, "--minimal"], env=env)
        check(p.returncode == 0 and "最小安装完成" in p.stdout and "不在 PATH" in p.stdout, p.stdout + p.stderr)
        check((home / ".claude/CLAUDE.md").read_text() == "# mine\n", "最小安装改了 CLAUDE.md")
        check((home / ".claude/settings.json").read_text() == '{"model": "opus"}\n', "最小安装改了 settings.json")
        check((home / ".codex/AGENTS.md").read_text() == "# codex mine\n" and not (home / ".codex/hooks.json").exists(), "最小安装改了 Codex 配置")
        check(not (home / ".zshrc").exists() and not list((home / ".claude").glob("*.bak-*")), "最小安装不应改 shell 配置或留备份")
        aiw = home / ".local/bin/aiw"
        check(aiw.is_symlink() and os.access(aiw, os.X_OK), "aiw 软链缺失")
        check((home / ".claude/aiw/state/minimal").exists(), "缺少最小安装标记")
        p = sh([str(aiw), "doctor"], env=env, cwd=home)
        check("最小安装" in p.stdout and "CLAUDE.md 引入" not in p.stdout and "Codex 派单钩子" not in p.stdout, p.stdout + p.stderr)
        # 坏 JSON 不影响最小安装：它不碰这些文件
        (home / ".claude/settings.json").write_text("{bad")
        p = sh(["bash", inst, "--minimal"], env=env)
        check(p.returncode == 0 and (home / ".claude/settings.json").read_text() == "{bad", "最小安装不应预检或改动 settings.json")
        (home / ".claude/settings.json").write_text('{"model": "opus"}\n')
        # 不带 --minimal 重跑即接入，标记清除
        p = sh(["bash", inst], env=env)
        check(p.returncode == 0 and not (home / ".claude/aiw/state/minimal").exists(), p.stdout + p.stderr)
        check("@~/.claude/aiw/CLAUDE.md" in (home / ".claude/CLAUDE.md").read_text() and (home / ".codex/hooks.json").exists(), "完整安装未接入")
        p = sh([str(aiw), "doctor"], env=env, cwd=home)
        check("最小安装" not in p.stdout and "CLAUDE.md 引入" in p.stdout, p.stdout + p.stderr)


def install_existing():
    with tempfile.TemporaryDirectory() as t:
        home, env = fake_home(Path(t))
        (home / ".claude/CLAUDE.md").write_text("# 我原来的全局约定\n- 用中文回答\n")
        (home / ".claude/settings.json").write_text(json.dumps({
            "model": "opus", "permissions": {"allow": ["Bash(git status)"]},
            "hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": "echo mine"}]}],
                      "Stop": [{"hooks": [{"type": "command", "command": "say done"}]}]}}))
        (home / ".codex").mkdir()
        (home / ".codex/AGENTS.md").write_text("# 我的 Codex 约定\n- 中文\n")
        (home / ".codex/hooks.json").write_text(json.dumps({"hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": "echo codex-mine"}]}],
                                                                    "Stop": [{"hooks": [{"type": "command", "command": "x"}]}]}}))
        inst = str(home / ".claude/aiw/install.sh")
        for _ in range(2):  # 跑两次验证幂等
            p = sh(["bash", inst], env=env)
            check(p.returncode == 0, f"安装失败: {p.stdout}{p.stderr}")
        md = (home / ".claude/CLAUDE.md").read_text()
        check(md.startswith("# 我原来的全局约定") and md.count("@~/.claude/aiw/CLAUDE.md") == 1, f"CLAUDE.md 原内容被改或重复追加:\n{md}")
        st = json.loads((home / ".claude/settings.json").read_text())
        check(st["model"] == "opus" and st["permissions"]["allow"] == ["Bash(git status)"], "原 settings 被改")
        cmds = [h["command"] for g in st["hooks"]["UserPromptSubmit"] for h in g["hooks"]]
        check("echo mine" in cmds and sum("aiw/route.py" in c for c in cmds) == 1, f"钩子合并不对: {cmds}")
        check(st["hooks"]["Stop"][0]["hooks"][0]["command"] == "say done", "其它钩子被改")
        baks = list((home / ".claude").glob("*.bak-*"))
        check(len(baks) >= 2, f"应生成 CLAUDE.md 与 settings.json 备份: {baks}")
        check(not (home / ".zshrc").exists(), ".zshrc 不应被创建或修改")
        # Codex 接入：原 AGENTS.md 保留、标记块只有一份；hooks.json 原有钩子保留、派单钩子只加一次
        ag = (home / ".codex/AGENTS.md").read_text()
        check(ag.startswith("# 我的 Codex 约定") and ag.count("aiw:begin") == 1 and "AIW_MAIN=codex" in ag, f"AGENTS.md 合并不对:\n{ag[:400]}")
        hk = json.loads((home / ".codex/hooks.json").read_text())
        cmds = [h["command"] for g in hk["hooks"]["UserPromptSubmit"] for h in g["hooks"]]
        check("echo codex-mine" in cmds and sum("aiw/route.py" in c for c in cmds) == 1 and hk["hooks"]["Stop"], f"Codex 钩子合并不对: {hk}")
        # 改了 AGENTS.harness.md 再装：标记块被刷新，块外内容不动
        src = home / ".claude/aiw/AGENTS.harness.md"
        src.write_text(src.read_text() + "\nNEW_LINE_X\n")
        sh(["bash", inst], env=env)
        ag = (home / ".codex/AGENTS.md").read_text()
        check(ag.count("aiw:begin") == 1 and "NEW_LINE_X" in ag and ag.startswith("# 我的 Codex 约定"), "标记块应原地刷新")
        # 坏 settings.json：拒绝改动，不覆盖
        (home / ".claude/settings.json").write_text("{oops")
        p = sh(["bash", inst], env=env)
        check(p.returncode != 0 and (home / ".claude/settings.json").read_text() == "{oops", "坏 JSON 时应拒绝并保持原样")



def install_personal_rules():
    import contextlib
    import io
    import doctor_config as d
    with tempfile.TemporaryDirectory() as td:
        home, env = fake_home(Path(td))
        env.pop('AIW_NO_CODEX', None)
        (home / '.codex').mkdir(exist_ok=True)
        repo = home / '.claude/aiw'
        md = home / '.claude/CLAUDE.md'
        ag = home / '.codex/AGENTS.md'
        folder = home / '.config/aiw'
        folder.mkdir(parents=True)
        cp, ap = folder / 'CLAUDE.personal.md', folder / 'AGENTS.personal.md'
        public = (repo / 'AGENTS.harness.md').read_text().strip()
        md.write_text('before\n' + d.IMPORT_LINE + '\nafter\n')
        ag.write_text('outside\n')

        installed = False

        def install():
            nonlocal installed
            if installed:
                results = [
                    install_phase(repo, 2, [md, cp, "test"], env),
                    install_phase(repo, 4, [home / ".codex", repo / "AGENTS.harness.md", "test"], env),
                ]
                check(all(r.returncode == 0 for r in results), str(results))
                return "".join(r.stdout for r in results)
            installed = True
            r = sh(['bash', str(repo / 'install.sh')], env=env)
            check(r.returncode == 0, r.stdout + r.stderr)
            return r.stdout

        def doctor(*expected):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                d.personal_import(home)
                d.agents(home, repo)
            for text in expected:
                check(text in out.getvalue(), out.getvalue())

        def stable():
            before = (md.read_bytes(), ag.read_bytes())
            backups = sorted(home.rglob('*.bak-*'))
            install()
            check(before == (md.read_bytes(), ag.read_bytes()), '个人规则安装不幂等')
            check(backups == sorted(home.rglob('*.bak-*')), '幂等安装产生额外备份')

        install()
        check(md.read_text() == 'before\n' + d.IMPORT_LINE + '\nafter\n', '无个人文件时改了 Claude 内容')
        check(f'{d.BEGIN}\n{public}\n{d.END}\n' in ag.read_text(), '无个人文件时 Codex 块错误')
        doctor('无个人规则（可选）', '✓ Codex AGENTS 标记块')
        stable()
        for symlink in (False, True):
            for path, body in ((cp, '  Claude private rule\n'), (ap, '\n  Codex private rule  \n')):
                path.unlink(missing_ok=True)
                if symlink:
                    target = folder / (path.name + '.source')
                    target.write_text(body)
                    path.symlink_to(target)
                else:
                    path.write_text(body)
            doctor('⚠ Claude 个人规则引入', '重跑 install.sh', '⚠ Codex AGENTS 标记块')
            # Deduplicate imports without touching surrounding content.
            md.write_text(d.PERSONAL_LINE + '\nbefore\n' + d.IMPORT_LINE + '\nafter\n' + d.PERSONAL_LINE + '\n')
            old = md.read_text()
            install()
            check(md.read_text() == 'before\n' + d.IMPORT_LINE + '\n' + d.PERSONAL_LINE + '\nafter\n', '个人引入顺序、去重或块外内容错误')
            check(any(p.read_text() == old for p in md.parent.glob('CLAUDE.md.bak-*')), '个人引入修改未备份')
            check(ag.read_text().startswith('outside\n'), 'Codex 块外内容被改')
            check(f'{d.BEGIN}\n{public}\n\nCodex private rule\n{d.END}\n' in ag.read_text(), '个人内容未 strip 或加载顺序错误')
            doctor('✓ Claude 个人规则引入', '✓ Codex AGENTS 标记块')
            stable()
            # Missing files preserve an existing Claude import but refresh Codex.
            cp.unlink()
            ap.unlink()
            install()
            check(d.PERSONAL_LINE in md.read_text().splitlines(), '个人文件移除后不应删除 Claude 引入')
            doctor('无个人规则（可选）', '✓ Codex AGENTS 标记块')
            md.write_text('before\n' + d.IMPORT_LINE + '\nafter\n')
        for path in (cp, ap):
            path.symlink_to(folder / 'missing')
        output = install()
        check(output.count('是断链，视为无个人规则') == 2, output)
        check(d.PERSONAL_LINE not in md.read_text(), '断链不应引入')
        doctor('无个人规则（可选）', '✓ Codex AGENTS 标记块')
        stable()



def install_claude_hook_mode():
    with tempfile.TemporaryDirectory() as t:
        home, env = fake_home(Path(t))
        inst = ["bash", str(home / ".claude/aiw/install.sh")]
        settings = home / ".claude/settings.json"
        (home / ".codex").mkdir()
        route = {"type": "command", "command": 'python3 "$HOME/.claude/aiw/route.py" --hook'}
        other = {"type": "command", "command": "echo other", "timeout": 23}
        expected = {"model": "opus", "permissions": {"allow": ["Read"]}, "hooks": {
            "UserPromptSubmit": [{"matcher": "*", "hooks": [other]}, {"hooks": [other]}],
            "PreToolUse": [{"matcher": "Bash", "hooks": [other]}]}}
        data = json.loads(json.dumps(expected))
        expected["autoCompactWindow"] = 400000
        data["hooks"]["UserPromptSubmit"][0]["hooks"].extend([route, route])
        data["hooks"]["UserPromptSubmit"].insert(1, {"hooks": [route]})
        original = json.dumps(data, ensure_ascii=False).encode()
        settings.write_bytes(original)

        installed = False

        def run(*args, shell=False):
            nonlocal installed
            if installed and not shell:
                mode = 'plugin' if args == ('--claude-hook=plugin',) else 'settings'
                r = install_phase(home / '.claude/aiw', 3, [settings, 'test', mode], env)
            else:
                installed = True
                r = sh(inst + list(args), env=env)
            check(r.returncode == 0, f"安装失败: {r.stdout}{r.stderr}")
            return r

        run("--claude-hook=plugin")
        check(json.loads(settings.read_text()) == expected, "plugin 删除范围不正确")
        backups = list(settings.parent.glob("settings.json.bak-*"))
        check(len(backups) == 1 and backups[0].read_bytes() == original, "原文件备份不正确")
        # 固定旧备份名，避免同秒覆盖掩盖重复备份错误。
        backups[0].rename(settings.with_name("settings.json.bak-sentinel"))
        before = settings.read_bytes()
        r = run("--claude-hook=plugin")
        check("✓ 派单钩子由插件管理，settings.json 无需改动" in r.stdout, "缺少无改动说明")
        check(settings.read_bytes() == before and len(list(settings.parent.glob("settings.json.bak-*"))) == 1,
              "重复 plugin 改写了文件或产生新备份")
        codex_hooks = (home / ".codex/hooks.json").read_bytes()
        codex_agents = (home / ".codex/AGENTS.md").read_bytes()
        check("aiw/route.py" in codex_hooks.decode() and "aiw:begin" in codex_agents.decode(),
              "plugin 影响了 Codex 安装")
        for args in ((), ("--claude-hook=settings",)):
            run(*args, shell=args == ())
            hooks = json.loads(settings.read_text())["hooks"]["UserPromptSubmit"]
            check(sum("aiw/route.py" in h["command"] for g in hooks for h in g["hooks"]) == 1,
                  "默认恢复或显式 settings 失败")
        check((home / ".codex/hooks.json").read_bytes() == codex_hooks
              and (home / ".codex/AGENTS.md").read_bytes() == codex_agents, "模式切换影响 Codex")
        settings.write_text(json.dumps({"hooks": {"UserPromptSubmit": [{"hooks": [route]}]}}))
        run("--claude-hook=plugin", shell=True)
        check(json.loads(settings.read_text()) == {"hooks": {}, "autoCompactWindow": 400000}, "空组或空事件键未删除")
        for raw in (b'{ "model": "opus" }\n', b'{broken'):
            settings.write_bytes(raw)
            for args in (("--claude-hook=invalid",), ("--unknown",), ("--claude-hook", "plugin"),
                         ("--claude-hook=plugin", "--unknown")):
                before_files = {str(p.relative_to(home)): p.read_bytes() for p in home.rglob("*")
                                if p.is_file() and not p.is_symlink()}
                r = sh(inst + list(args), env=dict(env, AIW_PYTHON="/missing/python"))
                check(r.returncode == 64 and "用法：" in r.stderr, "非法参数没有在预检前退出 64")
                after_files = {str(p.relative_to(home)): p.read_bytes() for p in home.rglob("*")
                               if p.is_file() and not p.is_symlink()}
                check(before_files == after_files, "非法参数修改了文件")
        settings.unlink()
        run("--claude-hook=plugin")
        check(json.loads(settings.read_text()) == {"autoCompactWindow": 400000}, "plugin 未新建自动压缩兜底")


def install_autocompact():
    with tempfile.TemporaryDirectory() as td:
        home, env = fake_home(Path(td))
        repo = home / '.claude/aiw'
        settings = home / '.claude/settings.json'
        env.pop('AIW_NO_AUTOCOMPACT', None)
        env.pop('CLAUDE_CODE_AUTO_COMPACT_WINDOW', None)
        for mode in ('settings', 'plugin'):
            # Use a real installation for both modes, then the same Python block
            # with a different stamp so a duplicate backup cannot hide in the same second.
            original = b'{ "model": "opus" }\n'
            settings.write_bytes(original)
            result = sh(['bash', str(repo / 'install.sh'), '--claude-hook=' + mode], env=env)
            check(result.returncode == 0, result.stdout + result.stderr)
            check(json.loads(settings.read_text())['autoCompactWindow'] == 400000, mode)
            check('自动压缩兜底：写入' in result.stdout, result.stdout)
            backups = list(settings.parent.glob('settings.json.bak-*'))
            check(backups and any(p.read_bytes() == original for p in backups), 'missing original backup')
            before = settings.read_bytes()
            result = install_phase(repo, 3, [settings, 'repeat-' + mode, mode], env)
            check(result.returncode == 0 and '已有 autoCompactWindow 值，保留' in result.stdout,
                  result.stdout + result.stderr)
            check(settings.read_bytes() == before
                  and set(settings.parent.glob('settings.json.bak-*')) == set(backups),
                  '重复安装改动文件或产生新备份')

            for value in (123, None, False, 0, '', [], {}):
                settings.write_text(json.dumps({'autoCompactWindow': value}))
                result = install_phase(repo, 3, [settings, 'existing', mode], env)
                check(result.returncode == 0 and '已有 autoCompactWindow 值，保留' in result.stdout,
                      result.stdout + result.stderr)
                check(json.loads(settings.read_text())['autoCompactWindow'] == value,
                      f'{mode}: existing value changed: {value!r}')

            for overrides in ({'AIW_NO_AUTOCOMPACT': '1'},
                              {'CLAUDE_CODE_AUTO_COMPACT_WINDOW': '500000'},
                              {'CLAUDE_CODE_AUTO_COMPACT_WINDOW': ''}):
                settings.write_bytes(original)
                backups = set(settings.parent.glob('settings.json.bak-*'))
                result = install_phase(repo, 3, [settings, 'skip', mode], dict(env, **overrides))
                check(result.returncode == 0 and '自动压缩兜底：跳过' in result.stdout,
                      result.stdout + result.stderr)
                check('autoCompactWindow' not in json.loads(settings.read_text()), str(overrides))
                if mode == 'plugin':
                    check(settings.read_bytes() == original
                          and set(settings.parent.glob('settings.json.bak-*')) == backups,
                          '跳过时 plugin 改动文件或产生备份')
                    settings.unlink()
                    result = install_phase(repo, 3, [settings, 'skip-absent', mode], dict(env, **overrides))
                    check(result.returncode == 0 and not settings.exists(), '跳过时 plugin 创建了文件')

        settings.unlink(missing_ok=True)
        result = sh(['bash', str(repo / 'install.sh'), '--minimal'], env=env)
        check(result.returncode == 0 and not settings.exists(), result.stdout + result.stderr)



def install_preflight():
    with tempfile.TemporaryDirectory() as td:
        home, env = fake_home(Path(td)); inst = ['bash', str(home / '.claude/aiw/install.sh')]
        settings = home / '.claude/settings.json'; md = home / '.claude/CLAUDE.md'
        for value in ([], {'hooks': []}, {'hooks': {'UserPromptSubmit': {}}},
                      {'hooks': {'UserPromptSubmit': [1]}}, {'hooks': {'UserPromptSubmit': [{'hooks': {}}]}}):
            settings.write_text(json.dumps(value))
            r = sh(inst, env=env)
            check(r.returncode == 1 and str(settings) in r.stderr and not md.exists(), f'预检改了文件: {r.stderr}')
        md.write_text('保留'); settings.write_text('{"hooks": []}')
        r = sh(inst, env=env); check(r.returncode == 1 and md.read_text() == '保留', '预检覆盖原文件')
        settings.write_text('{}'); (home / '.codex').mkdir(); bad = home / '.codex/hooks.json'; bad.write_text('{bad')
        r = sh(inst, env=dict(env, AIW_NO_CODEX='1'))
        check(r.returncode == 0 and bad.read_text() == '{bad', f'跳过 Codex 失败: {r.stderr}')



def doctor_fingerprint():
    import hashlib
    import contextlib
    import io
    from unittest.mock import patch
    import doctor_config as d
    import ai_worker as w
    import seats_config as sc
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); repo = t / 'repo'; repo.mkdir()
        def git(*args):
            r = sh(['git', '-C', str(repo), *args])
            check(r.returncode == 0, r.stderr)
            return r.stdout.strip()
        git_template(repo, {'tracked': 'one'})
        head = git('rev-parse', '--short', 'HEAD')
        with d.memoized_checks(), patch.object(d.subprocess, 'run', wraps=d.subprocess.run) as queries:
            check(d.git_fingerprint(repo) == (str(repo.resolve()), head), '初始指纹错误')
            count = queries.call_count
            check(d.git_fingerprint(repo) == (str(repo.resolve()), head) and queries.call_count == count,
                  '同次 doctor 重复 Git 查询未复用')
        (repo / 'tracked').write_text('changed')
        with d.memoized_checks():
            check(d.git_fingerprint(repo) == (str(repo.resolve()), head + '*'), 'Git 查询缓存跨 doctor 运行泄漏')
        (repo / 'tracked').write_text('one')
        config = sc.load_seats(REPO)
        with patch.dict(os.environ, HOME=str(t), AIW_CONFIG=str(t / 'absent'), AIW_ROUTER_RULES=str(t / 'absent-route'), AGENT_USE_HOME=str(repo), AGENT_USE_CONFIG=str(t / 'missing-registry')):
            data = d.fingerprint(repo, config)
            check(data['harness'] == data['agent_use'] == head, str(data))
            default_home = t / '.local/share/agent-use'
            default_home.parent.mkdir(parents=True)
            default_home.symlink_to(repo, target_is_directory=True)
            os.environ.pop('AGENT_USE_HOME')
            check(d.fingerprint(repo, config)['agent_use'] == head, 'default agent-use HEAD missing')
            os.environ['AGENT_USE_HOME'] = str(repo)
            check(data['registry'] == {'source': 'env', 'sha256': '-', 'git': '-'}, str(data))
            check(set(data['local'].values()) == {'-'} and data['local_git'] == ['-'], str(data))
            registry = repo / 'registry.json'; registry.write_text('{"providers":{"generic":{}}}')
            (repo / 'local.json').write_text('{}')
            (t / 'local-link').symlink_to(repo / 'local.json')
            os.environ.update(AGENT_USE_CONFIG=str(registry), AIW_CONFIG=str(t / 'local-link'), AIW_ROUTER_RULES=str(repo / 'local.json'))
            personal = t / '.config/aiw'
            personal.mkdir(parents=True)
            for name in ('CLAUDE.personal.md', 'AGENTS.personal.md'):
                (personal / name).symlink_to(repo / 'local.json')
            data = d.fingerprint(repo, config)
            check(len(set(data['local'].values())) == 1 and data['local']['claude'] != '-', str(data))
            check(data['effective_hash'] == sc.effective_hash(config), str(data))
            check(data['harness'] == data['agent_use'] == data['registry']['git'] == head + '*', str(data))
            check(data['local_git'] == [head + '*'] and len(data['local']['seats']) == 12, str(data))
            line = d.fingerprint_line(data)
            check(str(t) not in line and '\n' not in line and len(data['registry']['sha256']) == 12, line)
            os.environ.pop('AGENT_USE_CONFIG')
            check(d.fingerprint(repo, config)['registry']['source'] == 'repo', 'repo source')
            user = t / '.config/agent-use/dispatcher.json'; user.parent.mkdir(parents=True); user.write_text('{}')
            check(d.fingerprint(repo, config)['registry']['source'] == 'user', 'user source')
            xdg = t / 'xdg/agent-use/dispatcher.json'
            xdg.parent.mkdir(parents=True); xdg.write_text('{"xdg":true}')
            os.environ['XDG_CONFIG_HOME'] = str(xdg.parent.parent)
            data = d.fingerprint(repo, config)['registry']
            check(data['source'] == 'user' and data['sha256'] == hashlib.sha256(xdg.read_bytes()).hexdigest()[:12], str(data))
            xdg.unlink(); xdg.symlink_to(t / 'absent')
            check(d.fingerprint(repo, config)['registry'] == {'source': 'user', 'sha256': '-', 'git': '-'}, 'broken XDG source')
        seats = {'seats': {'generic': {'kind': 'agent_use', 'provider': 'generic'},
                           'disabled': {'kind': 'agent_use', 'provider': 'absent', 'disabled_by_user': True}}}
        for content, expected in (('{"providers":{"generic":{}}}', ''), ('{"providers":{}}', '不在生效注册表'), ('{"providers":{"generic":{"enabled":false}}}', 'enabled=false'), ('{', '不可读')):
            registry.write_text(content)
            with contextlib.redirect_stdout(io.StringIO()) as capture:
                d.registry_check(seats, lambda: registry)
            output = capture.getvalue()
            check((expected in output if expected else output == '') and 'disabled' not in output, output)
        local = t / 'doctor-local.json'
        write_legacy_local(local, {})
        env = dict(os.environ, HOME=str(t), AIW_CONFIG=str(local),
                   AGENT_USE_CONFIG=str(registry), AGENT_USE_HOME=str(fake_agent_use(t)))
        for content in ('{"providers":{}}', '{"providers":{"doubao":{"enabled":false}}}', '{'):
            registry.write_text(content)
            r = inprocess_sh([sys.executable, str(REPO / 'ai_worker.py'), 'doctor'], env=env)
            check(r.returncode == 0 and '⚠ agent-use' in r.stdout, r.stdout + r.stderr)
        env = dict(os.environ, HOME=str(t), AIW_CONFIG=str(t / 'broken'), AGENT_USE_CONFIG=str(t / 'absent'))
        r = inprocess_sh([sys.executable, str(REPO / 'ai_worker.py'), 'doctor', '--fingerprint'], env=env)
        check(r.returncode == 0 and len(r.stdout.splitlines()) == 1 and r.stdout.startswith('指纹：') and not r.stderr, r.stdout + r.stderr)
        check(str(t) not in r.stdout and str(REPO) not in r.stdout, r.stdout)



def doctor_readonly():
    import hashlib
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        env = free_guard_fixture(root)
        source = codex_home_fixture(root)
        env.update(HOME=str(root), CODEX_HOME=str(source))
        state = root / 'state'
        state.mkdir()
        entry = {'id': 'pending-disable', 'since': 'now', 'host': 'test',
                 'reason': 'paid', 'evidence': 'HTTP 402', 'notified': False}
        (state / 'disabled.json').write_text(json.dumps({'qoder:free-model-q': entry}))
        (state / 'notify-pending.jsonl').write_text(json.dumps({'id': 'pending-notice', 'message': 'test'}) + '\n')
        (state / 'breaker.json').write_text(json.dumps({'codex': {'until': 9999999999, 'reason': 'quota'}}))
        (state / '.state.lock').write_text('keep lock contents')
        (state / 'codex-home').mkdir()
        (state / 'codex-home/config.toml').write_text('keep existing config')
        (state / 'inflight').mkdir()
        (state / 'inflight/keep.json').write_text('{}')

        def snapshot():
            return {str(p.relative_to(state)): (p.is_dir(), p.stat().st_mtime_ns,
                    None if p.is_dir() else hashlib.sha256(p.read_bytes()).hexdigest())
                    for p in [state, *state.rglob('*')]}

        before = snapshot()
        for args in (['doctor'], ['doctor', '--fingerprint'], ['doctor', '--require-no-tools'], ['doctor', '--ro']):
            r = inprocess_sh([sys.executable, str(REPO / 'ai_worker.py'), *args], env=env)
            check(r.returncode == (64 if '--ro' in args else 0), r.stdout + r.stderr)
            check(snapshot() == before, 'doctor 修改了 STATE 路径、内容或 mtime')
            check(not (root / 'discord').exists(), 'doctor 调用了 discord-use')
            if args == ['doctor']:
                check('待发通知：2' in r.stdout and '由 aiw freecheck 或下一次通知补发' in r.stdout, r.stdout)
                check('可生成' in r.stdout and '熔断: quota' in r.stdout and '⛔ qoder' in r.stdout, r.stdout)
        shutil.rmtree(state)
        for args in (['doctor'], ['doctor', '--fingerprint'], ['doctor', '--require-no-tools'], ['doctor', '--ro']):
            r = inprocess_sh([sys.executable, str(REPO / 'ai_worker.py'), *args], env=env)
            check(r.returncode == (64 if '--ro' in args else 0) and not state.exists(), 'doctor 创建了不存在的 STATE')
            check(not (root / 'discord').exists(), 'doctor 调用了 discord-use')



def ecosystem_fixture(t):
    home, env = fake_home(t)
    env.pop('AIW_CONFIG', None)
    env.pop('AGENT_USE_CONFIG', None)
    core = git_template(t / 'core-origin', {'scripts/run_agent.py': '# fixture core\n'})
    adapters = git_template(t / 'adapters-origin', {
        'config/dispatcher.json': json.dumps({'providers': {'opencode': {'enabled': True}}}),
        'scripts/install.sh': '''#!/usr/bin/env bash
set -eu
repo="$(cd "$(dirname "$0")/.." && pwd)"
p="${XDG_CONFIG_HOME:-$HOME/.config}/agent-use/dispatcher.json"
mkdir -p "$(dirname "$p")"
if [ -e "$p" ] || [ -L "$p" ]; then
  if [ "$(readlink "$p")" != "$repo/config/dispatcher.json" ]; then
    echo 'fixture registry conflict: refusing replacement' >&2
    exit 1
  fi
  echo 'Already installed'
  exit 0
fi
ln -sfn "$repo/config/dispatcher.json" "$p"
echo installed >> "$HOME/adapter-installs"
'''})
    env.update(AGENT_USE_HOME='~/ecosystem/agent-use', AIW_ADAPTERS_HOME='~/ecosystem/adapters',
               AIW_AGENT_USE_REPO=str(core), AIW_ADAPTERS_REPO=str(adapters),
               XDG_CONFIG_HOME=str(home / '.config'), AIW_NO_CODEX='1')
    return home, env, core, adapters


def ecosystem_install(home, env, *args):
    result = sh(['bash', str(home / '.claude/aiw/install.sh'), *args], env=env)
    check(result.returncode == 0, result.stdout + result.stderr)
    return result.stdout + result.stderr


def install_agent_use_fresh_repeat():
    with tempfile.TemporaryDirectory() as td:
        home, env, core, adapters = ecosystem_fixture(Path(td))
        out = ecosystem_install(home, env, '--claude-hook=plugin', '--with-agent-use')
        check((home / 'ecosystem/agent-use/.git').is_dir() and
              (home / 'ecosystem/adapters/.git').is_dir(), '两个仓库未克隆')
        check('已克隆' in out and (home / 'adapter-installs').read_text() == 'installed\n', out)
        registry = home / '.config/agent-use/dispatcher.json'
        check(registry.is_symlink(), '未运行适配器安装')
        config = home / '.config/aiw/seats.local.json'
        check(not config.exists(), '安装创建了本地座位配置')
        out = ecosystem_install(home, env, '--with-agent-use', '--claude-hook=settings')
        check(out.count('已更新（pull --ff-only）') == 2, out)
        # Default sibling path, preserving unrelated values.
        env.pop('AIW_ADAPTERS_HOME')
        registry.unlink()
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(json.dumps({'custom': [1, 2], 'seats': {'codex': {'label': 'custom'}},
                                     'fallback': {'codex': []}}))
        ecosystem_install(home, env, '--with-agent-use')
        data = json.loads(config.read_text())
        check((home / 'ecosystem/agent-use-adapters/.git').is_dir(), '默认同级目录错误')
        check(data['custom'] == [1, 2] and data['seats']['codex'] == {'label': 'custom'}
              and data['fallback']['codex'] == [], '未保留其它键')


def install_agent_use_preserves_config():
    with tempfile.TemporaryDirectory() as td:
        home, env, _, _ = ecosystem_fixture(Path(td))
        config = home / 'custom/config.json'
        config.parent.mkdir()
        original = {'extra': {'keep': [1]}, 'seats': {'opencode': {'kind': 'cli', 'bin': 'custom'}},
                    'fallback': {'opencode': ['codex']}}
        config.write_text(json.dumps(original))
        env['AIW_CONFIG'] = '~/custom/config.json'
        before = config.read_bytes()
        ecosystem_install(home, env, '--with-agent-use')
        check(config.read_bytes() == before, '已有 opencode / 其它键被改动')


def install_agent_use_dangling_symlink_config():
    with tempfile.TemporaryDirectory() as td:
        home, env, _, _ = ecosystem_fixture(Path(td))
        config = home / '.config/aiw/seats.local.json'
        config.parent.mkdir(parents=True)
        target = home / 'private-config/missing.json'
        config.symlink_to(target)
        link = os.readlink(config)
        ecosystem_install(home, env, '--with-agent-use')
        check(config.is_symlink() and os.readlink(config) == link, '断链被替换')
        check(not target.exists() and not target.parent.exists(), '创建了断链目标')
        check(not list(config.parent.glob('*.bak-*'))
              and not list(config.parent.glob('*.tmp-aiw')), '断链产生了备份或临时文件')


def install_agent_use_registry_conflict():
    with tempfile.TemporaryDirectory() as td:
        home, env, _, _ = ecosystem_fixture(Path(td))
        registry = home / '.config/agent-use/dispatcher.json'
        registry.parent.mkdir(parents=True)
        registry.write_text('{"custom": true}')
        config = home / '.config/aiw/seats.local.json'
        config.parent.mkdir(parents=True)
        config.write_text('{"extra": [1], "seats": {"custom": {"kind": "cli"}}, "fallback": {}}')
        before = config.read_bytes()
        out = ecosystem_install(home, env, '--with-agent-use')
        check('fixture registry conflict: refusing replacement' in out, out)
        check(registry.read_text() == '{"custom": true}', '注册表被覆盖')
        check(config.read_bytes() == before, '冲突时新增了座位 / fallback 或修改了配置')
        # Non-repository directories must not run their installer.
        shutil.rmtree(home / 'ecosystem/adapters/.git')
        out = ecosystem_install(home, env, '--with-agent-use')
        check('目录不是 git 仓库' in out and 'fixture registry conflict' not in out, out)
        check(config.read_bytes() == before, '适配器仓库无效时修改了座位')
        shutil.rmtree(home / 'ecosystem/adapters')
        env['AIW_ADAPTERS_REPO'] = str(home / 'missing-adapters-origin')
        config.unlink()
        out = ecosystem_install(home, env, '--with-agent-use')
        check('克隆失败' in out and not config.exists(), out)


def install_doctor_agent_use_hint():
    with tempfile.TemporaryDirectory() as td:
        home, env, _, _ = ecosystem_fixture(Path(td))
        out = ecosystem_install(home, env)
        install_hint = '想把更多模型接进来'
        check(install_hint in out, out)
        hint = '想把更多模型（如免费的 OpenCode）接进来'
        repo = home / '.claude/aiw'
        command = ['bash', str(repo / 'bin/aiw'), 'doctor']
        missing = sh(command, env=env)
        check(missing.returncode == 0 and missing.stdout.count(hint) == 1, missing.stdout + missing.stderr)
        script = home / 'ecosystem/agent-use/scripts/run_agent.py'
        script.parent.mkdir(parents=True)
        script.write_text('# core')
        present = sh(command, env=env)
        check(present.returncode == missing.returncode and hint not in present.stdout, present.stdout)
        check(install_hint not in ecosystem_install(home, env), '核心存在仍建议安装')
        script.unlink()
        config = home / '.config/aiw/seats.local.json'
        config.parent.mkdir(parents=True)
        config.write_text(json.dumps({'seats': {'opencode': {'kind': 'agent_use', 'provider': 'opencode'}}}))
        enabled = sh(command, env=env)
        check(enabled.returncode == 1 and 'AGENT_USE_HOME' in enabled.stdout
              and hint not in enabled.stdout, enabled.stdout)
        config.write_text(json.dumps({'seats': {'opencode': {'kind': 'agent_use', 'provider': 'opencode',
                                                           'disabled_by_user': 'test'}}}))
        disabled = sh(command, env=env)
        check(disabled.returncode == 0 and hint in disabled.stdout, disabled.stdout)


def repository_git_env_shell_guard():
    import ast
    import re
    tree = ast.parse((REPO / 'gitenv.py').read_text())
    function = next(node for node in tree.body
                    if isinstance(node, ast.FunctionDef) and node.name == 'repository_git_env')
    excluded = next(node.value for node in function.body if isinstance(node, ast.Assign)
                    and any(isinstance(target, ast.Name) and target.id == 'excluded'
                            for target in node.targets))
    python_vars = ast.literal_eval(excluded)
    shell = (REPO / 'agent_use_setup.sh').read_text()
    wrapper = re.search(r'^aiw_git\(\) \{\n(.*?)^\}', shell, re.M | re.S)
    check(wrapper is not None, '缺少 aiw_git 包装函数')
    shell_vars = set(re.findall(r'-u\s+(GIT_[A-Z_]+)\b', wrapper.group(1)))
    check(python_vars == shell_vars,
          f'Git 环境变量列表不一致: Python={sorted(python_vars)}, shell={sorted(shell_vars)}')


def install_sync_foreign_git_env():
    from support import git_history
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        home, clean_env, core, adapters = ecosystem_fixture(t)
        caller_origin = git_template(t / 'caller-origin', {'marker': 'caller initial'})
        caller = t / 'caller'
        result = sh(['git', 'clone', str(caller_origin), str(caller)], env=clean_env)
        check(result.returncode == 0, result.stderr)
        git_history(caller_origin, [('next', {'marker': 'caller next'},
                                    '2026-10-13T01:00:00+07:00')], append=True)
        (caller / 'private-untracked').write_text('preserve caller data')

        def caller_snapshot():
            return {str(path.relative_to(caller)): path.read_bytes()
                    for path in caller.rglob('*') if path.is_file()}

        before = caller_snapshot()
        env = dict(clean_env, GIT_DIR=str(caller / '.git'), GIT_WORK_TREE=str(caller))
        # Fresh installation exercises clone with a foreign repository environment.
        out = ecosystem_install(home, env, '--with-agent-use')
        check(out.count('已克隆') == 2, out)
        check(caller_snapshot() == before, '安装克隆修改了调用方仓库')
        check((home / 'adapter-installs').read_text() == 'installed\n', '未识别适配器仓库')
        checkouts = (home / 'ecosystem/agent-use', home / 'ecosystem/adapters')
        for upstream in (core, adapters):
            git_history(upstream, [('install update', {'marker': 'install updated'},
                                   '2026-10-13T02:00:00+07:00')], append=True)
        out = ecosystem_install(home, env, '--with-agent-use')
        check(out.count('已更新（pull --ff-only）') == 2, out)
        for checkout in checkouts:
            check((checkout / 'marker').read_text() == 'install updated', '安装未更新正确仓库')
        check(caller_snapshot() == before, '重复安装修改了调用方仓库')

        repo = home / '.claude/aiw'
        origin = git_template(t / 'aiw-origin', {'marker': 'initial'})
        result = sh(['git', 'clone', str(origin), str(t / 'aiw-clone')], env=clean_env)
        check(result.returncode == 0, result.stderr)
        shutil.copytree(t / 'aiw-clone/.git', repo / '.git')
        for upstream in (origin, core, adapters):
            git_history(upstream, [('sync update', {'marker': 'sync updated'},
                                   '2026-10-13T03:00:00+07:00')], append=True)
        command = ['bash', str(repo / 'bin/aiw'), 'sync']
        result = sh(command, env=env)
        check(result.returncode == 0 and result.stdout.count('已更新（pull --ff-only）') == 2,
              result.stdout + result.stderr)
        for checkout in (repo, *checkouts):
            check((checkout / 'marker').read_text() == 'sync updated', 'sync 未更新正确仓库')
        check(caller_snapshot() == before, 'sync 修改了调用方仓库')
        # A broken aiw upstream must still stop sync with Git's nonzero status.
        (repo / '.git/config').write_text('[core]\n repositoryformatversion = 0\n')
        expected = sh(['git', '-C', str(repo), 'pull', '--ff-only'], env=clean_env)
        result = sh(command, env=env)
        check(result.returncode == expected.returncode != 0 and 'agent-use 已更新' not in result.stdout,
              result.stdout + result.stderr)
        check(caller_snapshot() == before, '失败的 sync 修改了调用方仓库')


def sync_agent_use_repos():
    from support import git_history
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        home, env, core, adapters = ecosystem_fixture(t)
        ecosystem_install(home, env, '--with-agent-use')
        repo = home / '.claude/aiw'
        origin = git_template(t / 'aiw-origin', {'marker': 'initial'})
        check(sh(['git', 'clone', str(origin), str(t / 'clone')], env=env).returncode == 0, 'aiw fixture clone')
        shutil.copytree(t / 'clone/.git', repo / '.git')
        for upstream in (origin, core, adapters):
            git_history(upstream, [('next', {'marker': 'updated'}, '2026-10-13T01:00:00+07:00')], append=True)
        result = sh(['bash', str(repo / 'bin/aiw'), 'sync'], env=env)
        check(result.returncode == 0, result.stdout + result.stderr)
        for checkout in (repo, home / 'ecosystem/agent-use', home / 'ecosystem/adapters'):
            check((checkout / 'marker').read_text() == 'updated', 'sync 未更新 ' + str(checkout))
        # Optional pull failures warn; aiw pull failures retain Git's exit status.
        (home / 'ecosystem/adapters/.git/config').write_text('[core]\n repositoryformatversion = 0\n')
        result = sh(['bash', str(repo / 'bin/aiw'), 'sync'], env=env)
        check(result.returncode == 0 and '⚠ agent-use-adapters 更新失败' in result.stdout, result.stdout + result.stderr)
        (repo / '.git/config').write_text('[core]\n repositoryformatversion = 0\n')
        expected = sh(['git', '-C', str(repo), 'pull', '--ff-only'], env=env)
        result = sh(['bash', str(repo / 'bin/aiw'), 'sync'], env=env)
        check(result.returncode == expected.returncode != 0 and 'agent-use 已更新' not in result.stdout,
              result.stdout + result.stderr)


def install_target():
    for kind in ('directory', 'symlink', 'dangling'):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            home, env = fake_home(root)
            repo = home / '.claude/aiw'
            source = home / '.claude/source'
            repo.rename(source)
            other = root / 'other'
            if kind != 'dangling':
                other.mkdir()
            if kind == 'directory':
                repo.mkdir()
                (repo / 'sentinel').write_text('untouched')
            else:
                repo.symlink_to(other)
            before = sorted(str(p.relative_to(root)) for p in root.rglob('*'))
            result = sh(['bash', str(source / 'install.sh')], env=env)
            check(result.returncode != 0 and '~/.claude/aiw 指向另一份 checkout：' in result.stderr,
                  result.stdout + result.stderr)
            check(before == sorted(str(p.relative_to(root)) for p in root.rglob('*')), 'failed preflight modified HOME')
            if kind == 'directory':
                check((repo / 'sentinel').read_text() == 'untouched', 'directory modified')
            probe = ('from pathlib import Path; import doctor_config; doctor_config.check_config(Path("'
                     + str(source) + '"), include_local=False)')
            result = inprocess_sh([sys.executable, '-c', probe], env=env, cwd=source)
            check('⚠ 安装链接 aiw' in result.stdout and '指向另一份 checkout' in result.stdout, result.stdout + result.stderr)



def install_hook_duplicates():
    with tempfile.TemporaryDirectory() as td:
        home, env = fake_home(Path(td))
        env.pop('AIW_NO_CODEX', None)
        (home / '.codex').mkdir(exist_ok=True)
        files = [home / '.claude/settings.json', home / '.codex/hooks.json']
        data = {'other': 42, 'hooks': {'UserPromptSubmit': [
            {'matcher': '*', 'extra': 1, 'hooks': [
                {'type': 'command', 'command': 'python3 ' + 'aiw' + '/route.py'},
                {'type': 'command', 'command': 'echo other'}]},
            {'matcher': 'second', 'extra': 2, 'hooks': [
                {'type': 'command', 'command': 'python3 ' + 'aiw' + '/route.py'}]}]}}
        for path in files:
            path.write_text(json.dumps(data))
        command = ['bash', str(home / '.claude/aiw/install.sh')]
        result = sh(command, env=env)
        check(result.returncode == 0, result.stdout + result.stderr)
        for path in files:
            value = json.loads(path.read_text())
            groups = value['hooks']['UserPromptSubmit']
            check([(g['matcher'], g['extra']) for g in groups] == [('*', 1), ('second', 2)], str(value))
            hooks = [h for g in groups for h in g['hooks']]
            check(sum('aiw/route.py' in h['command'] for h in hooks) == 1
                  and any(h['command'] == 'echo other' for h in hooks), str(value))
            check(value['other'] == 42, str(value))
        before = [p.read_bytes() for p in files]
        repo = home / '.claude/aiw'
        result = install_phase(repo, 3, [files[0], 'test', 'settings'], env)
        codex = install_phase(repo, 5, [home / '.codex', 'test'], env)
        check(codex.returncode == 0, codex.stderr)
        check(result.returncode == 0 and before == [p.read_bytes() for p in files], 'duplicate removal not idempotent')
        result = install_phase(repo, 3, [files[0], 'test', 'plugin'], env)
        groups = json.loads(files[0].read_text())['hooks']['UserPromptSubmit']
        check(result.returncode == 0 and len(groups) == 2 and groups[1]['hooks'] == [], str(groups))



def install_empty_python_state():
    for python in ('', None):
        with tempfile.TemporaryDirectory() as td:
            home, env = fake_home(Path(td))
            env.pop('AIW_PYTHON', None)
            if python is not None:
                env['AIW_PYTHON'] = python
            env['AIW_STATE'] = '~/expanded-state'
            repo = home / '.claude/aiw'
            result = sh(['bash', str(repo / 'install.sh')], env=env)
            check(result.returncode == 0, result.stdout + result.stderr)
            result = sh(['bash', str(repo / 'bin/aiw'), 'route', '--check'], env=env)
            check(result.returncode == 0, result.stdout + result.stderr)
            probe = ('from env_compat import getenv; '
                     'import ai_worker, route, findings, panel, map, handoff, tokens, weekly; '
                     'print(getenv("AIW_PYTHON")); print(getenv("AIW_STATE")); '
                     'print("\\n".join(str(m.STATE) for m in '
                     '(ai_worker, route, findings, panel, map, handoff, tokens, weekly)))')
            result = inprocess_sh([sys.executable, '-c', probe], env=env, cwd=repo)
            lines = result.stdout.splitlines()
            check(result.returncode == 0 and lines[0] == 'python3' and len(lines) == 10
                  and all(line == str(home / 'expanded-state') for line in lines[1:]),
                  result.stdout + result.stderr)



def install_launch_failure():
    import plistlib
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        home, env = fake_home(root)
        bins = root / 'launchbin'
        bins.mkdir()
        launch = bins / 'launchctl'
        launch.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$LAUNCH_LOG"\n[ "$1" = bootstrap ] && exit 1\nexit 0\n')
        launch.chmod(0o755)
        directory = root / 'agents'
        directory.mkdir()
        env.update(PATH=str(bins) + ':' + env['PATH'], AIW_LAUNCH_AGENTS_DIR=str(directory),
                   LAUNCH_LOG=str(root / 'launch.log'))
        result = sh(['bash', str(home / '.claude/aiw/bin/aiw'), 'freecheck', '--install'], env=env)
        check(result.returncode != 0 and '失败' in result.stderr, result.stdout + result.stderr)
        calls = (root / 'launch.log').read_text()
        check('bootstrap' in calls, 'bootstrap not attempted: ' + calls)
        check(plistlib.loads((directory / 'com.aiw.freecheck.plist').read_bytes())['Label'] == 'com.aiw.freecheck',
              'new plist invalid')



CASES = {case.__name__: case for case in (
    agent_use_default_paths,
    install_agent_use_default_location,
    python_requirement,
    doctor,
    doctor_config,
    install_fresh,
    install_minimal,
    install_existing,
    install_personal_rules,
    install_claude_hook_mode,
    install_autocompact,
    install_preflight,
    doctor_fingerprint,
    doctor_readonly,
    install_agent_use_fresh_repeat,
    install_agent_use_preserves_config,
    install_agent_use_dangling_symlink_config,
    install_agent_use_registry_conflict,
    install_doctor_agent_use_hint,
    repository_git_env_shell_guard,
    install_sync_foreign_git_env,
    sync_agent_use_repos,
    install_target,
    install_hook_duplicates,
    install_empty_python_state,
    install_launch_failure,
)}


def install_pi_omp_agents():
    import doctor_config as d
    with tempfile.TemporaryDirectory() as td:
        home, env = fake_home(Path(td))
        repo = home / '.claude/aiw'
        personal = home / '.config/aiw/AGENTS.personal.md'
        personal.parent.mkdir(parents=True, exist_ok=True)
        personal.write_text('PERSONAL_RULE_TEST\n')
        for main in ('pi', 'omp'):
            folder = home / f'.{main}/agent'; folder.mkdir(parents=True)
            (folder / 'AGENTS.md').write_text('outside\n')
        def install():
            r = sh(['bash', str(repo/'install.sh')], env=env)
            check(r.returncode == 0, r.stdout+r.stderr)
            return r
        install()
        before = {}
        for main in ('pi', 'omp'):
            md = home/f'.{main}/agent/AGENTS.md'
            content = md.read_text(); before[main] = md.read_bytes()
            check(content.startswith('outside\n') and content.count(d.BEGIN) == 1
                  and 'PERSONAL_RULE_TEST' in content
                  and (repo/'AGENTS.harness.md').read_text().strip() in content, content)
            check(not (md.parent/'hooks.json').exists(), 'pi/omp hooks written')
        install()
        for main in before:
            check((home/f'.{main}/agent/AGENTS.md').read_bytes() == before[main], 'not idempotent')
        # The startup doctor checks both installed blocks, including stale content.
        r = sh([sys.executable, str(repo/'ai_worker.py'), 'doctor'], env=env)
        check(all(f'✓ {m} AGENTS 标记块' in r.stdout for m in before), r.stdout+r.stderr)
        src = repo/'AGENTS.harness.md'; src.write_text(src.read_text()+'\nREFRESH_TEST\n')
        env['AIW_NO_PI'] = '1'; env['AIW_NO_OMP'] = '1'
        install()
        check(all((home/f'.{m}/agent/AGENTS.md').read_bytes() == before[m] for m in before), 'skip failed')
        env.pop('AIW_NO_PI'); env.pop('AIW_NO_OMP')
        r = sh([sys.executable, str(repo/'ai_worker.py'), 'doctor'], env=env)
        check(all(f'⚠ {m} AGENTS 标记块' in r.stdout for m in before), r.stdout)
        install()
        check(all('REFRESH_TEST' in (home/f'.{m}/agent/AGENTS.md').read_text() for m in before), 'refresh failed')
        # Command-only discovery creates the agent directory too.
        import shutil
        bins = Path(td)/'main-bins'; bins.mkdir()
        for main in before:
            shutil.rmtree(home/f'.{main}')
            binary = bins/main; binary.write_text('#!/bin/sh\nexit 0\n'); binary.chmod(0o755)
        env['PATH'] = str(bins)+':'+env['PATH']
        env['AIW_NO_PI'] = '1'
        install()
        check(not (home/'.pi').exists() and (home/'.omp/agent/AGENTS.md').is_file(), 'command detection / skip')


CASES['install_pi_omp_agents'] = install_pi_omp_agents


def uninstall_roundtrip():
    with tempfile.TemporaryDirectory() as td:
        home, env = fake_home(Path(td))
        repo = home / '.claude/aiw'
        # Exercise the symlink installation layout as well as the checkout layout.
        checkout = home / 'checkout'
        repo.rename(checkout)
        repo.symlink_to(checkout, target_is_directory=True)
        personal = home / '.config/aiw/CLAUDE.personal.md'
        personal.parent.mkdir(parents=True)
        personal.write_text('personal\n')
        md = home / '.claude/CLAUDE.md'
        md.write_bytes(b'user rules\r\n')
        settings = home / '.claude/settings.json'
        original = {'model': 'user-model', 'hooks': {
            'UserPromptSubmit': [{'hooks': [{'command': 'echo mine', 'type': 'command'}]}],
            'Stop': [{'hooks': [{'command': 'echo stop'}]}]}}
        settings.write_text(json.dumps(original))
        for directory in ('.codex', '.pi/agent', '.omp/agent'):
            folder = home / directory
            folder.mkdir(parents=True)
            (folder / 'AGENTS.md').write_text('user agents\n')
        result = sh(['bash', str(repo / 'install.sh')], env=env)
        check(result.returncode == 0, result.stdout + result.stderr)
        # 旧版 install.sh 会写 .zshrc；模拟旧安装，确认卸载仍清理。
        (home / '.zshrc').write_text('# aiw: on PATH\nexport PATH="$HOME/.local/bin:$PATH"\n')
        # install.sh normalizes newlines; verify uninstall preserves the current bytes.
        md.write_bytes(md.read_bytes().replace(b'user rules\n', b'user rules\r\n'))
        state = checkout / 'state/keep'
        state.write_text('keep')
        # Preserve content following the block, including a second blank line.
        agents = home / '.codex/AGENTS.md'
        agents.write_text(agents.read_text() + '\n\nafter\n')
        command = ['bash', str(checkout / 'uninstall.sh')]

        def snapshot():
            return {str(p.relative_to(home)): (('link', os.readlink(p)) if p.is_symlink()
                    else ('file', p.read_bytes())) for p in home.rglob('*')
                    if p.is_symlink() or p.is_file()}

        before = snapshot()
        result = sh(command + ['--dry-run'], env=env)
        check(result.returncode == 0 and '将修改' in result.stdout, result.stdout + result.stderr)
        check(snapshot() == before, 'dry-run modified files')
        result = sh(command, env=env)
        check(result.returncode == 0, result.stdout + result.stderr)
        check(md.read_bytes() == b'user rules\r\n\n', repr(md.read_bytes()))
        check(json.loads(settings.read_text()) == dict(original, autoCompactWindow=400000), settings.read_text())
        check('autoCompactWindow' in result.stdout and '可自行删除' in result.stdout, result.stdout)
        check(json.loads((home / '.codex/hooks.json').read_text()) == {}, 'empty hooks remain')
        for directory in ('.codex', '.pi/agent', '.omp/agent'):
            text = (home / directory / 'AGENTS.md').read_text()
            check('aiw:begin' not in text and 'user agents\n' in text, text)
        check(agents.read_text() == 'user agents\n\n\nafter\n', repr(agents.read_text()))
        check(not (home / '.local/bin/aiw').is_symlink() and not repo.is_symlink(), 'links remain')
        check('aiw: on PATH' not in (home / '.zshrc').read_text(), 'PATH remains')
        check(personal.read_text() == 'personal\n' and state.read_text() == 'keep', 'local data changed')
        # Every changed regular file has a recoverable backup; removed links leave no .bak.
        for path in (md, settings, agents, home / '.codex/hooks.json', home / '.zshrc'):
            check(list(path.parent.glob(path.name + '.bak-*')), f'no backup: {path}')
        for path in (home / '.local/bin/aiw', repo):
            check(not list(path.parent.glob(path.name + '.bak-*')), f'link backup left: {path}')
        before = snapshot()
        result = sh(command, env=env)
        check(result.returncode == 0 and snapshot() == before, 'second uninstall changed files')


def uninstall_preflight():
    for broken in ('.claude/settings.json', '.codex/hooks.json'):
        with tempfile.TemporaryDirectory() as td:
            home, env = fake_home(Path(td))
            repo = home / '.claude/aiw'
            md = home / '.claude/CLAUDE.md'
            md.write_text('@~/.claude/aiw/CLAUDE.md\n')
            path = home / broken
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('{oops')
            before = {str(p): p.read_bytes() for p in home.rglob('*') if p.is_file()}
            result = sh(['bash', str(repo / 'uninstall.sh')], env=env)
            check(result.returncode != 0 and '没有做任何改动' in result.stderr, result.stdout + result.stderr)
            check({str(p): p.read_bytes() for p in home.rglob('*') if p.is_file()} == before,
                  'invalid JSON changed files')
            result = sh(['bash', str(repo / 'uninstall.sh'), '--unknown'], env=env)
            check(result.returncode == 64 and '用法' in result.stderr, result.stdout + result.stderr)


def uninstall_preserves_foreign_entries():
    with tempfile.TemporaryDirectory() as td:
        home, env = fake_home(Path(td))
        repo = home / '.claude/aiw'
        link = home / '.local/bin/aiw'
        link.parent.mkdir(parents=True)
        link.symlink_to(home / 'other/bin/aiw')
        zsh = home / '.zshrc'
        zsh.write_text('# aiw: on PATH\nexport PATH="custom:$PATH"\n')
        agents = home / '.codex/AGENTS.md'
        agents.parent.mkdir()
        agents.write_text('<!-- aiw:begin（install.sh 生成，勿手改） -->\nblock\n<!-- aiw:end -->\n\n')
        settings = home / '.claude/settings.json'
        settings.write_text(json.dumps({'hooks': {'UserPromptSubmit': [
            {'hooks': [{'command': 'aiw/route.py'}], 'matcher': '*'},
            {'hooks': [{'command': 'aiw/route.py'}, {'command': 'echo mine'}]}]}}))
        result = sh(['bash', str(repo / 'uninstall.sh')], env=env)
        check(result.returncode == 0 and '仓库和 state/ 保留' in result.stdout, result.stdout + result.stderr)
        check(link.is_symlink() and os.readlink(link) == str(home / 'other/bin/aiw'), 'foreign link removed')
        check(zsh.read_text() == 'export PATH="custom:$PATH"\n', zsh.read_text())
        check(agents.exists() and agents.read_bytes() == b'', 'empty AGENTS removed')
        groups = json.loads(settings.read_text())['hooks']['UserPromptSubmit']
        check(groups == [{'hooks': [], 'matcher': '*'}, {'hooks': [{'command': 'echo mine'}]}], str(groups))
        check(repo.is_dir(), 'checkout removed')


CASES.update({case.__name__: case for case in (
    uninstall_roundtrip, uninstall_preflight, uninstall_preserves_foreign_entries,
)})
