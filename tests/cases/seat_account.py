"""Seat account cases: a seat runs on its own Codex sign-in or a relay, never on the shared one."""
from __future__ import annotations

import json

from support import (
    inprocess_sh,
    Path,
    REPO,
    check,
    os,
    sys,
    tempfile,
    write_legacy_local,
)

FAKE_KEY = 'sk-fake-account-key-7f3a9c'
RELAY = 'https://relay.fake.invalid/v1'

# Records what it was started with; when given a key command (codex -c auth.command /
# claude --settings apiKeyHelper) it runs it like the real CLI, and keeps the key to itself.
FAKE_CLI = r'''#!{python}
import json, os, shlex, subprocess, sys
if sys.argv[1:2] == ['--version']:
    print('fake 1.0'); sys.exit(0)
args = sys.argv[1:]
key = None
for i, a in enumerate(args):
    if a.startswith('model_providers.aiw.auth.command='):
        command = [json.loads(a.split('=', 1)[1])]
        rest = next(x for x in args if x.startswith('model_providers.aiw.auth.args='))
        command += json.loads(rest.split('=', 1)[1])
        key = subprocess.run(command, capture_output=True, text=True).stdout.strip()
    if a == '--settings':
        helper = json.loads(args[i + 1]).get('apiKeyHelper')
        if helper:
            key = subprocess.run(['/bin/sh', '-c', helper], capture_output=True, text=True).stdout.strip()
with open(os.environ['FAKE_RECORD'], 'w') as f:
    json.dump({{'argv': args, 'env': dict(os.environ), 'key_ok': key == {key!r}}}, f)
print('fake ok')
'''


def setup(root, accounts):
    bins = root / 'bin'; bins.mkdir()
    cli = bins / 'fake-cli'
    cli.write_text(FAKE_CLI.format(python=sys.executable, key=FAKE_KEY)); cli.chmod(0o755)
    keytool = root / 'keys' / 'get-key'
    keytool.parent.mkdir()
    keytool.write_text(f'#!/bin/sh\n[ "$1" = relay ] && [ "$2" = "it\'s" ] || exit 3\necho {FAKE_KEY}\n'); keytool.chmod(0o755)
    shared = root / 'shared-codex'; shared.mkdir()
    (shared / 'config.toml').write_text('model_provider = "S"\n[model_providers.S]\nname = "S"\nbase_url = "https://shared.invalid"\n')
    (shared / 'auth.json').write_text('{"shared":"login"}')
    local = root / 'seats.local.json'
    write_legacy_local(local, {'seats': {seat: {'account': account(keytool) if callable(account) else account}
                                         for seat, account in accounts.items()}})
    pf = root / 'prompt.md'; pf.write_text('测试')
    wd = root / 'work'; wd.mkdir()
    env = dict(os.environ, HOME=str(root), CODEX_HOME=str(shared), CODEX_BIN=str(cli), CLAUDE_BIN=str(cli),
               AIW_CONFIG=str(local), AIW_STATE=str(root / 'state'), FAKE_RECORD=str(root / 'record.json'),
               ANTHROPIC_API_KEY='outer-key', ANTHROPIC_BASE_URL='https://outer.invalid', CLAUDE_CODE_USE_BEDROCK='1')
    env.pop('AIW_CODEX_SHARED_HOME', None)
    return env, pf, wd, keytool


def dispatch(env, pf, wd, seat, ro=True):
    cmd = [sys.executable, str(REPO / 'ai_worker.py'), seat, str(wd), '10', str(pf), '--no-findings']
    return inprocess_sh(cmd + (['--ro'] if ro else []), env=env)


def record(root):
    path = root / 'record.json'
    return json.loads(path.read_text()) if path.exists() else None


def no_key_anywhere(root, *outputs):
    for text in outputs:
        check(FAKE_KEY not in text, '密钥出现在输出里')
    for path in (root / 'state').rglob('*'):
        if path.is_file() and not path.is_symlink():
            check(FAKE_KEY.encode() not in path.read_bytes(), f'密钥出现在运行目录文件 {path}')
    rec = record(root)
    if rec is not None:
        check(FAKE_KEY not in json.dumps(rec['argv']) and FAKE_KEY not in json.dumps(rec['env']),
              '密钥出现在执行方的参数或环境里')


def account_validation():
    import seats_config as config
    public = config.load_public(REPO)

    def errors(seat, account):
        merged = config.merge(public, {'seats': {seat: {'account': account}}})
        return config.validate(merged)

    good = [('codex', {'codex_home': '/x/codex-a'}), ('codex-sol', {'codex_home': '~/codex-b'}),
            ('codex', {'api': {'base_url': RELAY, 'key_command': ['/usr/bin/security', 'x'], 'small_model': 'm'}}),
            ('claude', {'api': {'base_url': RELAY, 'key_command': ['/k'], 'small_model': 'mini'}}),
            ('claude-write', {'api': {'base_url': RELAY, 'key_command': ['/k']}}), ('codex', None)]
    for seat, account in good:
        check(errors(seat, account) == [], f'{seat} 合法账号被拒：{errors(seat, account)}')
    secret = 'sk-should-not-echo'
    bad = [('claude', {'codex_home': '/x'}), ('codex', {'codex_home': 'relative'}),
           ('codex', {'api': {'base_url': 'ftp://' + secret, 'key_command': ['/k']}}),
           ('codex', {'api': {'base_url': RELAY, 'key_command': ['k', secret]}}),
           ('codex', {'api': {'base_url': RELAY, 'key_command': []}}),
           ('codex', {'api': {'base_url': RELAY, 'key_command': ['/k'], 'api_key': secret}}),
           ('claude', {'api': {'base_url': RELAY, 'key_command': ['/k'], 'small_model': ''}}),
           ('codex', {'base_url': RELAY, 'key_command': ['/k']}), ('codex', {'api': secret}),
           ('codex', {'api': {'base_url': 'https://[bad', 'key_command': ['/k']}}),
           ('codex', {'api': {'base_url': 'https://', 'key_command': ['/k']}}),
           ('codex', {'codex_home': '/x', 'api': {}}), ('codex', 'codex-a'), ('codex', {})]
    for seat, account in bad:
        found = errors(seat, account)
        check(found and all('account' in e for e in found), f'{seat} {account} 应被拒：{found}')
        check(secret not in ' '.join(found), '校验信息泄漏字段值')
    merged = config.merge(public, {'seats': {'x': {'kind': 'cli', 'bin': 'qoder', 'argv_rw': ['{bin}'],
                                                  'argv_ro': ['{bin}'], 'account': {'codex_home': '/x'}}}})
    check(any('seats.x.account' in e for e in config.validate(merged)), '非 Codex 座位不应接受登录目录')


def account_base_seat():
    import seats_config as config
    public = config.load_public(REPO)
    account = {'api': {'base_url': RELAY, 'key_command': ['/bin/cat', '/k'], 'small_model': 'mini'}}
    local = {'seats': {'glm': {'base': 'claude', 'label': '智谱', 'model': 'glm-5', 'account': account, 'disabled': False},
                       'codex-b': {'base': 'codex', 'account': {'codex_home': '/x/b'}}}}
    merged = config.combine(public, local)
    check(config.validate(merged) == [], f'base 座位被拒：{config.validate(merged)}')
    glm, claude = merged['seats']['glm'], public['seats']['claude']
    check('base' not in glm and glm['argv_ro'] == claude['argv_ro'] and glm['bin_env'] == 'CLAUDE_BIN'
          and glm['model'] == 'glm-5' and glm['label'] == '智谱' and glm['account'] == account and glm['disabled'] is False,
          'base 继承或覆盖错误')
    check(merged['seats']['codex-b']['adapter'] == 'codex' and merged['seats']['codex']['model'] == public['seats']['codex']['model'],
          '派生座位不应改动原座位')
    for bad in ({'x': {'base': 'nope'}}, {'codex': {'base': 'claude'}}, {'x': {'base': 3}}):
        errors = config.validate(config.combine(public, {'seats': bad}))
        check(any('.base' in e for e in errors), f'{bad} 应报 base 错误：{errors}')


def account_readonly_install():
    """Heya runs aiw from a read-only directory with state and config elsewhere."""
    import shutil
    import subprocess
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        login = root / 'login'; login.mkdir(); (login / 'auth.json').write_text('{}')
        env, pf, wd, _ = setup(root, {'codex-b': {'codex_home': str(login)},
                                      'glm': lambda k: {'api': {'base_url': RELAY, 'key_command': [str(k), 'relay', "it's"]}}})
        local = json.loads(Path(env['AIW_CONFIG']).read_text())
        local['seats']['codex-b']['base'] = 'codex'
        local['seats']['glm'].update(base='claude', model='glm-5')
        Path(env['AIW_CONFIG']).write_text(json.dumps(local))
        install = root / 'install'
        shutil.copytree(REPO, install, ignore=shutil.ignore_patterns('.git', 'state', 'tests', '__pycache__', 'snapshots', '.codegraph'))
        before = sorted(str(p.relative_to(install)) for p in install.rglob('*'))
        for p in [install, *install.rglob('*')]:
            if not p.is_symlink():
                p.chmod(p.stat().st_mode & ~0o222)
        env['PYTHONDONTWRITEBYTECODE'] = '1'
        try:
            for seat in ('codex-b', 'glm'):
                r = subprocess.run([sys.executable, str(install / 'ai_worker.py'), seat, str(wd), '10', str(pf), '--ro'],
                                   env=env, capture_output=True, text=True, timeout=60)
                rec = record(root)
                check(r.returncode == 0 and rec and rec['key_ok'] == (seat == 'glm'), f'{seat}: {r.stderr}')
                check(seat != 'codex-b' or rec['env']['CODEX_HOME'] == str(login), 'codex-b 没用指定登录目录')
                no_key_anywhere(root, r.stdout, r.stderr)
                (root / 'record.json').unlink()
        finally:
            for p in [install, *install.rglob('*')]:
                if not p.is_symlink():
                    p.chmod(p.stat().st_mode | 0o200)
        check(sorted(str(p.relative_to(install)) for p in install.rglob('*')) == before, 'aiw 往自己的目录写了文件')


def account_codex_home():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        login = root / 'friend codex'
        env, pf, wd, _ = setup(root, {'codex': {'codex_home': str(login)}, 'codex-sol': {'codex_home': str(login)}})
        r = dispatch(dict(env, CODEX_BIN=str(root / 'no-such-codex')), pf, wd, 'codex')
        check(r.returncode == 1 and '登录目录不存在' in r.stderr, f'缺 CLI 时也应先报账号问题 {r.stderr}')
        # 目录不存在、没有 auth.json：本单失败，不启动、不回退共享登录、不顶替
        for state in ('missing', 'no_auth'):
            if state == 'no_auth':
                login.mkdir()
            r = dispatch(env, pf, wd, 'codex-sol')
            check(r.returncode == 1 and record(root) is None, f'{state}: 应失败且不启动执行方 {r.stderr}')
            check(str(login) in r.stderr and ('不存在' if state == 'missing' else 'auth.json') in r.stderr
                  and '不回退共享登录' in r.stderr, f'{state}: 原因不清 {r.stderr}')
            ledger = [json.loads(x) for x in (root / 'state/seat-ledger.jsonl').read_text().splitlines()]
            check(ledger[-1]['actual'] == 'codex-sol' and ledger[-1]['kind'] == 'account', '账本应记账号失败')
            breaker = root / 'state/breaker.json'
            check(not breaker.exists() or 'codex-sol' not in json.loads(breaker.read_text()), '账号缺失不应熔断')
        (login / 'auth.json').write_text('{"friend":"login"}')
        for seat, ro in (('codex-sol', True), ('codex', False)):
            r = dispatch(env, pf, wd, seat, ro)
            rec = record(root)
            check(r.returncode == 0 and rec, f'{seat} 未完成 {r.stderr}')
            check(rec['env']['CODEX_HOME'] == str(login), 'CODEX_HOME 不是指定登录目录')
            check(rec['argv'][:3] == ['exec', '-c', 'skills.include_instructions=false'], '隔离参数丢失')
            check(rec['env'].get('AIW_NESTED') == '1', 'AIW_NESTED 丢失')
            check(not (root / 'state/codex-home').exists(), '不应生成共享登录副本')
            (root / 'record.json').unlink()


def account_codex_relay():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        env, pf, wd, keytool = setup(root, {'codex-sol': lambda k: {'api': {'base_url': RELAY, 'key_command': [str(k), 'relay', "it's"]}}})
        r = dispatch(env, pf, wd, 'codex-sol')
        rec = record(root)
        check(r.returncode == 0 and rec, r.stderr)
        check(rec['key_ok'], '执行方没能用取密钥命令拿到密钥')
        home = (root / 'state/codex-home-api').resolve()
        check(rec['env']['CODEX_HOME'] == str(home), 'CODEX_HOME 应是中转专用 home')
        check(not (home / 'auth.json').exists() and not (root / 'state/codex-home').exists(), '中转座位不应带共享登录')
        argv = rec['argv']
        for item in ('skills.include_instructions=false', 'model_provider="aiw"', 'model_providers.aiw.wire_api="responses"',
                     f'model_providers.aiw.base_url="{RELAY}"', f'model_providers.aiw.auth.command="{keytool}"',
                     'model_providers.aiw.auth.args=["relay", "it\'s"]'):
            check(item in argv, f'codex 参数缺 {item}: {argv}')
        check(argv.index('--sandbox') > argv.index('model_provider="aiw"'), '-c 应插在 exec 之后')
        no_key_anywhere(root, r.stdout, r.stderr)


def account_claude_relay():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        account = lambda k: {'api': {'base_url': RELAY, 'key_command': [str(k), 'relay', "it's"], 'small_model': 'mini'}}
        env, pf, wd, keytool = setup(root, {'claude': account, 'claude-write': account})
        local = json.loads(Path(env['AIW_CONFIG']).read_text())
        argv = json.loads((REPO / 'seats.json').read_text())['seats']['claude-write']['argv_rw']
        i = argv.index('--settings') + 1
        argv[i] = argv[i].replace('{{\"sandbox\"', '{{\"env\":{{\"ANTHROPIC_BETAS\":\"old\",\"KEEP\":\"1\"}},\"sandbox\"')
        check('ANTHROPIC_BETAS' in argv[i], '测试夹具没改到 settings')
        local['seats']['claude-write']['argv_rw'] = argv
        Path(env['AIW_CONFIG']).write_text(json.dumps(local))
        for seat, ro in (('claude', True), ('claude-write', False)):
            r = dispatch(env, pf, wd, seat, ro)
            rec = record(root)
            check(r.returncode == 0 and rec, f'{seat}: {r.stderr}')
            check(rec['key_ok'], f'{seat}: apiKeyHelper 拿不到密钥')
            argv, child = rec['argv'], rec['env']
            check(argv.count('--settings') == 1, f'{seat}: --settings 应只有一个')
            settings = json.loads(argv[argv.index('--settings') + 1])
            senv = settings['env']
            check(senv['ANTHROPIC_BASE_URL'] == RELAY and senv['ANTHROPIC_API_KEY'] == '' and senv['ANTHROPIC_AUTH_TOKEN'] == ''
                  and senv['CLAUDE_CODE_USE_BEDROCK'] == '' and senv['CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC'] == '1',
                  f'{seat}: settings env 错误')
            check(senv['ANTHROPIC_MODEL'] == senv['ANTHROPIC_DEFAULT_OPUS_MODEL'] == senv['CLAUDE_CODE_SUBAGENT_MODEL'] == 'opus'
                  and senv['ANTHROPIC_DEFAULT_HAIKU_MODEL'] == 'mini', f'{seat}: 模型别名错误')
            check(str(keytool) in settings['apiKeyHelper'], f'{seat}: apiKeyHelper 错误')
            check(not any(k.startswith('ANTHROPIC_') for k in child) and 'CLAUDE_CODE_USE_BEDROCK' not in child,
                  f'{seat}: 外面的 ANTHROPIC_* 没清掉')
            if seat == 'claude-write':
                check(settings['sandbox'] == {'enabled': True, 'allowUnsandboxedCommands': False, 'failIfUnavailable': True},
                      '原有 sandbox 设置丢失')
                check(argv.index('--settings') < argv.index('--'), '--settings 应在 -- 之前')
                check('ANTHROPIC_BETAS' not in senv and senv.get('KEEP') == '1', '原 settings 的 ANTHROPIC_* 没清掉或误删其它变量')
            no_key_anywhere(root, r.stdout, r.stderr)
            (root / 'record.json').unlink()


def account_key_program_missing():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        env, pf, wd, _ = setup(root, {'claude': {'api': {'base_url': RELAY, 'key_command': [str(root / 'nope')]}}})
        r = dispatch(env, pf, wd, 'claude')
        check(r.returncode == 1 and record(root) is None and '取密钥程序不存在或不可执行' in r.stderr, r.stderr)
        breaker = root / 'state/breaker.json'
        check(not breaker.exists() or 'claude' not in json.loads(breaker.read_text()), '取密钥程序缺失不应熔断')


def account_doctor():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        login = root / 'codex-a'
        env, _, _, keytool = setup(root, {'codex': {'codex_home': str(login)},
                                         'claude': lambda k: {'api': {'base_url': RELAY + '?token=' + FAKE_KEY, 'key_command': [str(k), 'relay', "it's"]}}})
        r = inprocess_sh([sys.executable, str(REPO / 'ai_worker.py'), 'doctor'], env=env)
        check(r.returncode == 1 and f'✗ 账号：Codex 登录目录 {login} 不存在' in r.stdout, '登录目录缺失应报错: ' + r.stdout)
        check('✓ 账号：中转 relay.fake.invalid；取密钥程序 get-key 可执行（未运行）' in r.stdout, '中转检查行错误: ' + r.stdout)
        login.mkdir(); (login / 'auth.json').write_text('{}')
        r = inprocess_sh([sys.executable, str(REPO / 'ai_worker.py'), 'doctor'], env=env)
        check(r.returncode == 0 and '✓ 账号：Codex 登录目录' in r.stdout, r.stdout + r.stderr)
        keytool.chmod(0o644)
        r = inprocess_sh([sys.executable, str(REPO / 'ai_worker.py'), 'doctor'], env=env)
        check(r.returncode == 1 and '没有执行权限' in r.stdout, r.stdout)
        check(FAKE_KEY not in r.stdout + r.stderr and 'token=' not in r.stdout, 'doctor 泄漏密钥或完整地址')
        check(not (root / 'state/codex-home-api').exists(), 'doctor 不应生成 home')


def account_absent_unchanged():
    """No account: the launch is the one before this field existed."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        env, pf, wd, _ = setup(root, {})
        for seat in ('codex-sol', 'claude'):
            r = dispatch(env, pf, wd, seat)
            rec = record(root)
            check(r.returncode == 0 and rec, r.stderr)
            if seat == 'codex-sol':
                check(rec['env']['CODEX_HOME'] == str((root / 'state/codex-home').resolve()), '共享隔离 home 行为变了')
            else:
                check('--settings' not in rec['argv'] and rec['env'].get('ANTHROPIC_API_KEY') == 'outer-key',
                      '无账号的 claude 不应改参数或环境')
            check(not (root / 'state/codex-home-api').exists(), '无账号不应生成中转 home')
            (root / 'record.json').unlink()


CASES = {case.__name__: case for case in (
    account_validation,
    account_base_seat,
    account_readonly_install,
    account_codex_home,
    account_codex_relay,
    account_claude_relay,
    account_key_program_missing,
    account_doctor,
    account_absent_unchanged,
)}
