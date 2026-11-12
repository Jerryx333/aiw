"""Shared fixtures and helpers for isolated harness regression cases."""
from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


def sh(cmd, env=None, cwd=None, input=None):
    return subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True, text=True, env=env, cwd=cwd, input=input, timeout=120)


def wait_until(condition, message, timeout=120):
    """等待可观察事件；上限仅防挂死，不决定测试执行顺序。"""
    import time
    deadline = time.monotonic() + timeout
    while True:
        result = condition()
        if result:
            return result
        if time.monotonic() >= deadline:
            raise AssertionError(message)
        time.sleep(0.02)


def latest_provider(state: Path) -> Path:
    rows = [json.loads(line) for line in (state / "seat-ledger.jsonl").read_text().splitlines()]
    run = rows[-1].get("run_dir")
    return Path(run) / "provider" if run else state / "no-run-provider"


def fake_bins(d: Path) -> Path:
    b = d / "fakebin"
    b.mkdir(parents=True, exist_ok=True)
    (b / "qoder").write_text('#!/bin/bash\n[ "$1" = --version ] && { echo "qoder 0.0-fake"; exit 0; }\ncase "$FAKE_QODER" in\n auth) echo "error: authentication_required" >&2; exit 1;;\n fmt) echo "JSONDecodeError" >&2; exit 1;;\n slow) sleep 5; echo done;;\n weird) echo "segfault-ish" >&2; exit 3;;\n *) echo "qoder ok";;\nesac\n')
    (b / "codebuddy").write_text('#!/bin/bash\n[ "$1" = --version ] && { echo "2.157.0-fake"; exit 0; }\necho "workbuddy ok"\n')
    (b / "codex").write_text('#!/bin/bash\n[ "$1" = --version ] && { echo "codex-cli fake"; exit 0; }\n[ -n "$FAKE_CODEX_ARGS" ] && printf "%s\\n" "$@" > "$FAKE_CODEX_ARGS"\ncase "$FAKE_CODEX" in\n tokens) cat "$FAKE_CODEX_STDERR" >&2; echo "codex ok";;\n findings) cat "$FAKE_FINDINGS_OUTPUT";;\n chinese_stderr) python3 -c "import sys; sys.stderr.write(chr(0x4e2d) * 70000)"; echo "codex ok";;\n large_stderr) printf "%210000s" x >&2; echo "codex ok";;\n partial_timeout) echo "before timeout"; echo "timeout stderr" >&2; sleep 30;;\n write) echo changed > tracked; echo new > created; echo "codex ok";;\n write_slow) echo partial > partial; sleep 30; echo "codex ok";;\n commit_write) echo committed > tracked; git add tracked; git -c user.name=t -c user.email=t@t commit -qm changed; echo "codex ok";;\n chmod_dirty) chmod +x dirty; echo "codex ok";;\n stage_dirty) git add dirty; echo "codex ok";;\n restore) git restore tracked; echo "codex ok";;\n auth_sol) if [ ! -e "$FAKE_CODEX_AUTH_MARKER" ]; then touch "$FAKE_CODEX_AUTH_MARKER"; echo "error: authentication_required" >&2; exit 1; fi; echo "codex ok";;\n talky) echo "user: 讨论 quota 和 401 token expired 的判断" >&2; echo "回答：QUOTA_PAT 会匹配 usage limit、额度、401";;\n quota) echo "user: hi" >&2; echo "ERROR: You\'ve hit your usage limit. Try again later." >&2; exit 1;;\n *) echo "codex ok";;\nesac\n')
    (b / "claude").write_text('#!/bin/bash\n[ "$1" = --version ] && { echo "9.9.9 (Claude Code fake)"; exit 0; }\n[ -n "$FAKE_CLAUDE_ARGS" ] && printf "%s\\n" "$@" > "$FAKE_CLAUDE_ARGS"\n[ -n "$FAKE_CLAUDE_NOLOGIN" ] && { echo "Not logged in · Please run /login"; exit 1; }\n[ "$1" = --session-id ] && [ -n "$FAKE_CLAUDE_USAGE" ] && { mkdir -p "$CLAUDE_CONFIG_DIR/projects/p"; printf "%s\\n" "$FAKE_CLAUDE_USAGE" > "$CLAUDE_CONFIG_DIR/projects/p/$2.jsonl"; }\necho "claude ok nested=$AIW_NESTED"\n')
    (b / "gh").write_text("#!/bin/sh\nexit 1\n")
    (b / "security").write_text('#!/bin/sh\nexit 1\n')
    for f in b.iterdir():
        f.chmod(0o755)
    return b


FAKE_AGENT_USE = r'''#!/usr/bin/env python3
# 模拟 agent-use scripts/run_agent.py 的合同：结果目录必须是新目录；status.json + response.txt；失败 reason 在 attempts 里。
import argparse, json, os, sys, time
ap = argparse.ArgumentParser(); ap.add_argument("task"); ap.add_argument("output")
ap.add_argument("--order"); ap.add_argument("--timeout", type=float); ap.add_argument("--model", action="append", default=[])
ap.add_argument("--option", action="append", default=[])
ap.add_argument("--require-no-tools", action="store_true")
ap.add_argument("--workdir"); ap.add_argument("--access", choices=["none", "ro", "rw"], default="none")
a = ap.parse_args()
if os.path.exists(a.output):
    print("output exists", file=sys.stderr); sys.exit(2)
os.makedirs(a.output)
prov = a.order
mode = os.environ.get("FAKE_AU_" + prov.upper(), "ok")
log = os.environ.get("FAKE_AU_LOG")
if log:
    open(log, "a").write(json.dumps({"order": prov, "model": a.model, "options": a.option, "no_tools": a.require_no_tools, **({"argv": sys.argv[1:], "workdir": a.workdir, "access": a.access} if os.environ.get("FAKE_AU_WORKSPACE_LOG") else {}), **({"panel": os.environ["AIW_PANEL_DIR"]} if os.environ.get("AIW_PANEL_DIR") else {})}) + "\n")
if mode == "write":
    assert a.access == "rw" and os.getcwd() == a.workdir
    open("tracked", "w").write("changed\n")
    open("created", "w").write("new\n")
    mode = "ok"
if mode == "brief":
    time.sleep(2)
    mode = "ok"
if mode == "slow":
    time.sleep(a.timeout + 60)
att = {"provider": prov, "requested_model": a.model[0].split("=", 1)[1] if a.model else ""}
att.update(json.loads(os.environ.get("FAKE_ATTEMPT_FIELDS", "{}")))
att['model_attempts'] = json.loads(os.environ.get('FAKE_MODEL_ATTEMPTS', '[]'))
att['credits'] = json.loads(os.environ.get('FAKE_AU_CREDITS', '0'))
if mode in ('credits', 'failedcredits'):
    att['credits'] = 1.25
if mode == 'credits':
    mode = 'ok'
if mode == "ok":
    open(os.path.join(a.output, "response.txt"), "w").write(open(os.environ["FAKE_FINDINGS_OUTPUT"]).read() if os.environ.get("FAKE_FINDINGS_OUTPUT") else prov + " ok")
    st = {"protocol_version": 1, "state": "staged_for_review", "accepted": False, "attempts": [dict(att, state="staged_for_review")]}
    st.update(json.loads(os.environ.get("FAKE_STATUS_FIELDS", "{}")))
    json.dump(st, open(os.path.join(a.output, "status.json"), "w")); print(json.dumps(st)); sys.exit(0)
reason = {"nocred": "credential_missing", "notfree": "free_status_unknown", "quota": "quota_exhausted", "auth": "authentication_failed", "fmt": "validation_failed",
          "timeout": "timeout", "weird": "weird_reason", "verify": "verification_required", "ratelimit": "rate_limited", "freetier": "free_tier_rejected", "toolreject": "tool_use_rejected", "paid": "payment_required", "exhausted": "credits_exhausted", "not_free": "not_free", "payment_text": "provider_failed", "failedcredits": "provider_failed", "quota_text": "quota_exhausted", "rate_text": "rate_limited"}.get(mode, mode)
if mode == 'payment_text':
    print('402 Payment Required', file=sys.stderr)
if mode == 'quota_text':
    print('You exceeded your current quota, please check your plan and billing details', file=sys.stderr)
if mode == 'rate_text':
    print('rate limit; quota exhausted', file=sys.stderr)
if os.environ.get("FAKE_AU_ERR"):
    print(os.environ["FAKE_AU_ERR"], file=sys.stderr)
st = {"protocol_version": 1, "state": "failed", "accepted": False, "reason": "all_providers_failed", "attempts": [dict(att, state="failed", reason=reason)]}
json.dump(st, open(os.path.join(a.output, "status.json"), "w")); print("agent-use: " + prov + " failed (" + reason + ")", file=sys.stderr); sys.exit(1)
'''


def fake_agent_use(d: Path, errands=None) -> Path:
    home = d / "agent-use"
    (home / "scripts").mkdir(parents=True, exist_ok=True)
    (home / "scripts" / "run_agent.py").write_text(FAKE_AGENT_USE)
    if errands is not None:
        (home / "config").mkdir(exist_ok=True)
        (home / "config/errands.json").write_text(json.dumps({"version": 1, "providers": {
            n: {"argv": ["{python}", "{repo}/scripts/" + n + "_errand.py"]} for n in errands}}))
    return home


# Adapter contracts use a machine-local fixture, never public defaults.
LEGACY_LOCAL = {'seats': {'qoder': {'kind': 'agent_use',
                     'free_guard': True,
                     'provider': 'qoder',
                     'model': 'free-model-q',
                     'require_no_tools': True,
                     'free_only': True},
           'opencode': {'kind': 'agent_use',
                        'free_guard': True,
                        'provider': 'opencode',
                        'model': '',
                        'require_no_tools': False,
                        'free_only': True},
           'workbuddy': {'kind': 'agent_use',
                         'free_guard': True,
                         'provider': 'workbuddy',
                         'model': 'free-model-a',
                         'models': ['free-model-a', 'free-model-b', 'free-model-c'],
                         'require_no_tools': True,
                         'free_only': True},
           'workbuddy-hy4': {'kind': 'agent_use',
                             'free_guard': True,
                             'provider': 'workbuddy',
                             'model': 'free-model-b',
                             'models': ['free-model-b'],
                             'require_no_tools': True,
                             'free_only': True},
           'antigravity': {'kind': 'agent_use',
                           'free_guard': True,
                           'provider': 'antigravity',
                           'model': '',
                           'require_no_tools': False},
           'doubao': {'kind': 'agent_use',
                      'free_guard': True,
                      'provider': 'doubao',
                      'model': 'doubao-fast',
                      'require_no_tools': False,
                      'free_only': True,
                      'max_concurrency': 1},
           'workbuddy-cn': {'kind': 'agent_use',
                            'free_guard': True,
                            'provider': 'workbuddy-cn',
                            'model': 'free-model-b',
                            'models': ['free-model-b'],
                            'require_no_tools': True,
                            'free_only': True}},
 'fallback': {'workbuddy': ['qoder', 'opencode'],
              'workbuddy-hy4': ['workbuddy', 'qoder', 'opencode'],
              'qoder': ['workbuddy', 'opencode'],
              'antigravity': ['workbuddy', 'qoder', 'opencode'],
              'doubao': ['workbuddy', 'qoder', 'opencode'],
              'opencode': ['workbuddy', 'qoder'],
              'workbuddy-cn': ['workbuddy', 'qoder', 'opencode']},
 'panel_default': ['workbuddy', 'workbuddy-hy4', 'qoder', 'opencode', 'codex-sol', 'claude'],
 'panel_free': ['workbuddy', 'workbuddy-hy4', 'qoder', 'opencode'],
 'map_default': 'workbuddy'}

for _seat in LEGACY_LOCAL["seats"].values():
    _seat["login_hint"] = "请按适配器官方说明登录"
LEGACY_LOCAL["seats"]["doubao"]["login_hint"] = "请在豆包浏览器完成验证并登录"

def legacy_config():
    from seats_config import load_seats
    return load_seats(REPO)

def write_legacy_local(path, override):
    from seats_config import merge
    path.write_text(json.dumps(merge(LEGACY_LOCAL, override)))


def fake_home(tmp: Path) -> tuple[Path, dict]:
    # 模拟 macOS：$TMPDIR 在 /var/folders，而 /var 是 /private/var 的软链。
    # HOME 走软链路径，真实目录在别处，install.sh 必须把两者认成同一个目录。
    real = tmp / "private" / "home"
    real.mkdir(parents=True)
    (tmp / "var").symlink_to(tmp / "private")
    home = tmp / "var" / "home"
    dest = home / ".claude" / "aiw"
    shutil.copytree(REPO, dest, ignore=shutil.ignore_patterns("state", "snapshots", "__pycache__", ".git", ".tmp-*", ".durations.json"))
    env = dict(os.environ, HOME=str(home), PATH=f"{fake_bins(tmp)}:{os.environ['PATH']}", SHELL="/bin/zsh",
               AGENT_USE_HOME=str(fake_agent_use(tmp)))
    env["AIW_STATE"] = str(tmp / "state")
    return home, env


def check(cond: bool, msg: str):
    if not cond:
        raise AssertionError(msg)


def worker_call(t, **extra):
    pf = t / 'prompt.md'; pf.write_text('测试')
    wd = t / 'work dir'; wd.mkdir(exist_ok=True)
    env = dict(os.environ, **extra)
    return [sys.executable, str(REPO / 'ai_worker.py'), 'qoder', str(wd), '10', str(pf), '--ro'], env, wd



def codex_preflight_case(mode):
    for seat in ('codex', 'codex-sol'):
        with tempfile.TemporaryDirectory() as td:
            t = Path(td); b = fake_bins(t); marker = t / 'codex-called'
            home = t / 'home'; home.mkdir()
            if mode not in ('runtime', 'missing_config'):
                (home / '.codex').mkdir()
                (home / '.codex/config.toml').write_text('cred-tool' if mode != 'unrelated_config' else 'official')
            if mode != 'missing_binary':
                (b / 'cred-tool').write_text('#!/bin/sh\n[ "$1" = unlocked ] || exit 99\necho preflight-stdout\necho "vault is locked" >&2\nexit ' + ('0' if mode == 'ok' else '1') + '\n')
                (b / 'cred-tool').chmod(0o755)
            (b / 'codex').write_text('#!/bin/sh\n[ "$1" = --version ] && { echo fake; exit 0; }\necho called > "$FAKE_CODEX_ARGS"\n' + ('echo "pinentry error" >&2; exit 1\n' if mode == 'runtime' else 'echo "codex ok"\n'))
            env = dict(os.environ, HOME=str(home), PATH=f'{b}:/usr/bin:/bin',
                       AIW_PYTHON=sys.executable, AIW_STATE=str(t / 'state'),
                       AGENT_USE_HOME=str(fake_agent_use(t)), FAKE_CODEX_ARGS=str(marker))
            spec = {'argv': ['cred-tool', 'unlocked'], 'config_file': '~/.codex/config.toml', 'config_mentions': 'cred-tool'}
            custom_hint = '请在自己的终端运行 cred-tool unlock，再重派'
            if mode == 'unlock_hint':
                spec['unlock_hint'] = custom_hint
            local = t / 'seats.local.json'
            write_legacy_local(local, {'seats': {name: {'preflight': spec} for name in ('codex', 'codex-sol')}})
            env['AIW_CONFIG'] = str(local)
            pf = t / 'prompt'; pf.write_text('只回复 OK')
            p = inprocess_sh([sys.executable, str(REPO / 'ai_worker.py'), seat, str(t), '30', str(pf), '--ro'], env=env)
            locked = mode in ('locked', 'runtime', 'unlock_hint')
            check(p.returncode == (1 if locked else 0), f'{mode}: {p.stdout}{p.stderr}')
            check(marker.exists() == (mode not in ('locked', 'unlock_hint')), f'{mode}: codex 启动状态错误')
            br = t / 'state/breaker.json'
            check(not br.exists() or seat not in json.loads(br.read_text()), '库锁不应熔断')
            rows = [json.loads(x) for x in (t / 'state/seat-ledger.jsonl').read_text().splitlines()]
            check(len(rows) == 1 and rows[0]['actual'] == seat and rows[0]['kind'] == ('locked' if locked else 'ok'), '库锁账本分类或座位错误')
            if locked:
                hint = custom_hint if mode == 'unlock_hint' else '凭据库锁着：请在你自己的终端解锁所用的凭据工具（pinentry 需要 TTY，不能用 ! 前缀），解锁后重派'
                check(hint in p.stderr and f'[{seat}]' in p.stderr and '不熔断、不顶替' in p.stderr, p.stderr)
            else:
                check(latest_provider(t / "state").read_text().strip() == seat, '实际执行方错误')
            if mode in ('locked', 'unlock_hint'):
                check('preflight-stdout' in p.stderr and 'vault is locked' in p.stderr, '预检输出丢失')
                if seat == 'codex-sol':
                    continue
                p = inprocess_sh([sys.executable, str(REPO / 'ai_worker.py'), 'doctor'], env=env)
                doctor_hint = custom_hint if mode == 'unlock_hint' else '🔒 凭据库锁着：在自己终端解锁凭据工具'
                check(p.returncode == 0 and p.stdout.count(doctor_hint) == 2, p.stdout + p.stderr)



def goal_file(t, tasks):
    # Keep verify commands inside the fixture even when TMPDIR is in a worktree.
    r = sh(['git', 'init', '-q', str(t)])
    check(r.returncode == 0, r.stderr)
    g = t / 'goal.json'
    g.write_text(json.dumps(dict(goal='测试', tasks=tasks, rounds=[], human_blockers=[], fuse=dict(max_rounds=25, stall_rounds=3))))
    return g



def task(id, owner='main', verify='false'):
    return dict(id=id, title=id, owner=owner, verify=verify, passes=False)



def token_worker(t, stderr, seat='codex', script=None):
    err = t / 'stderr.txt'; err.write_text(stderr)
    cmd, env, wd = worker_call(t, HOME=str(t), CODEX_HOME=str(t / '.codex'),
                               AIW_STATE=str(t / 'state'), FAKE_CODEX='tokens',
                               FAKE_CODEX_STDERR=str(err))
    cmd[2] = seat
    cmd += ['--no-fallback', '--no-findings']
    if script:
        cmd = [sys.executable, '-c', script, *cmd[2:]]
    result = (sh if script else inprocess_sh)(cmd, env=env, cwd=REPO)
    row = json.loads((t / 'state/seat-ledger.jsonl').read_text().splitlines()[-1])
    return result, row



def token_rollout(t):
    sid = '00000000-0000-0000-0000-000000000001'
    path = t / '.codex/sessions/2020/01/01' / f'rollout-test-{sid}.jsonl'
    path.parent.mkdir(parents=True, exist_ok=True)
    return sid, path



def weekly_repo_fixture(t):
    import datetime as dt
    row = dict(ts=dt.datetime.now().astimezone().isoformat(), host='fake', seat='codex',
               actual='codex', rc=0, kind='ok', secs=2, effort='high', via='direct')
    rows = [dict(row, repo='owner/a', workdir=None, changed_files=1),
            dict(row, repo='owner/a', secs=4, changed_files=4, substituted=True),
            dict(row, repo='owner/a', rc=0, kind='timeout', changed_files=8, via='panel'),
            dict(row, repo='owner/b', rc=1, kind='auth', changed_files=True),
            dict(row, repo=None, workdir='/tmp/local', secs=0, changed_files=0),
            dict(row, rc=1, kind='format'),
            dict(row, repo='excluded', freecheck=True),
            dict(row, repo='excluded', kind='skipped_disabled', rc=1),
            dict(row, repo='excluded', kind='skipped_breaker', rc=None)]
    (t / 'seat-ledger.jsonl').write_text('\n'.join(json.dumps(r) for r in rows) + '\n{bad\n')
    (t / 'router-log.jsonl').write_text(json.dumps(dict(ts=row['ts'], project='test', rule='test', escape=False)) + '\n')
    return rows



def signal_window(mode):
    import signal
    import time
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        cmd, env, _ = worker_call(t, AIW_STATE=str(t / 'state'),
                                  FAKE_CODEX='partial_timeout' if mode in ('startup', 'timeout') else 'ok')
        cmd[2], cmd[4] = 'codex', '1'
        wrapper, pidfile = t / 'wrapper.py', t / 'child.pid'
        # 就绪文件只在 os.write 完成后发布；不以 CLI 启动耗时决定信号窗口。
        ready = t / 'output.ready'
        env['SIGNAL_OUTPUT_READY'] = str(ready)
        bins = fake_bins(t)
        env['PATH'] = f"{bins}:{env['PATH']}"
        codex = bins / 'codex'
        output = ('os.write(1, b"before timeout\\n")\n'
                  'os.write(2, b"timeout stderr\\n")\n'
                  if mode in ('startup', 'timeout') else
                  'os.write(1, b"codex ok\\n")\n')
        codex.write_text('#!' + sys.executable + '\n' +
                         'import os, signal\nfrom pathlib import Path\n' + output +
                         'Path(os.environ["SIGNAL_OUTPUT_READY"]).touch()\n' +
                         ('while True: signal.pause()\n' if mode in ('startup', 'timeout') else ''))
        wrapper.write_text('''import os, signal, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv.pop(1))
import ai_worker as w
mode = sys.argv.pop(1)
pidfile = Path(sys.argv.pop(1))
original_popen = w.subprocess.Popen
def output_ready(p):
    ready = Path(os.environ['SIGNAL_OUTPUT_READY'])
    deadline = time.monotonic() + 120
    while not ready.exists():
        assert p.poll() is None or ready.exists(), '假 CLI 就绪前退出'
        assert time.monotonic() < deadline, '假 CLI 输出就绪超时'
        time.sleep(.02)
    assert ready.exists(), '假 CLI 输出未就绪'
def popen(*a, **kw):
    p = original_popen(*a, **kw)
    if a[0][:2] == [sys.executable, '-c'] and a[0][2] == w.WATCHDOG_CODE:
        return p
    pidfile.write_text(str(p.pid))
    if mode != 'startup':
        original_communicate = p.communicate
        first = True
        def communicate(*args, **kwargs):
            nonlocal first
            if first:
                first = False
                output_ready(p)
                # timeout 仍由真实 communicate 抛出，走生产清理及输出捕获。
                # 其余模式等待真实 communicate 收齐输出，再进入归档/账本钩子；
                # 上限只防挂死，不让原来的 1 秒超时抢先改变测试窗口。
                kwargs['timeout'] = 0 if mode == 'timeout' else 120
            return original_communicate(*args, **kwargs)
        p.communicate = communicate
    if mode == 'startup':
        assert w.DEFERRED_DEPTH > 0, '启动窗口未延迟信号'
        output_ready(p)
        os.kill(os.getpid(), signal.SIGTERM)
    return p
w.subprocess.Popen = popen
original_killpg = w.os.killpg
sent = False
def killpg(*a):
    global sent
    if mode == 'timeout' and not sent:
        sent = True
        os.kill(os.getpid(), signal.SIGINT)
    return original_killpg(*a)
w.os.killpg = killpg
original_write = w.write_run_file
def write(path, *a):
    if mode == 'archive' and path.name == 'output.md': os.kill(os.getpid(), signal.SIGTERM)
    return original_write(path, *a)
w.write_run_file = write
original_ledger = w.ledger
def ledger(row):
    if mode == 'ledger': os.kill(os.getpid(), signal.SIGTERM)
    return original_ledger(row)
w.ledger = ledger
if mode == 'lock':
    original_flock = w.fcntl.flock
    def flock(fd, flags):
        if flags & w.fcntl.LOCK_NB: raise OSError('模拟拿锁失败')
        return original_flock(fd, flags)
    w.fcntl.flock = flock
    original_unlink = Path.unlink
    def unlink(path, *a, **kw):
        if path.suffix == '.lock': raise OSError('模拟删除失败')
        return original_unlink(path, *a, **kw)
    Path.unlink = unlink
    original_save = w.save_json
    def save(path, data):
        assert path.parent.name != 'inflight', '锁删不掉仍发布 inflight'
        return original_save(path, data)
    w.save_json = save
sys.exit(w.main())
''')
        # 用文件接输出，变异遗留的子进程继承管道也不能阻塞守护断言。
        with (t / 'log').open('w+') as log:
            proc = subprocess.Popen([sys.executable, str(wrapper), str(REPO), mode, str(pidfile), *cmd[2:]],
                                    env=env, stdout=log, stderr=log)
            try:
                proc.wait(timeout=120)
                log.seek(0)
                err = log.read()
                expected = 0 if mode == 'lock' else 130 if mode == 'timeout' else 124
                check(proc.returncode == expected, ('启动窗口未延迟信号: ' if mode == 'startup' else '') + f'{mode} 退出码错误: {proc.returncode} {err}')
                if mode in ('startup', 'timeout'):
                    pid = int(pidfile.read_text())
                    def group_exited():
                        try:
                            os.killpg(pid, 0)
                        except (ProcessLookupError, PermissionError):
                            return True
                        return False
                    wait_until(group_exited, '启动窗口遗留子进程组' if mode == 'startup' else '超时清理遗留子进程组')
                rows = [json.loads(x) for x in (t / 'state/seat-ledger.jsonl').read_text().splitlines()]
                check(len(rows) == 1, f'{mode} 账本不是恰好一行: {rows}')
                check(rows[0]['kind'] == ('ok' if mode in ('ledger', 'lock') else 'interrupted'), f'{mode} 账本分类错误: {rows}')
                runs = list((t / 'state/runs').iterdir())
                check(len(runs) == 1, f'{mode} 创建了多个 run 目录')
                text = (runs[0] / 'output.md').read_text()
                check(text == ('before timeout\n' if mode in ('startup', 'timeout') else 'codex ok\n'), f'{mode} 真实输出丢失: {text!r}')
                if mode == 'lock':
                    check(not list((t / 'state/inflight').glob('*.json')), '锁删不掉留下 inflight')
                    check(err.count('警告：') == 1, '锁删不掉应只警告一次: ' + err)
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()
                if pidfile.exists():
                    try:
                        os.killpg(int(pidfile.read_text()), signal.SIGKILL)
                    except (ProcessLookupError, PermissionError):
                        pass



def free_guard_fixture(t):
    env = dict(os.environ, AIW_STATE=str(t / 'state'),
               AGENT_USE_HOME=str(fake_agent_use(t)), DISCORD_TOKEN='test-only-token',
               AIW_DISCORD_CHANNEL='test-channel', FAKE_AU_LOG=str(t / 'calls'),
               FAKE_DISCORD_LOG=str(t / 'discord'))
    binary = t / 'discord-use'
    binary.write_text('''#!/usr/bin/env python3
import json, os, sys
with open(os.environ['FAKE_DISCORD_LOG'], 'a') as f:
    f.write(json.dumps({'argv': sys.argv[1:], 'has_token': os.environ.get('DISCORD_TOKEN') == 'test-only-token', 'no_update': os.environ.get('DISCORD_USE_NO_UPDATE_CHECK')}) + '\\n')
if os.environ.get('FAKE_DISCORD_FAIL'):
    print('401 ' + os.environ.get('DISCORD_TOKEN', ''), file=sys.stderr)
    sys.exit(1)
''')
    binary.chmod(0o755)
    env['AIW_DISCORD_USE_BIN'] = str(binary)
    return env



def permodel_run(t, env, seat='workbuddy', **extra):
    prompt = t / 'prompt'; prompt.write_text('test')
    return inprocess_sh([sys.executable, str(REPO / 'ai_worker.py'), seat, str(t), '10', str(prompt), '--ro'], env=dict(env, **extra))



def permodel_state(t):
    path = t / 'state/disabled.json'
    return json.loads(path.read_text()) if path.exists() else {}



def errand_fixture(t):
    home = fake_agent_use(t, ['cue', 'future-errand'])
    (home / 'scripts/errand_use.py').write_text(
        "import json, sys\nprint(json.dumps(sys.argv[1:]))\nsys.exit(20)\n")
    return dict(os.environ, AGENT_USE_HOME=str(home), AGENT_USE_ERRAND_HOME=str(home), AIW_STATE=str(t / 'state'))



def errand_route_test(runtime):
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); env = errand_fixture(t)
        for name in ('route.py', 'ai_worker.py', 'findings.py', 'harness_ids.py', 'gitenv.py', 'env_compat.py', 'seats_config.py', 'seats.json'):
            shutil.copy(REPO / name, t / name)
        rule = {'id': 'errand-test', 'seat': 'cue', 'patterns': ['跑腿'], 'instruction': '执行任务'}
        for update in ({'seat': 'cue'}, {'seat': 'future-errand'},
                       {'seat': 'qoder', 'instruction': 'aiw cue .'},
                       {'seat': 'panel', 'instruction': 'aiw panel . 10 F cue'},
                       {'seat': 'qoder', 'instruction_codex': 'aiw errand submit'}):
            data = dict(rule, **update)
            (t / 'router-rules.json').write_text(json.dumps({'rules': [data]}))
            args = ['跑腿'] if runtime else ['--check']
            r = inprocess_sh([sys.executable, str(t / 'route.py'), *args], cwd=t, env=env)
            check(r.returncode == 1 and 'aiw errand' in r.stderr and not r.stdout,
                  'errand 路由未拒绝: ' + repr(r))
            if runtime:
                r = inprocess_sh([sys.executable, str(t / 'route.py'), '--hook'], cwd=t, env=env,
                       input=json.dumps({'prompt': '跑腿'}))
                check(r.returncode == 0 and not r.stdout and 'aiw errand' in r.stderr,
                      'errand 钩子注入了指令')



def complete_config_json(serialized):
    from seats_config import load_public, merge
    public = load_public(REPO)
    public["findings"] = {}  # Preserve tests of missing findings options.
    return json.dumps(merge(public, json.loads(serialized)))



def findings_fixture(root):
    import importlib.util
    spec = importlib.util.spec_from_file_location('findings_test', REPO / 'findings.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.HERE = root / 'harness'
    module.HERE.mkdir()
    module.STATE = root / 'state'
    (module.HERE / 'seats.json').write_text(complete_config_json(json.dumps({
        'findings': {'max_items': 2, 'daily_cap': 2},
        'seats': {'text': {'kind': 'agent_use', 'provider': 'test'}}})))
    return module



FINDINGS_BLOCK = ("<<<AIW-FINDINGS\nguess: 无\ndeviation: 无\nabandoned: 无\nfixed: 修复了测试\n"
                  "- observation: 现象一 | expected: 预期 | evidence: test.py:1\n"
                  "- observation: 现象二 | expected: 预期 | location: test.py:main\nAIW-FINDINGS>>>")



def findings_worker_fixture(root, collect=True):
    home, env = fake_home(root)
    harness = home / '.claude/aiw'
    config = json.loads((harness / 'seats.json').read_text())
    config['findings'] = {'collect': collect}
    local = root / 'findings-local.json'
    write_legacy_local(local, {'fallback': {'codex': ['workbuddy']}})
    env['AIW_CONFIG'] = str(local)
    # 所有配置变更仅在假 HOME 副本内。
    (harness / 'seats.json').write_text(json.dumps(config))
    work = root / 'work'; work.mkdir()
    prompt = root / 'prompt'; prompt.write_text('检查本地代码')
    output = root / 'output'; output.write_text('正文\n' + FINDINGS_BLOCK + '\n')
    launcher = harness / 'ai_worker.py'
    env.update(FAKE_CODEX='findings', FAKE_FINDINGS_OUTPUT=str(output))
    for key in ('AIW_PANEL_DIR', 'AIW_MAP_DIR'):
        env.pop(key, None)
    def run(seat='codex', flags=('--ro', '--no-fallback'), **overrides):
        result = inprocess_sh([sys.executable, str(launcher), seat, str(work), '10', str(prompt), *flags],
                    env=dict(env, **overrides))
        ledger_path = root / 'state/seat-ledger.jsonl'
        row = json.loads(ledger_path.read_text().splitlines()[-1])
        record_path = root / 'state/findings/records.jsonl'
        records = [json.loads(line) for line in record_path.read_text().splitlines()] if record_path.exists() else []
        return result, row, records
    return run, work, prompt, output, harness









def findings_append(folder, *rows):
    with (folder / 'records.jsonl').open('a') as stream:
        for row in rows:
            stream.write(json.dumps(row) + '\n')





















def codex_home_fixture(root):
    source = root / 'source'
    source.mkdir()
    (source / 'config.toml').write_text(r'''
model_provider = "X"
disable_response_storage = true
default_trust_level = "trusted"
notify = ["fake-notify"]
[hooks]
command = "fake-hook"
[mcp_servers.fake]
command = "fake-mcp"
[shell_environment_policy]
inherit = "all"
[skills]
include_instructions = true
[plugins]
[projects]
[model_providers.other]
name = "not-selected"
[model_providers.X]
name = "fake-provider"
base_url = "https://fake.invalid"
wire_api = "responses"
requires_openai_auth = true
number = 42
fraction = 1.25
[model_providers.X.auth]
command = "fake-auth"
args = ["quote\"", "back\\slash", "line\nbreak", "😀", "\u007F"]
[model_providers.X.auth.extra]
enabled = false
''')
    (source / 'auth.json').write_text('{"OPENAI_API_KEY":"fake-test-only"}')
    return source



def codex_home_run_case(mode):
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        source = codex_home_fixture(root)
        if mode == 'fallback':
            configs = ['broken = [', 'model_provider = "X"', '', None]
        else:
            configs = [(source / 'config.toml').read_text()]
        cli = root / 'fake-cli'
        cli.write_text('#!' + sys.executable + '\nimport json, os, sys\n'
                       'print(json.dumps({"argv":sys.argv[1:], "home":os.environ.get("CODEX_HOME"), "nested":os.environ.get("AIW_NESTED")}))\n')
        cli.chmod(0o755)
        for content in configs:
            if content is None:
                (source / 'config.toml').unlink()
            else:
                (source / 'config.toml').write_text(content)
            seats = ('codex', 'codex-sol') if mode in ('normal', 'fallback', 'shared') else ('claude',)
            for seat in seats:
                cmd, env, _ = worker_call(root, CODEX_BIN=str(cli), CLAUDE_BIN=str(cli), AIW_STATE=str(root / 'state'))
                env.pop('CODEX_HOME', None)
                env.pop('AIW_CODEX_SHARED_HOME', None)
                env['HOME'] = str(root)
                if mode == 'shared':
                    env['AIW_CODEX_SHARED_HOME'] = '1'
                else:
                    env['CODEX_HOME'] = str(source)
                if mode == 'claude':
                    env.pop('CODEX_HOME', None)
                cmd[2] = seat
                r = inprocess_sh(cmd + ['--no-findings'], env=env)
                check(r.returncode == 0, f'{seat} 未完成: {r.stderr}')
                record = json.loads(r.stdout)
                check(record['nested'] == '1', 'AIW_NESTED 丢失')
                isolated = mode == 'normal'
                check(('skills.include_instructions=false' in record['argv']) == isolated, 'skills 参数错误')
                expected = str((root / 'state/codex-home').resolve()) if isolated else (str(source) if mode == 'fallback' else None)
                check(record['home'] == expected, 'CODEX_HOME 环境错误')
                check(('执行方独立 CODEX_HOME 不可用，回退共享配置：' in r.stderr) == (mode == 'fallback'), '回退说明错误')
                if mode == 'shared':
                    check(not (root / 'state/codex-home').exists(), '关闭时不应生成 home')


@contextlib.contextmanager
def isolated_call(env=None, cwd=None):
    """Restore process globals even when a CLI raises SystemExit."""
    import signal
    saved_env, saved_cwd, saved_argv = dict(os.environ), os.getcwd(), sys.argv
    saved_path = sys.path[:]
    signals = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
    # Harness modules bind configuration and paths at import time.
    names = {p.stem for p in REPO.glob('*.py')} | {'__main__'}
    saved_modules = {n: sys.modules.pop(n) for n in names if n in sys.modules}
    try:
        if env is not None:
            os.environ.clear(); os.environ.update(env)
        if cwd is not None:
            os.chdir(cwd)
        yield
    finally:
        os.chdir(saved_cwd)
        os.environ.clear(); os.environ.update(saved_env)
        sys.argv = saved_argv
        sys.path[:] = saved_path
        for s, handler in signals.items():
            signal.signal(s, handler)
        for n in names:
            sys.modules.pop(n, None)
        sys.modules.update(saved_modules)


def inprocess_sh(cmd, env=None, cwd=None, input=None):
    """Opt-in Python CLI calls; external tools and shell boundaries stay real."""
    import contextlib, io, runpy
    if (not isinstance(cmd, (list, tuple)) or len(cmd) < 2
            or cmd[0] != sys.executable or (cmd[1] != '-c' and not str(cmd[1]).endswith('.py'))):
        return sh(cmd, env=env, cwd=cwd, input=input)
    out, err = io.StringIO(), io.StringIO()
    with isolated_call(env, cwd), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        saved_stdin = sys.stdin
        sys.argv = [str(x) for x in cmd[1:]]
        sys.path.insert(0, str(Path.cwd() if cmd[1] == '-c' else Path(cmd[1]).resolve().parent))
        if cmd[1] == '-c':
            sys.argv = ['-c', *map(str, cmd[3:])]
        try:
            if input is not None:
                sys.stdin = io.StringIO(input)
            try:
                if cmd[1] == '-c':
                    exec(compile(cmd[2], '<string>', 'exec'), {'__name__': '__main__'})
                else:
                    runpy.run_path(str(cmd[1]), run_name='__main__')
                rc = 0
            except SystemExit as exc:
                rc = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
                if exc.code is not None and not isinstance(exc.code, int):
                    print(exc.code, file=sys.stderr)
            except Exception:
                import traceback
                traceback.print_exc()
                rc = 1
        finally:
            sys.stdin = saved_stdin
    return subprocess.CompletedProcess(cmd, rc, out.getvalue(), err.getvalue())


_git_templates = None

def git_template(destination, files=None, *, bare=False, empty=False):
    """Copy a session-local committed fixture; no user config or hooks."""
    global _git_templates
    import hashlib
    if _git_templates is None:
        _git_templates = tempfile.TemporaryDirectory(prefix='aiw-git-templates-')
    files = files or {}
    key = hashlib.sha256(repr((sorted(files.items()), bare, empty)).encode()).hexdigest()
    template = Path(_git_templates.name) / key
    if not template.exists():
        template.mkdir()
        from gitenv import repository_git_env
        env = dict(repository_git_env(), GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@t',
                   GIT_COMMITTER_NAME='t', GIT_COMMITTER_EMAIL='t@t')
        command = ['git', 'init', '--template=', '-q', '-b', 'main']
        check(sh([*command, *(['--bare'] if bare else []), str(template)], env=env).returncode == 0, 'template init')
        metadata = template if bare else template / '.git'
        (metadata / 'config').write_text('[core]\n repositoryformatversion = 0\n bare = ' + str(bare).lower() + '\n filemode = true\n[user]\n name = t\n email = t@t\n')
        (metadata / 'hooks').mkdir(exist_ok=True)
        for name, content in files.items():
            path = template / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(content)
        if not empty:
            git_history(template, [('initial', files, '2026-10-13T00:00:00+07:00')], checkout=not bare, bare=bare)
    shutil.copytree(template, destination, dirs_exist_ok=True)
    return destination


def git_history(repo, commits, *, checkout=True, bare=False, append=False):
    """Import fixture history in one process; contents, dates and messages stay real."""
    import datetime as dt
    chunks = []
    for index, (message, files, stamp) in enumerate(commits):
        date = dt.datetime.fromisoformat(stamp)
        epoch = int(date.timestamp())
        zone = date.strftime('%z')
        body = message.encode()
        chunks.append(f'commit refs/heads/main\nmark :{index+1}\ncommitter t <t@t> {epoch} {zone}\ndata {len(body)}\n'.encode() + body + b'\n')
        if index:
            chunks.append(f'from :{index}\n'.encode())
        elif append:
            chunks.append(b'from refs/heads/main^0\n')
        for name, value in files.items():
            content = value.encode()
            chunks.append(f'M 100644 inline {json.dumps(name)}\ndata {len(content)}\n'.encode() + content + b'\n')
        chunks.append(b'\n')
    from gitenv import repository_git_env
    result = subprocess.run(['git', 'fast-import', '--quiet'], cwd=repo,
                            env=repository_git_env(), input=b''.join(chunks), capture_output=True)
    check(result.returncode == 0, result.stderr.decode())
    if checkout and not bare:
        result = sh(['git', 'reset', '--hard', '-q', 'main'], cwd=repo, env=repository_git_env())
        check(result.returncode == 0, result.stderr)



def install_phase(repo, index, args, env):
    """Run the unchanged installer Python block after a real shell installation."""
    import re
    blocks = re.findall(r"<<'PYEOF'\n(.*?)\nPYEOF", (repo / 'install.sh').read_text(), re.S)
    return inprocess_sh([sys.executable, '-c', blocks[index], *map(str, args)], env=env, cwd=repo)
