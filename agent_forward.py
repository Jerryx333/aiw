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


def describe(config, chain, mode, home):
    if mode not in ('rw', 'ro', 'text') or not 1 <= len(chain) <= 64 or len(set(chain)) != len(chain):
        raise ValueError('agent-forward-invalid-chain')
    result = []
    for name in chain:
        row = config['seats'][name]
        if not NAME.fullmatch(name) or row.get('local_only'):
            raise ValueError('agent-forward-local-only')
        effective = max((mode, ceiling(row)), key=('rw', 'ro', 'text').index)
        result.append({'name': name, 'digest': fingerprint(row, home), 'mode': effective})
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
        if not isinstance(entry, dict) or set(entry) != {'name', 'digest', 'mode'} or not isinstance(entry['name'], str) or not NAME.fullmatch(entry['name']) or entry['name'] in mapping or entry['mode'] not in ('rw', 'ro', 'text') or not isinstance(entry['digest'], str) or not re.fullmatch('[0-9a-f]{64}', entry['digest']):
            raise ValueError('agent-forward-invalid-entry')
        candidates = []
        for name, row in config['seats'].items():
            if row.get('local_only'):
                continue
            effective = max((mode, ceiling(row)), key=('rw', 'ro', 'text').index)
            if ('rw', 'ro', 'text').index(effective) < ('rw', 'ro', 'text').index(entry['mode']):
                continue
            if entry['mode'] == 'rw' and ceiling(row) != 'rw':
                continue
            if entry['mode'] != 'rw' and row.get('kind') != 'agent_use' and not row.get('argv_ro'):
                continue
            try:
                if fingerprint(row, home) == entry['digest']:
                    candidates.append(name)
            except (ValueError, TypeError, AttributeError):
                continue
        if entry['name'] in candidates:
            candidates = [entry['name']]
        if len(candidates) != 1 or candidates[0] in mapping.values():
            raise ValueError('agent-forward-no-unique-equivalent')
        mapping[entry['name']] = candidates[0]
    return mapping


def main():
    if sys.argv[1:] == ['capabilities']:
        print(json.dumps({'protocols': [PROTOCOL]}))
        return
    if sys.argv[1:] != ['resolve']:
        raise ValueError('agent-forward-invalid-command')
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from seats_config import load_seats, effective_hash
    config = load_seats()
    request = json.loads(sys.stdin.read(65537))
    mapping = resolve(config, request, str(Path.home()))
    print(json.dumps({'mapping': mapping, 'config_hash': effective_hash(config)}))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('Agent 转发身份无法核对，未执行', file=sys.stderr)
        sys.exit(64)
