"""Public defaults plus one optional, machine-local override (standard library only)."""
import copy
import hashlib
import json
import os
import string
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROLE_NAMES = frozenset(('write', 'review', 'draft'))
BUILTIN_COMMANDS = frozenset(('rate', 'note', 'findings', 'triage', 'track', 'errand', 'freecheck',
    'enable', 'doctor', 'handoff', 'route', 'panel', 'map', 'goal', 'board', 'statusline',
    'tokens', 'weekly', 'snapshot', 'gc', 'sync', 'help'))


class ConfigError(Exception):
    """An unusable configuration; never silently fall back from a local error."""


def local_config_path():
    explicit = os.environ.get('AIW_CONFIG')
    if explicit:
        return Path(explicit).expanduser()
    path = Path.home() / '.config/aiw/seats.local.json'
    return path if path.exists() or path.is_symlink() else None


def read_config(path):
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data, dict):
            raise ValueError('顶层必须是对象')
        return data
    except (OSError, ValueError) as exc:
        raise ConfigError(f'{path}: {str(exc).replace(chr(10), " ")}') from exc


def load_public(repo=None):
    return read_config((Path(repo) if repo is not None else HERE) / 'seats.json')


def merge(public, local):
    if isinstance(public, dict) and isinstance(local, dict):
        result = copy.deepcopy(public)
        for key, value in local.items():
            result[key] = merge(result[key], value) if key in result else copy.deepcopy(value)
        return result
    return copy.deepcopy(local)


def validate(merged) -> list[str]:
    """Return all structural and seat-reference errors without mutating config."""
    if not isinstance(merged, dict):
        return ['配置类型错误：顶层必须是对象']
    errors = []

    def error(path, expected):
        errors.append(f'配置类型错误：{path} 必须是{expected}')

    def strings(value, path):
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            error(path, '字符串列表')
            return False
        return True

    def concurrency(value, path):
        if type(value) is not int or value <= 0:
            error(path, '正整数')

    def templates(value, path, allowed, *, nonempty=False):
        if not strings(value, path):
            return
        if nonempty and not value:
            error(path, '非空字符串列表')
        for index, template in enumerate(value):
            if nonempty and not template:
                error(f'{path}[{index}]', '非空字符串')
                continue
            try:
                plain = []
                for literal, field, spec, conversion in string.Formatter().parse(template):
                    plain.append(literal.replace('{', '{{').replace('}', '}}'))
                    if field is not None and (field not in allowed or spec or conversion is not None):
                        raise ValueError('非法占位符')
                    if field is not None:
                        plain.append('{' + field + '}')
                # parse() normalizes {model:}; accept only the plain {model} form.
                if ''.join(plain) != template:
                    raise ValueError('非法占位符')
            except ValueError:
                error(f'{path}[{index}]', '合法命令模板（占位符仅允许 ' +
                      '、'.join('{' + key + '}' for key in allowed) + '，不允许格式或转换修饰）')

    for key in ('seats', 'fallback', 'self_seats', 'substitute_limit'):
        if not isinstance(merged.get(key), dict):
            error(key, '对象')
    for key in ('notify', 'findings', 'errand', 'main_exclude', 'forward'):
        if key in merged and not isinstance(merged[key], dict):
            error(key, '对象')
    if isinstance(merged.get('forward'), dict):
        strings(merged['forward'].get('command'), 'forward.command')
    seats = merged.get('seats')
    seats = seats if isinstance(seats, dict) else {}
    for name, seat in seats.items():
        path = f'seats.{name}'
        if not isinstance(seat, dict):
            error(path, '对象')
            continue
        kind = seat.get('kind')
        if kind not in ('agent_use', 'cli'):
            errors.append(f'{path}.kind 必须是 agent_use 或 cli')
        adapter = seat.get('adapter')
        if adapter is not None:
            if not isinstance(adapter, str):
                error(f'{path}.adapter', '字符串或 null')
            elif adapter not in ('codex',):
                errors.append(f'{path}.adapter 必须是 codex 或 null')
            if kind != 'cli':
                errors.append(f'{path}.adapter 非 null 时 kind 必须是 cli；'
                              '若该座位不再使用 Codex CLI，在本地配置中写 "adapter": null')
        required = ('provider',) if kind == 'agent_use' else ('bin',) if kind == 'cli' else ()
        for key in required:
            if not isinstance(seat.get(key), str) or not seat[key].strip():
                error(f'{path}.{key}', '非空字符串')
        if kind == 'cli':
            for key in ('argv_rw', 'argv_ro'):
                # build_argv supplies only these three format keywords.
                templates(seat.get(key), f'{path}.{key}', ('bin', 'prompt', 'workdir'), nonempty=True)
                if adapter == 'codex':
                    argv = seat.get(key)
                    if (not isinstance(argv, list) or argv[:1] != ['{bin}'] or argv[1:2] != ['exec']
                            or argv.count('--sandbox') != 1
                            or argv[argv.index('--sandbox') + 1:argv.index('--sandbox') + 2]
                            not in ((['read-only'],) if key == 'argv_ro' else (['read-only'], ['workspace-write']))):
                        errors.append(f'{path}.{key} 必须是 Codex exec 命令（argv[0] 为 {{bin}}，argv[1] 为 exec，'
                                      f'恰好一个 --sandbox，其后为 {"read-only" if key == "argv_ro" else "read-only 或 workspace-write"}）；'
                                      '若该座位不再使用 Codex CLI，在本地配置中写 "adapter": null')
        if 'model' in seat and not isinstance(seat['model'], str):
            error(f'{path}.model', '字符串')
        if kind == 'cli':
            if 'model_args' in seat or seat.get('model'):
                templates(seat.get('model_args'), f'{path}.model_args', ('model', 'effort'),
                          nonempty=bool(seat.get('model')))
        elif 'model_args' in seat:
            strings(seat['model_args'], f'{path}.model_args')
        # free_only 是展示/不换付费模型的声明；free_guard 才是执行时的收费检测保护。
        for key in ('disabled', 'ro_only', 'free_only', 'free_guard', 'require_no_tools', 'expects_credits', 'workspace'):
            if key in seat and type(seat[key]) is not bool:
                error(f'{path}.{key}', '布尔值')
        if seat.get('workspace') is True and seat.get('require_no_tools') is True:
            errors.append(f'{path}.workspace 与 require_no_tools 不能同时为 true')
        if 'max_concurrency' in seat:
            concurrency(seat['max_concurrency'], f'{path}.max_concurrency')
    roles = merged.get('roles', {})
    if not isinstance(roles, dict):
        error('roles', '对象')
        roles = {}
    for name in ROLE_NAMES & seats.keys():
        errors.append(f'角色名冲突：{name} 不能用作座位名；请重命名座位并更新引用')
    for identity, mapping in roles.items():
        if not isinstance(mapping, dict):
            error(f'roles.{identity}', '对象')
            continue
        for role, candidates in mapping.items():
            path = f'roles.{identity}.{role}'
            if role in seats or role in BUILTIN_COMMANDS:
                errors.append(f'角色名冲突：{role}')
            elif role not in ROLE_NAMES:
                errors.append(f'未知角色：{role}')
            if strings(candidates, path):
                for name in candidates:
                    if name not in seats:
                        errors.append(f'引用悬空：{path} → {name}')
    refs = []
    for key in ('fallback', 'self_seats', 'main_exclude'):
        values = merged.get(key)
        if isinstance(values, dict):
            for name, entries in values.items():
                if key == 'fallback':
                    refs.append((key, name))
                valid = strings(entries, f'{key}.{name}')
                # Identity filters may name optional seats absent on this machine.
                if valid and key == 'fallback':
                    refs.extend((f'{key}.{name}', entry) for entry in entries)
    for key in ('panel_default', 'panel_free'):
        if strings(merged.get(key), key):
            refs.extend((key, entry) for entry in merged[key])
    limits = merged.get('substitute_limit')
    if isinstance(limits, dict):
        refs.extend(('substitute_limit', name) for name in limits)
    if 'map_default' in merged:
        if not isinstance(merged['map_default'], str):
            error('map_default', '字符串')
        else:
            if merged['map_default'] not in ROLE_NAMES:
                refs.append(('map_default', merged['map_default']))
    for path, name in refs:
        if name not in seats:
            errors.append(f'引用悬空：{path} → {name}')
    if 'max_concurrency' in merged:
        concurrency(merged['max_concurrency'], 'max_concurrency')
    return errors


def forward_command(config):
    if os.environ.get('AIW_NO_FORWARD') == '1':
        return []
    value = config.get('forward', {}).get('command', [])
    if 'AIW_FORWARD' in os.environ:
        try:
            value = json.loads(os.environ['AIW_FORWARD'])
        except ValueError as exc:
            raise ConfigError('AIW_FORWARD 必须是 JSON 字符串数组') from exc
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigError('AIW_FORWARD / forward.command 必须是字符串数组')
    return value


def load_seats(repo=None, *, tolerate_public=False):
    # Read the override first: a broken public file must not hide local errors.
    path = local_config_path()
    local = read_config(path) if path is not None else {}
    try:
        public = load_public(repo)
    except ConfigError:
        if not tolerate_public:
            raise
        public = {}
    merged = merge(public, local)
    errors = validate(merged)
    if errors:
        raise ConfigError(f"{path or (Path(repo) if repo is not None else HERE) / 'seats.json'}: " + "; ".join(errors))
    return merged


def effective_hash(merged):
    keys = ('seats', 'fallback', 'panel_default', 'panel_free', 'self_seats', 'main_exclude', 'map_default',
            'substitute_limit', 'breaker', 'roles')
    data = {key: copy.deepcopy(merged[key]) for key in keys if key in merged}
    if isinstance(data.get('seats'), dict):
        for seat in data['seats'].values():
            if isinstance(seat, dict):
                for key in ('label', 'login_hint', 'tools_note'):
                    seat.pop(key, None)
    return hashlib.sha1(json.dumps(data, sort_keys=True, separators=(',', ':')).encode()).hexdigest()[:12]
