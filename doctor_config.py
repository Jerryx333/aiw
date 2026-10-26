"""doctor 的只读配置检查；运行 python3 ai_worker.py doctor；仅依赖标准库。"""
import hashlib
import json
import os
from env_compat import getenv
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tomllib
import contextvars
from contextlib import contextmanager
from urllib.parse import urlsplit

from gitenv import repository_git_env


_queries = contextvars.ContextVar('doctor_git_queries', default=None)


@contextmanager
def memoized_checks():
    """One read-only doctor invocation; never reuse results across invocations."""
    token = _queries.set({})
    try:
        yield
    finally:
        _queries.reset(token)


def git_query(path, *args, text=False, timeout=10):
    env = repository_git_env()
    key = (str(path), args, text, timeout, tuple(sorted(env.items())))
    cache = _queries.get()
    if cache is None or key not in cache:
        result = subprocess.run(['git', '-C', str(path), *args], capture_output=True,
                                text=text, timeout=timeout, env=env)
        if cache is None:
            return result
        cache[key] = result
    return cache[key]

HOOK_COMMAND = 'python3 "$HOME/.claude/aiw/route.py" --hook'
HOOK_TIMEOUT = 10
IMPORT_LINE = '@~/.claude/aiw/CLAUDE.md'
PERSONAL_LINE = '@~/.config/aiw/CLAUDE.personal.md'
BEGIN = '<!-- aiw:begin（install.sh 生成，勿手改） -->'
END = '<!-- aiw:end -->'
BOARD_INSTALL = '未安装 aiw-board（可选组件）：见 https://github.com/Jerryx333/aiw-board ，安装后确保 aiw-board 在 PATH 中'
REINSTALL = '重跑 install.sh'


def warn(name, problem, fix=REINSTALL):
    print(f'  ⚠ {name}：{problem}。修：{fix}')


def safe(name, fn, fix=REINSTALL):
    # 不输出异常原文，尤其 TOML 解析错误可能夹带密钥。
    try:
        return fn()
    except Exception as exc:
        reason = '文件不存在' if isinstance(exc, FileNotFoundError) else f'检查失败（{type(exc).__name__}）'
        warn(name, reason, fix)


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def plugin_hook(home):
    """List enabled aiw plugins with dispatch hooks; bad metadata is ignored."""
    try:
        enabled = read_json(home / '.claude/settings.json').get('enabledPlugins', {})
        installed = read_json(home / '.claude/plugins/installed_plugins.json')
        plugins = installed.get('plugins', installed)
        if not isinstance(enabled, dict) or not isinstance(plugins, dict):
            return []
    except (OSError, ValueError, AttributeError):
        return []
    matches = []
    for name, active in enabled.items():
        if not name.startswith('aiw@') or active is not True:
            continue
        records = plugins.get(name, [])
        if not isinstance(records, list):
            continue
        for record in records:
            try:
                data = read_json(Path(record['installPath']) / 'hooks/hooks.json')
                groups = data['hooks']['UserPromptSubmit']
                if not isinstance(groups, list):
                    continue
                commands = []
                for group in groups:
                    entries = group['hooks']
                    if not isinstance(entries, list):
                        raise ValueError('invalid hooks')
                    for hook in entries:
                        command = hook.get('command')
                        commands.append(hook.get('type') == 'command' and
                                        isinstance(command, str) and
                                        any(entry in command for entry in
                                            ('scripts/route-hook.sh', 'aiw/route.py')))
                if any(commands):
                    matches.append(name)
                    break
            except (OSError, ValueError, TypeError, KeyError, AttributeError):
                continue
    return sorted(matches)


def hooks(path, name, plugins=()):
    data = read_json(path)
    groups = data.get('hooks', {}).get('UserPromptSubmit', [])
    if not isinstance(groups, list):
        raise ValueError('钩子格式错误')
    mine = []
    for g, group in enumerate(groups):
        entries = group.get('hooks', [])
        if not isinstance(entries, list):
            raise ValueError('钩子格式错误')
        for i, hook in enumerate(entries):
            if 'aiw/route.py' in hook.get('command', ''):
                mine.append((g, i, hook))
    plugin_names = '、'.join(plugins)
    if plugins and mine:
        warn(name, f'settings.json 与插件 {plugin_names} 都有派单钩子，会重复执行',
             '重跑 install.sh --claude-hook=plugin')
    if not mine and len(plugins) >= 2:
        warn(name, f'多个插件（{plugin_names}）都有派单钩子，会重复执行',
             '在 Claude Code 中只保留一个 aiw 插件启用')
    elif not mine and plugins:
        print(f'  ✓ {name}（由插件 {plugin_names} 提供）')
    elif len(mine) != 1:
        warn(name, '没有 aiw 钩子' if not mine else f'有多个 aiw 钩子（{len(mine)} 个）')
    elif any(h.get('command') != HOOK_COMMAND or h.get('timeout') != HOOK_TIMEOUT for _, _, h in mine):
        warn(name, '命令或 timeout 与安装值不一致')
    elif not plugins:
        print(f'  ✓ {name}')
    return mine


def import_line(home):
    text = (home / '.claude/CLAUDE.md').read_text(encoding='utf-8')
    if IMPORT_LINE in text.splitlines():
        print('  ✓ CLAUDE.md 引入')
    else:
        warn('CLAUDE.md 引入', '缺少完整引入行')


def personal_import(home):
    personal = home / '.config/aiw/CLAUDE.personal.md'
    if not personal.exists():
        print('  · Claude 个人规则引入：无个人规则（可选）' + ('（断链）' if personal.is_symlink() else ''))
        return
    md = home / '.claude/CLAUDE.md'
    if md.exists() and PERSONAL_LINE in md.read_text(encoding='utf-8').splitlines():
        print('  ✓ Claude 个人规则引入')
    else:
        warn('Claude 个人规则引入', '缺少完整引入行')


def link(path, expected, name):
    actual = path.resolve()
    if path.exists() and actual == expected.resolve():
        print(f'  ✓ {name}')
    else:
        warn(name, f'实际指向 {str(actual)!r}（目标不存在或不是当前仓库）')


def agents(home, repo, main='Codex'):
    rel = '.codex/AGENTS.md' if main == 'Codex' else f'.{main}/agent/AGENTS.md'
    text = (home / rel).read_text(encoding='utf-8')
    content = (repo / 'AGENTS.harness.md').read_text(encoding='utf-8').strip()
    personal = home / '.config/aiw/AGENTS.personal.md'
    if personal.exists():
        content += "\n\n" + personal.read_text(encoding='utf-8').strip()
    block = f"{BEGIN}\n{content}\n{END}\n"
    if text.count(BEGIN) == text.count(END) == 1 and block in text:
        print(f'  ✓ {main} AGENTS 标记块')
    else:
        warn(f'{main} AGENTS 标记块', '没有标记块或内容不一致')


def trust(home, hp, mine):
    config = tomllib.loads((home / '.codex/config.toml').read_text(encoding='utf-8'))
    if config.get('features', {}).get('hooks') is False:
        warn('Codex 钩子功能', 'Codex 关闭了钩子功能', '在 config.toml 中启用 features.hooks')
    states = config.get('hooks', {}).get('state', {})
    if not isinstance(states, dict):
        raise ValueError('信任记录格式错误')
    if not mine:
        print('  · Codex 信任：没有可核对的 aiw 钩子')
    for g, i, _ in mine:
        name = f'Codex 信任 [{g}:{i}]'
        records = [states[key] for key in dict.fromkeys(
            f'{p}:user_prompt_submit:{g}:{i}' for p in (hp, hp.resolve())) if key in states]
        if any(r.get('enabled') is False for r in records):
            warn(name, '被禁用', '在 Codex 中重新启用并确认信任这个钩子')
        elif any(r.get('trusted_hash') for r in records):
            print(f'  ✓ {name}（只能确认有信任记录，无法确认是否过期）')
        else:
            warn(name, '没有信任记录；Codex 会跳过这个钩子', '开一次 Codex 交互会话按提示确认信任')


def statusline(home):
    command = read_json(home / '.claude/settings.json').get('statusLine', {}).get('command', '')
    if not any(s in command for s in ('aiw-statusline', 'aiw-board')):
        print('  · 状态栏：不是 aiw 状态栏，跳过')
        return
    first = shlex.split(command)[0]
    first = str(home) + first[1:] if first == '~' or first.startswith('~/') else first
    resolved = first if '/' in first else shutil.which(first)
    path = Path(resolved) if resolved else None
    if path is not None and path.is_file() and os.access(path, os.X_OK):
        print('  ✓ 状态栏命令')
    else:
        warn('状态栏命令', '状态栏命令不存在，状态栏会静默失效（或文件不可执行）', BOARD_INSTALL)


def board():
    if shutil.which('aiw-board'):
        print('  ✓ aiw-board 转发')
    else:
        warn('aiw-board 转发', '`aiw board` / `aiw statusline` 转发不可用', BOARD_INSTALL)


def repository(repo):
    def git(*args):
        return git_query(repo, *args)

    try:
        if git('rev-parse', '--show-toplevel').returncode:
            print('  · 仓库状态：不是 git 仓库或 git 失败')
            return
        dirty = git('status', '--porcelain=v1', '-z')
        if dirty.returncode:
            print('  · 仓库状态：git status 失败')
            return
        # -z 的重命名记录额外占一个字段，不能直接按行计数。
        fields = iter(dirty.stdout.split(b'\0'))
        count = 0
        for field in fields:
            if field:
                count += 1
                if b'R' in field[:2] or b'C' in field[:2]:
                    next(fields, None)
        if count:
            warn('仓库状态', f'未提交改动 {count} 项', '检查 git diff，提交或妥善保存改动')
        else:
            print('  ✓ 仓库状态（无未提交改动）')
        if git('rev-parse', '--verify', '@{upstream}').returncode:
            print('  · 仓库上游：没有上游分支或 git 查询失败')
            return
        result = git('rev-list', '--left-right', '--count', 'HEAD...@{upstream}')
        if result.returncode:
            print('  · 仓库上游：git 比较失败')
            return
        ahead, behind = map(int, result.stdout.split())
        summary = f'领先 {ahead} / 落后 {behind}（按上次 fetch）'
        if ahead or behind:
            fix = '；'.join(s for s in ('aiw sync' if behind else '', '领先提交还没 push，确认后 push' if ahead else '') if s)
            warn('仓库上游', summary, fix)
        else:
            print(f'  ✓ 仓库上游（{summary}）')
    except Exception as exc:
        print(f'  · 仓库状态：git 检查失败（{type(exc).__name__}）')


def check_config(repo, include_local=True):
    home = Path(os.environ['HOME'])
    print('\n配置检查')
    minimal = (repo / 'state/minimal').exists()
    if minimal:
        print('  · 最小安装：未接 Claude 规则、派单钩子和 Codex / pi / omp 标记块（要接入重跑 install.sh，不带 --minimal）')
    else:
        safe('Claude 派单钩子', lambda: hooks(home / '.claude/settings.json', 'Claude 派单钩子', plugins=plugin_hook(home)))
        safe('CLAUDE.md 引入', lambda: import_line(home))
        safe('Claude 个人规则引入', lambda: personal_import(home))
    installation = home / '.claude/aiw'
    if installation.exists() or installation.is_symlink():
        if installation.resolve() == repo.resolve():
            print('  ✓ 安装链接 aiw')
        else:
            warn('安装链接 aiw', f'~/.claude/aiw 指向另一份 checkout：{installation.resolve()}，请先处理')
    else:
        warn('安装链接 aiw', '安装路径不存在')
    for rel, target, name in (('.local/bin/aiw', repo / 'bin/aiw', '安装链接 aiw'),):
        safe(name, lambda: link(home / rel, target, name))
    hp = home / '.codex/hooks.json'
    if minimal:
        pass
    elif getenv('AIW_NO_CODEX'):
        print('  · 跳过 Codex（AIW_NO_CODEX 已设置；钩子、信任、功能及 AGENTS 均跳过）')
    elif not hp.exists():
        print('  · 未接入 Codex（hooks.json 不存在；钩子、信任、功能及 AGENTS 均跳过）')
    else:
        mine = safe('Codex 派单钩子', lambda: hooks(hp, 'Codex 派单钩子'))
        safe('Codex 配置', lambda: trust(home, hp, mine or []), '检查 config.toml 格式与读取权限，并确认钩子信任')
        safe('Codex AGENTS 标记块', lambda: agents(home, repo))
    for main in ('pi', 'omp'):
        name = f'{main} AGENTS 标记块'
        if minimal:
            continue
        if getenv(f'AIW_NO_{main.upper()}') == '1':
            print(f'  · 跳过 {main}（AIW_NO_{main.upper()}=1）')
        elif (home / f'.{main}').is_dir() or shutil.which(main):
            safe(name, lambda main=main: agents(home, repo, main))
        else:
            print(f'  · 未接入 {main}（未发现目录或命令）')
    safe('状态栏命令', lambda: statusline(home), BOARD_INSTALL)
    safe('aiw-board 转发', board, BOARD_INSTALL)
    repository(repo)
    return local_config(repo) if include_local else 0


def local_config(repo):
    """Inspect overrides without disclosing string leaf values; return failures."""
    from seats_config import ConfigError, local_config_path, load_public, read_config, merge, validate
    print('\n本地配置')
    path = local_config_path()
    if path is None:
        print('  · 无本地配置（使用公开默认）')
    else:
        print(f'  · 来源：{path}' + (f' → {path.resolve()}' if path.is_symlink() else ''))
    try:
        public = load_public(repo)
        local = read_config(path) if path is not None else {}
    except ConfigError as error:
        print(f'  ✗ 本地配置解析失败：{error}')
        return 1
    merged = merge(public, local)
    errors = validate(merged)
    for error in errors:
        print(f'  ✗ {error}')
    failures = len(errors)
    missing = object()
    leaves = []
    public_seats = public.get('seats', {})
    public_seats = public_seats if isinstance(public_seats, dict) else {}
    new_seats = sorted(set(local.get('seats', {})) - set(public_seats)) if isinstance(local.get('seats', {}), dict) else []
    if new_seats:
        print(f'  · 仅本地的座位：{new_seats}')

    seat_fields = {"disabled", "disabled_by_user", "max_concurrency", "kind", "provider", "model", "models",
                   "free_guard", "free_only", "expects_credits", "require_no_tools", "tools_note", "adapter", "workspace"}
    seat_fields.update(key for seat in public_seats.values() if isinstance(seat, dict) for key in seat)

    known_seats = merged.get('seats', {})
    if not isinstance(known_seats, dict):
        known_seats = {}
    for name, seat in known_seats.items():
        if not isinstance(seat, dict) or seat.get('kind') != 'cli' or seat.get('adapter') is not None:
            continue
        if any(isinstance(argv, list) and argv[1:2] == ['exec'] and '--sandbox' in argv
               for argv in (seat.get('argv_rw'), seat.get('argv_ro'))):
            warn(f'seats.{name}.adapter', '看起来是 Codex CLI，但未声明 adapter，不享有 codex 专属行为',
                 '在本地座位配置中设置 "adapter": "codex"')

    def private_path(parts):
        return (len(parts) >= 2 and parts[0] in ('notify', 'errand') or
                parts == ('findings', 'hub') or
                len(parts) >= 3 and parts[:2] == ('findings', 'owners') or
                len(parts) == 3 and parts[0] == 'seats' and
                parts[2] in ('login_hint', 'disabled_by_user'))

    def walk(value, base, parts=()):
        key = '.'.join(parts)
        new_seat = len(parts) >= 2 and parts[0] == 'seats' and parts[1] in new_seats
        known_optional = (len(parts) == 3 and parts[0] == "seats" and parts[2] in seat_fields) or parts in (("map_default",), ("errand",), ("errand", "home"))
        if parts == ('forward', 'runner_hosts') or (len(parts) == 3 and
                parts[:2] == ('forward', 'runner_hosts') and parts[2].isdigit()):
            known_optional = True
        if len(parts) == 2 and parts[0] in ('fallback', 'substitute_limit', 'self_seats', 'main_exclude'):
            known_optional = parts[1] in known_seats or (parts[0] in ('self_seats', 'main_exclude') and parts[1] in ('claude', 'codex', 'pi', 'omp'))
        if base is missing and not new_seat and not known_optional and (not parts or not parts[-1].isdigit()):
            print(f'  ⚠ 未知键路径：{key}')
        if isinstance(value, dict):
            for name, child in value.items():
                walk(child, base.get(name, missing) if isinstance(base, dict) else missing, (*parts, name))
        elif isinstance(value, list):
            if isinstance(base, list) and base and value != base:
                print(f'  · 列表整体替换：{key}；公开版：{json.dumps(base, ensure_ascii=False)}（公开版更新不会自动合入）')
            for index, child in enumerate(value):
                walk(child, base[index] if isinstance(base, list) and index < len(base) else missing, (*parts, str(index)))
        elif isinstance(value, str) and len(value) >= 6 and value != base and private_path(parts):
            leaves.append((key, value.encode()))

    walk(local, public)
    if leaves:
        try:
            remotes = git_query(repo, 'config', '--get-regexp', '^remote\\..*\\.url$')
            urls = [line.split(None, 1)[1] for line in remotes.stdout.splitlines()
                    if len(line.split(None, 1)) == 2] if remotes.returncode == 0 else []
        except (OSError, subprocess.TimeoutExpired):
            urls = []
        public_segments = set()
        for url in urls:
            url = url.decode(errors='surrogateescape')
            if '://' in url:
                try:
                    parsed = urlsplit(url)
                except ValueError:
                    continue
                # file:// remotes are local paths, not public addresses.
                if parsed.scheme == 'file' or not parsed.netloc:
                    continue
                path = parsed.path
            else:
                match = re.fullmatch(r'(?:[^/@:]+@)?[^/@:]+:(.+)', url)
                if not match or re.match(r'^[A-Za-z]:[/\\]', url):
                    continue
                path = match.group(1)
            segments = [segment for segment in path.split('/') if segment]
            if segments:
                segments[-1] = segments[-1].removesuffix('.git')
                public_segments.update(segment.encode(errors='surrogateescape') for segment in segments)
        leaves = [(key, value) for key, value in leaves if value not in public_segments]
    if leaves:
        result = git_query(repo, 'ls-files', '-z')
        if result.returncode:
            print('  ⚠ 防泄漏：无法列出已跟踪文件')
        else:
            for filename in result.stdout.decode(errors='surrogateescape').split('\0'):
                if not filename:
                    continue
                target = repo / filename
                # Do not follow tracked links outside the repository.
                if target.is_symlink():
                    continue
                try:
                    content = target.read_bytes()
                except OSError:
                    continue
                for key, value in leaves:
                    if value in content:
                        print(f'  ⚠ 防泄漏：{key} 命中 {filename}')
    return failures


def git_fingerprint(path):
    """Return an internal root for deduplication and a path-free revision."""
    try:
        def git(*args):
            result = git_query(path, *args, text=True, timeout=5)
            if result.returncode:
                raise ValueError('git query failed')
            return result.stdout.strip()
        location = git_query(path, 'rev-parse', '--show-toplevel')
        if location.returncode:
            return None, '-'
        root = location.stdout.decode().strip()
        head = git('rev-parse', '--short', 'HEAD')
        return root, head + ('*' if git('status', '--porcelain') else '')
    except (OSError, ValueError, subprocess.SubprocessError):
        return None, '-'


def fingerprint(repo, seats=None, registry_info=None, adapter_home=None):
    """Stable, path-free drift data shared by doctor and snapshot."""
    from seats_config import load_seats, local_config_path, effective_hash
    if registry_info is None or adapter_home is None:
        from ai_worker import agent_use_home, agent_use_registry_info
        registry_info = registry_info or agent_use_registry_info
        adapter_home = adapter_home or agent_use_home

    def sha(path):
        try:
            return hashlib.sha256(path.read_bytes()).hexdigest()[:12] if path else '-'
        except OSError:
            return '-'

    revisions = {}
    def cached_fingerprint(path):
        try:
            result = git_query(path, 'rev-parse', '--show-toplevel')
            if result.returncode:
                return None, '-'
            key = result.stdout.decode().strip()
        except (OSError, ValueError, UnicodeError, subprocess.SubprocessError):
            return None, '-'
        if key not in revisions:
            revisions[key] = git_fingerprint(key)
        return revisions[key]

    def revision(path):
        return cached_fingerprint(path)[1] if path else '-'

    home = Path.home()
    try:
        registry, source = registry_info()
    except Exception as error:
        registry, source = None, getattr(error, "source", "repo")
    # Avoid importing route: its startup validation exits on broken overrides.
    route_path = Path(os.environ.get('AIW_ROUTER_RULES') or
                      home / '.config/aiw/router-rules.local.json').expanduser()
    paths = {'seats': local_config_path(), 'route': route_path,
             'claude': home / '.config/aiw/CLAUDE.personal.md',
             'agents': home / '.config/aiw/AGENTS.personal.md'}
    roots = {}
    local = {}
    for name, path in paths.items():
        local[name] = sha(path)
        if path is not None and path.exists():
            root, head = cached_fingerprint(path.resolve().parent)
            if root:
                roots[root] = head
    try:
        effective = effective_hash(load_seats(repo) if seats is None else seats)
    except Exception:
        effective = '-'
    return {'harness': revision(repo),
            'agent_use': revision(adapter_home()),
            'registry': {'source': source, 'sha256': sha(registry),
                         'git': revision(registry.resolve().parent) if registry and registry.exists() else '-'},
            'local': local, 'local_git': sorted(roots.values()) or ['-'],
            'effective_hash': effective}


def fingerprint_line(data):
    return '指纹：' + json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def registry_check(seats, registry_path):
    """Warnings only: registry drift does not change installation status."""
    configured = seats.get('seats') if isinstance(seats, dict) else None
    configured = configured if isinstance(configured, dict) else {}
    active = {name: cfg for name, cfg in configured.items()
              if isinstance(cfg, dict) and cfg.get('kind') == 'agent_use' and not cfg.get('disabled_by_user')}
    if not active:
        return
    fix = '对齐 agent-use 注册表与本地座位配置'
    try:
        providers = read_json(registry_path())['providers']
        if not isinstance(providers, dict) or any(not isinstance(v, dict) for v in providers.values()):
            raise ValueError('invalid providers')
    except Exception:
        warn('agent-use 注册表', '不可读或格式错误', fix)
        return
    for name, cfg in active.items():
        entry = providers.get(cfg['provider'])
        if entry is None:
            warn(f'agent-use 座位 {name}', 'provider 不在生效注册表中', fix)
        elif entry.get('enabled') is False:
            warn(f'agent-use 座位 {name}', 'provider 的 enabled=false', fix)
