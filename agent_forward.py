"""Agent forwarding identity: digest only; receiver executes its own named recipes.
Run: python3 agent_forward.py resolve < request.json (installed aiw configuration).
Dependencies: standard library and seats_config from the same installation.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import sys

PROTOCOL = 'agent-desc-v1'
# 执行端在起任何进程前明确拒绝：点名座位没有同名等价行（或没有唯一等价行）。
RECEIVER_SEAT_UNAVAILABLE = 66
NAME = re.compile(r'[A-Za-z0-9][\w.-]*')
FIELDS = ('kind', 'adapter', 'bin', 'bin_env', 'argv_rw', 'argv_ro', 'model_args', 'provider',
          'workspace', 'account', 'env', 'free_only', 'free_guard', 'expects_credits',
          'require_no_tools', 'max_concurrency', 'models', 'preflight', 'model', 'effort')
IGNORED = {'label', 'login_hint', 'tools_note', 'version_args', 'disabled', 'disabled_by_user',
           'force_mode', 'ro_only', 'agent_pick', 'local_only', 'cost'}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def ceiling(row):
    return 'text' if row.get('require_no_tools') or row.get('force_mode') == 'text' else 'ro' if row.get('ro_only') or row.get('force_mode') == 'ro' else 'rw'


def fingerprint(row, home):
    if set(row) - set(FIELDS) - IGNORED:
        raise ValueError('agent-forward-unknown-fields')
    if not isinstance(row.get('env', {}), dict) or not isinstance(row.get('preflight', []), list):
        raise ValueError('agent-forward-invalid-recipe')
    if any(not isinstance(v, str) or not re.fullmatch(r'\$\{[A-Z_][A-Z0-9_]*\}', v) for v in row.get('env', {}).values()):
        raise ValueError('agent-forward-inline-environment')
    value = {k: row[k] for k in FIELDS if k in row}
    value = json.loads(json.dumps(value))
    def path(s):
        return '~/' + s[len(home) + 1:] if isinstance(s, str) and s.startswith(home.rstrip('/') + '/') else s
    if 'bin' in value:
        value['bin'] = path(value['bin'])
    if value.get('preflight'):
        value['preflight'][0] = path(value['preflight'][0])
    account = value.get('account', {})
    if 'codex_home' in account:
        account['codex_home'] = path(account['codex_home'])
    if account.get('api', {}).get('key_command'):
        account['api']['key_command'][0] = path(account['api']['key_command'][0])
    # Generated default effort/model use empty strings; legacy absence means the same.
    value.setdefault('effort', '')
    value.setdefault('model', '')
    return digest(value)


MODES = ('rw', 'ro', 'text')


def eligible(row, mode, entry_mode):
    """这一行能不能承接按 entry_mode 点名的请求（只看权限，不看内容）。"""
    if row.get('local_only'):
        return False
    effective = max((mode, ceiling(row)), key=MODES.index)
    if MODES.index(effective) < MODES.index(entry_mode):
        return False
    if entry_mode == 'rw' and ceiling(row) != 'rw':
        return False
    return entry_mode == 'rw' or row.get('kind') == 'agent_use' or bool(row.get('argv_ro'))


def matches(row, home, digest):
    try:
        return fingerprint(row, home) == digest
    except (ValueError, TypeError, AttributeError):
        return False


def runnable(config, home):
    """执行端能接的内容摘要，按请求权限分组；发起端据此在同步文件前判断（#1332）。
    只是预检：最后能不能执行仍以 resolve 为准。"""
    result = {m: set() for m in MODES}
    for row in config['seats'].values():
        try:
            digest = fingerprint(row, home)
        except (ValueError, TypeError, AttributeError):
            continue
        for m in MODES:
            if eligible(row, m, m):
                result[m].add(digest[:16])
    return {m: sorted(v) for m, v in result.items()}


def describe(config, chain, mode, home, exact=()):
    if mode not in ('rw', 'ro', 'text') or not 1 <= len(chain) <= 64 or len(set(chain)) != len(chain):
        raise ValueError('agent-forward-invalid-chain')
    result = []
    for name in chain:
        row = config['seats'][name]
        if not NAME.fullmatch(name) or row.get('local_only'):
            raise ValueError('agent-forward-local-only')
        effective = max((mode, ceiling(row)), key=('rw', 'ro', 'text').index)
        entry = {'name': name, 'digest': fingerprint(row, home), 'mode': effective}
        if name in exact:
            # 点名座位必须由同名行执行；不允许远端用等价别名顶替。
            entry['exact'] = True
        result.append(entry)
    return {'protocol': PROTOCOL, 'mode': mode, 'entries': result}


def resolve(config, request, home):
    entries = request.get('entries')
    mode = request.get('mode')
    if mode not in ('rw', 'ro', 'text'):
        raise ValueError('agent-forward-invalid-mode')
    if request.get('protocol') != PROTOCOL or not isinstance(entries, list) or not 1 <= len(entries) <= 64:
        raise ValueError('agent-forward-invalid-description')
    mapping = {}
    for entry in entries:
        if (not isinstance(entry, dict) or not {'name', 'digest', 'mode'} <= set(entry)
                or set(entry) - {'name', 'digest', 'mode', 'exact'}
                or ('exact' in entry and type(entry['exact']) is not bool)
                or not isinstance(entry['name'], str) or not NAME.fullmatch(entry['name'])
                or entry['name'] in mapping or entry['mode'] not in ('rw', 'ro', 'text')
                or not isinstance(entry['digest'], str) or not re.fullmatch('[0-9a-f]{64}', entry['digest'])):
            raise ValueError('agent-forward-invalid-entry')
        candidates = [name for name, row in config['seats'].items()
                      if eligible(row, mode, entry['mode']) and matches(row, home, entry['digest'])]
        if entry['name'] in candidates:
            candidates = [entry['name']]
        elif entry.get('exact') or len(candidates) != 1:
            # 两台的座位名由各自的账号叫法算出（同一个订阅这台叫 claude、那台叫 main），
            # 名字对不上不代表不是同一个座位。只认执行内容相同、权限档也相同的那一行：
            # 同一座位按可写/只读/只出文字各生成一行，权限档相同的只有一行（#1332）。
            # 这仍是执行端自己的配方，不是顶替：内容不同的别名照样拒（#1047）。
            candidates = [name for name in candidates if ceiling(config['seats'][name]) == entry['mode']]
        if len(candidates) != 1 or candidates[0] in mapping.values():
            raise ValueError('agent-forward-no-unique-equivalent')
        mapping[entry['name']] = candidates[0]
    return mapping


def main():
    if sys.argv[1:] == ['capabilities']:
        print(json.dumps({'protocols': [PROTOCOL]}))
        return
    if sys.argv[1:] not in (['resolve'], ['runnable']):
        raise ValueError('agent-forward-invalid-command')
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from seats_config import load_seats, effective_hash
    config = load_seats()
    if sys.argv[1] == 'runnable':
        table = {'runtime': 'agent-runtime-v1' in config.get('capabilities', []), 'seats': len(config['seats'])}
        print(json.dumps(dict(table, runnable=runnable(config, str(Path.home())), config_hash=effective_hash(config))))
        return
    request = json.loads(sys.stdin.read(65537))
    mapping = resolve(config, request, str(Path.home()))
    print(json.dumps({'mapping': mapping, 'config_hash': effective_hash(config)}))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        if str(error) == 'agent-forward-no-unique-equivalent':
            # 只有"执行端没有这个座位/没有同名等价行"是明确的未执行拒收；
            # 发起端可以安全地在本机重跑，其他 64 表示请求或配置有误，不重跑。
            print('Agent 转发身份无法核对：执行端没有点名座位或同名等价座位，未执行', file=sys.stderr)
            sys.exit(RECEIVER_SEAT_UNAVAILABLE)
        print('Agent 转发身份无法核对，未执行', file=sys.stderr)
        sys.exit(64)
