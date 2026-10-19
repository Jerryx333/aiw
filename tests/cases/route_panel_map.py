"""route panel map regression cases."""
from __future__ import annotations

from support import (
    FAKE_AGENT_USE,
    Path,
    REPO,
    check,
    fake_agent_use,
    fake_bins,
    fake_home,
    json,
    legacy_config,
    os,
    inprocess_sh as sh,
    shutil,
    subprocess,
    sys,
    tempfile,
    wait_until,
    worker_call,
    write_legacy_local,
)


def route_textwork():
    import importlib.util
    spec = importlib.util.spec_from_file_location('route_textwork_module', REPO / 'route.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    examples = {
        'draft': ['帮我写个 commit message', '起草一下这个 PR 的描述', '更新一下变更记录',
                  '给这个函数起个名', '写 PR 正文', '写合并请求说明', '更新 changelog',
                  '写 release notes', '写发布说明', '给产品起名', '提供命名建议',
                  '取个名', '改写这段', '缩写这篇', '扩写文案', '精简一下这段',
                  '把这段文案精简一下', '这句话帮我润色一下'],
        'review': ['看下 PR 描述有没有问题', '看看这个函数对不对', '检查一下这个方案行不行'],
        'longread': ['总结一下这份日志', '帮我摘要这篇文章', '概括这个文件', '提炼输出'],
        'snippet': ['列一下这个函数的边界情况', '写个正则匹配手机号', '列出测试用例清单',
                    '想几个用例清单', '写个 SQL 查询', '写个 shell 命令', '写个小脚本示例'],
        'logs': ['翻一下日志看看为什么失败', '看下这个日志有没有问题', '帮我看看这个日志对不对'],
        'bulk': ['补测试', '写测试', '给 scripts 下的脚本加单元测试',
                 '批量改写文案并更新所有文件的 changelog',
                 '批量给所有文件加测试，并列出边界情况'],
        None: ['总结一下我们刚才的讨论', '总结一下', '想几个名字', '写个函数'],
    }
    seats = {'draft': 'draft', 'longread': 'draft', 'snippet': 'draft',
             'logs': 'review', 'bulk': 'write', 'review': 'review'}
    with tempfile.TemporaryDirectory() as t:
        for main in (None, 'codex'):
            result = module.route('总结这份报告，结合我们刚才讨论的决定', Path(t), main=main)
            check(result['rule'] not in ('draft', 'longread', 'snippet'), str(result))
            for expected, messages in examples.items():
                for msg in messages:
                    result = module.route(msg, Path(t), main=main)
                    check(result['rule'] == expected, f'{msg!r}: 应命中 {expected}: {result}')
                    if expected:
                        check(result['seat'] == seats[expected], f'座位错误: {result}')
                        check(result['instruction'].startswith('本条判定为【'),
                              f'路由正文缺失: {result}')



def route_override():
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        env = dict(os.environ, AIW_STATE=str(t / "state"))
        proj = t / "proj"
        (proj / ".claude").mkdir(parents=True)
        (proj / "sub").mkdir()
        msg = "帮我批量给 scripts 补测试，顺便查一下问财"
        # 无项目规则：命中全局 bulk
        r = json.loads(sh([sys.executable, str(REPO / "route.py"), msg], env=env, cwd=t).stdout)
        check(r["rule"] == "bulk" and r["source"] == "global", f"全局应命中 bulk: {r}")
        # 有项目规则：项目规则优先，且在子目录里也能找到
        (proj / ".claude" / "router-rules.json").write_text(json.dumps({
            "escape": ["交给我"],
            "rules": [{"id": "research", "seat": "main", "patterns": ["问财"], "instruction": "项目规则"}],
            "disable": ["logs"]}, ensure_ascii=False))
        r = json.loads(sh([sys.executable, str(REPO / "route.py"), msg], env=env, cwd=proj / "sub").stdout)
        check(r["rule"] == "research" and r["source"] == "project" and r["project"] == "proj", f"项目规则应优先: {r}")
        # 项目逃生口
        r = json.loads(sh([sys.executable, str(REPO / "route.py"), "交给我 批量改"], env=env, cwd=proj).stdout)
        check(r["escape"] is True, f"项目逃生口应生效: {r}")
        # disable 关掉全局规则
        r = json.loads(sh([sys.executable, str(REPO / "route.py"), "帮我翻一下日志"], env=env, cwd=proj).stdout)
        check(r["rule"] != "logs", f"disable 应关掉 logs: {r}")
        # 钩子模式：用 payload 的 cwd，不用进程 cwd
        out = sh([sys.executable, str(REPO / "route.py"), "--hook"], env=env, cwd=t,
                 input=json.dumps({"prompt": msg, "cwd": str(proj)})).stdout
        check("项目规则" in out, f"钩子应按 payload.cwd 取项目规则: {out}")
        # Codex 钩子（payload 带 turn_id）：用 Codex 主会话版指令；会诊带 AIW_MAIN=codex、问 claude
        out = sh([sys.executable, str(REPO / "route.py"), "--hook"], env=env, cwd=t,
                 input=json.dumps({"prompt": "几家都看看这个方案", "cwd": str(t), "turn_id": "x", "model": "gpt"})).stdout
        check("AIW_MAIN=codex" in out and "panel_default" in out, f"Codex 钩子应注入 Codex 版会诊指令: {out}")
        out = sh([sys.executable, str(REPO / "route.py"), "--hook"], env=env, cwd=t,
                 input=json.dumps({"prompt": "几家都看看这个方案", "cwd": str(t)})).stdout
        check("AIW_MAIN=codex" not in out and "panel_default" in out, f"Claude 钩子应是 Claude 版指令: {out}")
        r = json.loads(sh([sys.executable, str(REPO / "route.py"), "--codex", "批量重命名所有文件"], env=env, cwd=t).stdout)
        check("你自己能改文件" in r["instruction"], f"Codex 主会话批量改动应自己做: {r}")
        # 被 aiw 外包出来的会话（AIW_NESTED）不再派单，防套娃
        p = sh([sys.executable, str(REPO / "route.py"), "--hook"], env=dict(env, AIW_NESTED="1"), cwd=t,
               input=json.dumps({"prompt": "几家都看看", "cwd": str(t)}))
        check(p.returncode == 0 and not p.stdout.strip(), "嵌套会话钩子应静默")
        # 损坏的项目规则不拖垮全局
        (proj / ".claude" / "router-rules.json").write_text("{bad json")
        r = json.loads(sh([sys.executable, str(REPO / "route.py"), msg], env=env, cwd=proj).stdout)
        check(r["rule"] == "bulk", f"项目规则损坏时应回落全局: {r}")
        # 钩子收到坏 stdin 也返回 0 且不输出
        p = sh([sys.executable, str(REPO / "route.py"), "--hook"], env=env, cwd=t, input="not json")
        check(p.returncode == 0 and not p.stdout.strip(), "钩子坏输入应静默放行")



def map_interrupt():
    import importlib.util
    import signal
    import time
    spec = importlib.util.spec_from_file_location('map_test_module', REPO / 'map.py')
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(REPO))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    module.SEATS = legacy_config()
    check(module.SEATS['fallback']['workbuddy'] == ['qoder', 'opencode'], 'workbuddy 顶替链变化')
    check(module.outer_timeout('workbuddy', 17, 9) == 3 * (17 + 30) + 9,
          '外层限时应覆盖三家和 agent-use 余量')
    started = time.monotonic()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with tempfile.TemporaryDirectory() as td:
            t = Path(td)
            au = fake_agent_use(t)
            pid_file = t / 'pids.json'
            (au / 'scripts/run_agent.py').write_text(
                'import json, os, time\n'
                'from pathlib import Path\n'
                'Path(os.environ["MAP_PID_FILE"]).write_text(json.dumps([os.getppid(), os.getpid()]))\n'
                'time.sleep(120)\n')
            pf = t / 'prompt.txt'; pf.write_text('检查附件')
            files = ['a.txt', 'b.txt', 'c.txt']
            for file in files:
                (t / file).write_text('data')
            env = dict(os.environ, AGENT_USE_HOME=str(au), MAP_PID_FILE=str(pid_file),
                       AIW_STATE=str(t / 'state'))
            p = subprocess.Popen([sys.executable, str(REPO / 'map.py'), str(t), '120',
                                  str(pf), *files, '--jobs', '1'], env=env,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            pids = []
            try:
                wait_until(lambda: pid_file.exists() or p.poll() is not None,
                           '第一单未启动', timeout=120)
                check(pid_file.exists(), '第一单未启动')
                pids = json.loads(pid_file.read_text())
                p.send_signal(sig)
                stdout, stderr = p.communicate(timeout=12)
                check(p.returncode == 128 + sig, f'{p.returncode}: {stdout} {stderr}')
                for pid in pids:
                    try:
                        os.kill(pid, 0)
                    except ProcessLookupError:
                        continue
                    raise AssertionError(f'中断后进程仍存在: {pid}')
                out, = (t / 'state/maps').iterdir()
                summary = json.loads((out / 'summary.json').read_text())
                check(str(out) in stdout, '中断后未打印目录')
                check([item['file'] for item in summary['items']] == files, str(summary))
                check(all(item['rc'] == 128 + sig for item in summary['items']), str(summary))
                check(all(item['provider'] is None for item in summary['items'][1:]), str(summary))
                check(not list(out.glob('02-*')) and not list(out.glob('03-*')), '排队项被启动')
            finally:
                if p.poll() is None:
                    p.kill()
                p.communicate()
                for pid in pids:
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
    check(time.monotonic() - started < 30, '中断用例超过 30 秒')



def map_batch():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); wd = t / 'work'; wd.mkdir()
        pf = t / 'prompt.txt'; pf.write_text('逐个检查这份附件')
        files = ['a.txt', '子目录/a.txt', 'c.txt']
        (wd / '子目录').mkdir()
        for index, file in enumerate(files):
            (wd / file).write_text(f'MAP_ITEM_{index}')
        au = fake_agent_use(t)
        script = au / 'scripts/run_agent.py'
        # 单个附件失败不会触发共享熔断；可只让首选失败来验证正常顶替。
        script.write_text(FAKE_AGENT_USE.replace('log = os.environ.get("FAKE_AU_LOG")',
            'if os.environ.get("FAKE_MAP_FAIL") in ("all", prov) and "MAP_ITEM_1" in open(a.task).read():\n'
            '    mode = "provider_failed"\n'
            'log = os.environ.get("FAKE_AU_LOG")'))
        env = dict(os.environ, AGENT_USE_HOME=str(au), AIW_STATE=str(t / 'state'))
        cmd = ['bash', str(REPO / 'bin/aiw'), 'map', str(wd), '30', str(pf)]
        ledger = t / 'state/seat-ledger.jsonl'

        def run(extra=(), changes=None):
            before = set((t / 'state/maps').glob('*'))
            r = sh(cmd + files + list(extra), env=dict(env, **(changes or {})))
            created = set((t / 'state/maps').glob('*')) - before
            check(len(created) == 1, f'map 目录缺失: {r.stderr}')
            out = created.pop()
            return r, out, json.loads((out / 'summary.json').read_text())

        r, out, summary = run()
        check(r.returncode == 0, r.stderr)
        check(set(summary) == {'dir', 'seat', 'jobs', 'items'} and summary['dir'] == str(out.resolve()), str(summary))
        check(summary['seat'] == 'workbuddy' and summary['jobs'] == 3, str(summary))
        check(summary['items'] == [dict(file=f, rc=0, provider='workbuddy', chars=len('workbuddy ok\n')) for f in files], str(summary))
        check(len(list(out.glob('[0-9][0-9]-*.md'))) == 3 and len(list(out.glob('*.provider'))) == 3, '输出文件缺失或同名覆盖')
        check((out / 'prompt.md').read_text() == pf.read_text(), 'prompt 快照不一致')
        check(not (wd / '.provider').exists(), '不应写共享 provider')
        rows = [json.loads(line) for line in ledger.read_text().splitlines()]
        check(len(rows) == 3 and all(row['via'] == 'map' for row in rows), str(rows))
        for row in rows:
            task = (Path(row['run_dir']) / 'task.txt').read_text()
            check(sum(f'MAP_ITEM_{i}' in task for i in range(3)) == 1, '每单必须恰有一个附件')

        r, out, summary = run(changes={'FAKE_MAP_FAIL': 'all'})
        check(r.returncode == 1 and summary['items'][1]['rc'] != 0, str(summary))
        check(summary['items'][0]['rc'] == summary['items'][2]['rc'] == 0, '失败项影响其他单')
        check(summary['items'][1]['provider'] is None, '失败不应伪报实际执行方')
        r, out, summary = run(changes={'FAKE_MAP_FAIL': 'workbuddy'})
        check(r.returncode == 0 and summary['items'][1]['provider'] == 'qoder', '正常顶替链未保留')

        (wd / '.env').write_text('fake test data')
        (t / 'outside.txt').write_text('outside')
        (wd / 'large.txt').write_text('x' * 200001)
        before = ledger.read_bytes()
        for bad in ('.env', '../outside.txt', 'large.txt'):
            r = sh(cmd + ['a.txt', bad], env=env)
            check(r.returncode == 64 and ledger.read_bytes() == before, f'坏附件竟派出单: {bad}: {r.stderr}')
        for extra in (['--jobs', '0'], ['--jobs', '7'], ['--jobs', 'x'], ['--unknown'],
                      ['--seat', 'missing'], ['--jobs']):
            r = sh(cmd + files + extra, env=env)
            check(r.returncode == 64 and ledger.read_bytes() == before, f'非法参数未拒绝: {extra}')
        for many in ([], ['a.txt'] * 51):
            check(sh(cmd + many, env=env).returncode == 64, '附件数量边界未校验')
        r = sh(cmd + files, env=dict(env, AIW_NESTED='1'))
        check(r.returncode == 64 and '不再嵌套派单' in r.stderr and ledger.read_bytes() == before, r.stderr)
        r, out, summary = run(['--seat', 'doubao', '--jobs', '3'])
        check(r.returncode == 0 and '并发' in r.stderr and 'jobs=1' in r.stderr and summary['jobs'] == 1, r.stderr)
        # 每家余量内的合法运行不能被旧 timeout + grace 截断。
        cmd[4] = '1'
        r, out, summary = run(changes={'FAKE_AU_WORKBUDDY': 'brief', 'AIW_MAP_GRACE': '0'})
        check(r.returncode == 0, str(summary))
        # 缩短外层函数的测试时钟，仍走真实 worker，验证超时归档和 124。
        cmd = [sys.executable, '-c',
               f'import sys; sys.path.insert(0, {str(REPO)!r}); import map; '
               'map.outer_timeout = lambda *args: 1; sys.exit(map.main())',
               str(wd), '1', str(pf)]
        r, out, summary = run(changes={'FAKE_AU_WORKBUDDY': 'brief', 'AIW_MAP_GRACE': '0'})
        check(r.returncode == 1 and all(item['rc'] == 124 for item in summary['items']), str(summary))
        check(len(list(out.glob('[0-9][0-9]-*.md'))) == 3 and
              all('外层超时' in f.read_text() for f in out.glob('*.stderr.txt')), '超时未归档')



def panel_compact_context():
    from ai_worker import PANEL_FORMAT, render_attachments
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        (t / 'code.txt').write_text('ATTACH_PANEL_MARKER')
        output = t / 'answer.txt'
        answer = '有效意见' + '中' * 3100 + 'HIDDEN_END'
        output.write_text(answer)
        env = dict(os.environ, AIW_STATE=str(t / 'state'),
                   AGENT_USE_HOME=str(fake_agent_use(t)), FAKE_FINDINGS_OUTPUT=str(output))
        cmd = [sys.executable, str(REPO / 'panel.py'), str(t), '10',
               '只列两条', 'qoder', 'codex-sol', '--attach', 'code.txt']
        for full in (False, True):
            before = set((t / 'state/runs').glob('*'))
            r = sh(cmd + (['--full'] if full else []), env=env)
            check(r.returncode == 0, r.stdout + r.stderr)
            runs = set((t / 'state/runs').iterdir()) - before
            tasks = [p.joinpath('task.txt').read_text() for p in runs]
            check(len(tasks) == 2, str(runs))
            for task in tasks:
                check((PANEL_FORMAT in task) == (not full), task)
                if not full:
                    check(task.endswith(PANEL_FORMAT), '格式要求不在最终末尾')
                    check(task.index('code.txt') < task.index(PANEL_FORMAT), task)
            d = Path(next(line.removeprefix('会诊目录：') for line in r.stdout.splitlines()
                          if line.startswith('会诊目录：')))
            check((d / 'prompt.md').read_text() == '只列两条' +
                  render_attachments([('code.txt', b'ATTACH_PANEL_MARKER')]), 'prompt 快照缺少附件全文')
            check((d / 'qoder.md').read_text() == answer + '\n', '归档回答被截断')
            if full:
                check('### qoder' not in r.stdout and '有效意见' not in r.stdout, r.stdout)
            else:
                check('### qoder' in r.stdout and answer[:3000] in r.stdout, r.stdout)
                check('HIDDEN_END' not in r.stdout and f'…（超长已截断，全文：{d / "qoder.md"}）' in r.stdout, r.stdout)
                check(r.stdout.index('rc=0') < r.stdout.index('### qoder')
                      < r.stdout.index('### codex-sol') < r.stdout.index('## 主会话裁决'), r.stdout)
        (t / 'code.txt').write_text('x' * 30_001)
        r = sh(cmd, env=env)
        warning = '超过文本座位建议的 30KB'
        check(r.returncode == 0 and r.stderr.count(warning) == 1, r.stderr)
        d = next(p.parent for p in (t / 'state/panels').glob('*/summary.json') if str(p.parent) in r.stdout)
        check(all(warning not in p.read_text() for p in d.glob('*.stderr.txt')), '子进程重复警告')


def panel_attachment_snapshot():
    import hashlib
    from ai_worker import render_attachments
    # Observe real worker launches, while retaining real provider subprocesses.
    code = """import json, os, sys
from pathlib import Path
import ai_worker, panel
read = ai_worker.read_attachments
def preflight(*args):
    files = read(*args)
    with open(os.environ['READ_LOG'], 'a') as log:
        log.write('read\\n')
    return files
ai_worker.read_attachments = preflight
popen = panel.subprocess.Popen
def launch(argv, *args, **kwargs):
    if len(argv) > 1 and argv[1] == str(panel.HERE / 'ai_worker.py'):
        with open(os.environ['WORKER_LOG'], 'a') as log:
            log.write(json.dumps(argv) + '\\n')
    return popen(argv, *args, **kwargs)
panel.subprocess.Popen = launch
sys.argv = ['panel', *sys.argv[1:]]
raise SystemExit(panel.main())
"""
    for mutation in ('rewrite', 'delete'):
        with tempfile.TemporaryDirectory() as td:
            t = Path(td)
            wd = t / 'work'; wd.mkdir()
            files = [('z.txt', '原始附件\n```代码```\n末尾无换行'.encode()),
                     ('a.txt', b'second attachment\n')]
            for name, data in files:
                (wd / name).write_bytes(data)
            au = fake_agent_use(t)
            script = au / 'scripts/run_agent.py'
            script.write_text(
                'import os\nfrom pathlib import Path\n'
                'source = Path(os.environ["SOURCE"])\n'
                'if os.environ["MUTATION"] == "delete": source.unlink()\n'
                'else: source.write_text("CHANGED_AFTER_PREFLIGHT")\n'
                'Path(os.environ["MUTATED"]).touch()\n' + FAKE_AGENT_USE)
            bins = fake_bins(t)
            cli = bins / 'codex'
            cli.write_text('#!' + sys.executable + '\n'
                           'import os, sys, time\nfrom pathlib import Path\n'
                           'if "--version" in sys.argv: print("codex-cli fake"); sys.exit(0)\n'
                           'deadline = time.monotonic() + 10\n'
                           'while not Path(os.environ["MUTATED"]).exists():\n'
                           '    if time.monotonic() > deadline: sys.exit(2)\n'
                           '    time.sleep(0.02)\n'
                           'prompt = next(a for a in sys.argv if "snapshot request" in a)\n'
                           'Path(os.environ["CLI_PROMPT"]).write_text(prompt)\n'
                           'print("codex ok")\n')
            env = dict(os.environ, AIW_STATE=str(t / 'state'), AGENT_USE_HOME=str(au),
                       PATH=f"{bins}:{os.environ['PATH']}", SOURCE=str(wd / 'z.txt'),
                       MUTATION=mutation, MUTATED=str(t / 'mutated'),
                       CLI_PROMPT=str(t / 'cli-prompt'), READ_LOG=str(t / 'reads'),
                       WORKER_LOG=str(t / 'workers'))
            r = subprocess.run([sys.executable, '-c', code, str(wd), '15', 'snapshot request',
                    'qoder', 'codex-sol', '--attach', './z.txt', '--attach', 'a.txt'], env=env, capture_output=True, text=True, timeout=120)
            check(r.returncode == 0, r.stdout + r.stderr)
            out, = (t / 'state/panels').iterdir()
            snapshot = (out / 'prompt.md').read_bytes()
            check(snapshot == ('snapshot request' + render_attachments(files)).encode(),
                  '快照不等于预检原文')
            check((t / 'reads').read_text() == 'read\n', 'panel 重复读取附件')
            argv = [json.loads(line) for line in (t / 'workers').read_text().splitlines()]
            check(len(argv) == 2 and all('--attach' not in a for a in argv), str(argv))
            agent_task, = [p.read_bytes() for p in (t / 'state/runs').glob('*/task.txt')
                           if 'qoder' in p.parent.name]
            prompts = [agent_task, (t / 'cli-prompt').read_bytes()]
            check(all(snapshot in prompt and b'CHANGED_AFTER_PREFLIGHT' not in prompt
                      for prompt in prompts), '座位未收到相同的原始附件全文')
            manifest = json.loads((out / 'attachments.json').read_text())
            check([item['path'] for item in manifest] == [name for name, _ in files], str(manifest))
            for item, (name, data) in zip(manifest, files):
                # Extract actual payload bytes from the rendered fence, independently of the manifest.
                section = snapshot.split(('### ' + name + '\n').encode(), 1)[1]
                fence, payload = section.split(b'\n', 1)
                actual = payload.split(b'\n' + fence, 1)[0]
                check(actual == data and item == dict(path=name, bytes=len(actual),
                      sha256=hashlib.sha256(actual).hexdigest()), str(item))

    # All-CLI panels now warn too; workers must not repeat the warning.
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        (t / 'large.txt').write_text('x' * 30_001)
        env = dict(os.environ, AIW_STATE=str(t / 'state'),
                   PATH=f"{fake_bins(t)}:{os.environ['PATH']}")
        r = subprocess.run([sys.executable, str(REPO / 'panel.py'), str(t), '10', 'review',
                'codex-sol', 'claude', '--attach', 'large.txt'], env=env, capture_output=True, text=True, timeout=120)
        warning = '超过文本座位建议的 30KB'
        check(r.returncode == 0 and r.stderr.count(warning) == 1, r.stdout + r.stderr)
        check(all(warning not in p.read_text() for p in (t / 'state/panels').glob('*/*.stderr.txt')),
              'CLI 子进程重复警告')


def panel_attachment_preflight_failure():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        wd = t / 'work'; wd.mkdir()
        (t / 'outside.txt').write_text('outside')
        (wd / 'binary.txt').write_bytes(b'text\0binary')
        log = t / 'providers'
        env = dict(os.environ, AIW_STATE=str(t / 'state'), FAKE_AU_LOG=str(log),
                   AGENT_USE_HOME=str(fake_agent_use(t)))
        code = """import panel, sys
def forbidden(*args, **kwargs):
    raise AssertionError('worker started before attachment validation')
panel.subprocess.Popen = forbidden
sys.argv = ['panel', *sys.argv[1:]]
raise SystemExit(panel.main())
"""
        for bad in ('../outside.txt', 'binary.txt'):
            r = subprocess.run([sys.executable, '-c', code, str(wd), '10', 'review',
                    'qoder', 'workbuddy', '--attach', bad], env=env, capture_output=True, text=True, timeout=120)
            check(r.returncode == 64, r.stdout + r.stderr)
            check(not log.exists() and not list((t / 'state/panels').glob('*/*.provider'))
                  and not list((t / 'state/panels').glob('*/*.md'))
                  and not list((t / 'state/runs').glob('*')), '坏附件启动 worker 或供应商')


def panel_prior():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        env = dict(os.environ, AIW_STATE=str(t / 'state'), AGENT_USE_HOME=str(fake_agent_use(t)))
        prior = t / 'own.md'; prior.write_text('PRIOR_PRIVATE_MARKER：先验判断')
        cmd = [str(REPO / 'bin/aiw'), 'panel', str(t), '10', '独立评审任务', 'qoder', 'workbuddy']
        r = sh(cmd + ['--prior', str(prior)], env=env)
        check(r.returncode == 0 and '未提供 --prior' not in r.stderr, r.stdout + r.stderr)
        check(r.stdout.index(prior.read_text()) < r.stdout.index('✓') < r.stdout.index('## 主会话裁决'), r.stdout)
        d = next((t / 'state/panels').iterdir())
        check((d / 'prior.md').read_text() == prior.read_text(), '先验判断未归档')
        check((d / 'prompt.md').read_text() == '独立评审任务', 'prior 混进 prompt 快照')
        check(not (d / 'attachments.json').exists(), '无附件不应写清单')
        tasks = [p.read_text() for p in (t / 'state/runs').glob('*/task.txt')]
        check(len(tasks) == 2 and all('PRIOR_PRIVATE_MARKER' not in x and x.count('今天是 ') == 1 for x in tasks), str(tasks))
        template = (d / 'adjudication.md').read_text()
        check(template in r.stdout and all(x in template for x in ('接受 / 驳回 / 暂缓', '驳回必须给理由', '暂缓写明等什么')), template)
        r = sh(cmd, env=env)
        check(r.returncode == 0 and r.stderr.count('未提供 --prior：建议先写下自己的判断再看意见') == 1, r.stderr)
        before = set((t / 'state/panels').iterdir())
        for extra in (['--prior', str(t / 'missing')], ['--prior'], ['--prior', str(t)], ['--prior', str(prior), '--prior', str(prior)]):
            r = sh(cmd + extra, env=env)
            check(r.returncode == 64 and set((t / 'state/panels').iterdir()) == before, r.stderr)
        r = sh(cmd, env=dict(env, FAKE_AU_QODER='auth', FAKE_AU_WORKBUDDY='auth'))
        check(r.returncode == 1 and '## 主会话裁决' in r.stdout, r.stdout + r.stderr)
        d = (set((t / 'state/panels').iterdir()) - before).pop()
        check((d / 'adjudication.md').read_text() == template, '缺席时未写裁决模板')



def panel():
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        env = dict(os.environ, PATH=f"{fake_bins(t)}:{os.environ['PATH']}", AIW_STATE=str(t / "state"),
                   AGENT_USE_HOME=str(fake_agent_use(t)), AIW_MAIN="claude")
        pf = t / "p.md"; pf.write_text("这个策略的最强反例是什么？")
        aiw = str(REPO / "bin" / "aiw")
        p = sh(["bash", aiw, "panel", str(t), "30", str(pf)], env=env)
        check(p.returncode == 0, f"默认座位都答应 rc=0: {p.stdout}{p.stderr}")
        d = max((t / "state" / "panels").iterdir(), key=lambda p: p.stat().st_mtime_ns)
        for s in ("workbuddy", "workbuddy-hy4", "qoder", "opencode"):
            check((d / f"{s}.md").read_text() == ("workbuddy ok\n" if s == "workbuddy-hy4" else f"{s} ok\n") and (d / f"{s}.provider").read_text().strip() == s, f"{s} 产出或 provider 不对")
        check("codex ok" in (d / "codex-sol.md").read_text() and (d / "codex-sol.provider").read_text().strip() == "codex-sol", "会诊应包含 codex-sol")
        check(not (t / ".provider").exists(), "panel 不应写共享 .provider")
        # 一家挂了：不顶替，记为缺席；两家仍算会诊
        p = sh(["bash", aiw, "panel", str(t), "30", str(pf)], env=dict(env, FAKE_AU_QODER="auth"))
        d = max((t / "state" / "panels").iterdir(), key=lambda p: p.stat().st_mtime_ns)
        summ = json.loads((d / "summary.json").read_text())
        check(p.returncode == 0 and summ["answered"] == ["workbuddy", "workbuddy-hy4", "opencode", "codex-sol"], f"一家缺席不应被顶替: {summ}")
        # Codex 当主会话：不问 codex-sol，改问 claude；Claude Code 里（CLAUDECODE=1）自动不问 claude
        p = sh(["bash", aiw, "panel", str(t), "30", str(pf)], env=dict(env, AIW_MAIN="codex"))
        d = max((t / "state" / "panels").iterdir(), key=lambda p: p.stat().st_mtime_ns)
        summ = json.loads((d / "summary.json").read_text())
        check([x["seat"] for x in summ["seats"]] == ["workbuddy", "workbuddy-hy4", "opencode", "claude"], f"codex 主会话会诊座位不对: {summ}")
        check("跳过不可用座位 qoder: 熔断：auth" in p.stderr, p.stderr)
        check("nested=1" in (d / "claude.md").read_text(), "外包出去的 claude 应带 AIW_NESTED=1")
        e2 = {k: v for k, v in env.items() if k != "AIW_MAIN"}; e2["CLAUDECODE"] = "1"
        p = sh(["bash", aiw, "panel", str(t), "30", str(pf)], env=e2)
        d = max((t / "state" / "panels").iterdir(), key=lambda p: p.stat().st_mtime_ns)
        summ = json.loads((d / "summary.json").read_text())
        check("claude" not in [x["seat"] for x in summ["seats"]] and "codex-sol" in [x["seat"] for x in summ["seats"]], f"Claude 主会话不应问 claude: {summ}")
        # 重复座位拒绝
        p = sh(["bash", aiw, "panel", str(t), "30", str(pf), "qoder", "qoder"], env=env)
        check(p.returncode == 64, f"重复座位应拒绝: {p.returncode}")
        # 只剩一家：rc=1
        (t / "state" / "breaker.json").unlink()
        p = sh(["bash", aiw, "panel", str(t), "30", str(pf), "qoder", "workbuddy"], env=dict(env, FAKE_AU_QODER="auth", FAKE_AU_WORKBUDDY="auth"))
        check(p.returncode == 1, f"不足两家应 rc=1: {p.returncode}")



def panel_opinion_threshold():
    # 门槛固定两家：显式单家、停用过滤、失败都不能降低门槛。
    scenarios = ((['qoder'], False, False, 1),
                 (['qoder', 'workbuddy'], True, False, 1),
                 (['qoder', 'workbuddy'], False, True, 1),
                 (['qoder', 'workbuddy'], False, False, 0))
    for seats, disabled, failed, rc in scenarios:
        with tempfile.TemporaryDirectory() as td:
            t = Path(td)
            env = dict(os.environ, AIW_STATE=str(t / 'state'),
                       AGENT_USE_HOME=str(fake_agent_use(t)))
            if disabled:
                (t / 'state').mkdir()
                (t / 'state/disabled.json').write_text(json.dumps({'workbuddy': {
                    'since': '2026-10-13', 'evidence': 'fixture', 'notified': True}}))
            if failed:
                env['FAKE_AU_WORKBUDDY'] = 'auth'
            r = sh([sys.executable, str(REPO / 'panel.py'), str(t), '10',
                    '独立评审任务', *seats], env=env)
            check(r.returncode == rc, f'会诊门槛应固定两家: rc={r.returncode}, expected={rc}: {r.stdout}{r.stderr}')
            out, = (t / 'state/panels').iterdir()
            summary = json.loads((out / 'summary.json').read_text())
            expected = ['qoder'] if rc == 1 else seats
            check(summary['answered'] == expected and summary['required'] == 2
                  and summary['sufficient'] is (rc == 0), str(summary))
            check('### qoder' in r.stdout and (out / 'qoder.md').read_text() == 'qoder ok\n',
                  '单家成功意见仍应展示并归档')
            if len(seats) == 1 or disabled:
                check('单家意见不构成会诊，返回码为 1' in r.stderr, r.stderr)
            if disabled:
                check([x['seat'] for x in summary['seats']] == ['qoder']
                      and '已停用' in r.stderr, str(summary) + r.stderr)
            if failed:
                check(summary['seats'][1]['rc'] != 0 and '✗ workbuddy' in r.stdout,
                      str(summary) + r.stdout)


def panel_free():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        env = dict(os.environ, PATH=f"{fake_bins(t)}:{os.environ['PATH']}",
                   AIW_STATE=str(t / "state"), AGENT_USE_HOME=str(fake_agent_use(t)), AIW_MAIN="codex")
        cmd = ["bash", str(REPO / "bin/aiw"), "panel", str(t), "30", "免费会诊测试", "--free"]
        r = sh(cmd, env=env)
        check(r.returncode == 0 and "免费会诊：" in r.stderr, f"免费会诊失败: {r.stdout}{r.stderr}")
        out = next((t / "state/panels").iterdir())
        summary = json.loads((out / "summary.json").read_text())
        expected = ["workbuddy", "workbuddy-hy4", "qoder", "opencode"]
        check([x["seat"] for x in summary["seats"]] == expected and summary["answered"] == expected,
              f"免费会诊座位不对: {summary}")
        for seat in expected:
            check((out / f"{seat}.provider").read_text().strip() == seat, f"{seat} provider 不对")
        check(not (out / "codex-sol.md").exists(), "免费会诊不应调用 codex-sol")
        r = sh(cmd + ["qoder"], env=env)
        check(r.returncode == 64 and "二选一" in r.stderr, f"免费与显式座位应互斥: {r.stderr}")
        for name in ("route.py", "dispatch.py", "ai_worker.py", "findings.py", "harness_ids.py", "gitenv.py", "env_compat.py", "seats_config.py", "router-rules.json", "seats.json"):
            shutil.copy(REPO / name, t / name)
        config = json.loads((t / "seats.json").read_text())
        for seat, reason in (("codex-sol", "agent_use"), ("missing-seat", "引用悬空"), ("doubao", "panel_default")):
            local = t / "invalid-panel.json"
            write_legacy_local(local, {"panel_free": [seat]})
            r = sh([sys.executable, str(t / "route.py"), "--check"], cwd=t, env=dict(os.environ, AIW_CONFIG=str(local)))
            check(r.returncode == 1 and "panel_free" in r.stderr and reason in r.stderr,
                  f"免费名单中的 {seat} 应被拒绝: {r.stdout}{r.stderr}")
        # 免费名单仍然排除主会话自己的座位。
        code = """import panel, sys
panel.SEATS['self_seats']['codex'] = ['qoder']
sys.argv = ['panel', sys.argv[1], '30', 'free self test', '--free']
raise SystemExit(panel.main())
"""
        before = set((t / "state/panels").iterdir())
        r = sh([sys.executable, "-c", code, str(t)], env=env, cwd=REPO)
        check(r.returncode == 0, f"免费会诊 self_seats 测试失败: {r.stderr}")
        created = set((t / "state/panels").iterdir()) - before
        check(len(created) == 1, '免费会诊结果目录不唯一')
        out = created.pop()
        summary = json.loads((out / "summary.json").read_text())
        check(summary["answered"] == ["workbuddy", "workbuddy-hy4", "opencode"], "免费会诊未排除 self_seats")



def route_seat_check():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        for name in ('route.py', 'dispatch.py', 'ai_worker.py', 'findings.py', 'harness_ids.py', 'gitenv.py', 'env_compat.py', 'seats_config.py', 'router-rules.json', 'seats.json'):
            shutil.copy(REPO / name, t / name)
        cmd = [sys.executable, str(t / 'route.py'), '--check']
        check(sh(cmd, cwd=t).returncode == 0, '当前规则不通过')
        source = json.loads((t / 'router-rules.json').read_text())
        for field in ('instruction', 'instruction_codex'):
            d = json.loads(json.dumps(source)); panel = next(r for r in d['rules'] if r['id'] == 'panel')
            panel[field] = 'aiw panel 同时问 ' + ' / '.join(seat for seat in legacy_config()['panel_default'] if seat not in legacy_config()['self_seats']['claude' if field == 'instruction' else 'codex'] and seat != 'qoder')
            (t / 'router-rules.json').write_text(json.dumps(d))
            r = sh(cmd, cwd=t)
            check(r.returncode == 1 and 'panel' in r.stderr and 'qoder' in r.stderr, '名单漂移未发现')
        source['rules'][0]['instruction'] += ' aiw qoder_bad .'
        (t / 'router-rules.json').write_text(json.dumps(source))
        r = sh(cmd, cwd=t)
        check(r.returncode == 1 and 'qoder_bad' in r.stderr, '未知座位未发现')



def test_panel_sets_panel_dir():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); log = t / 'env.jsonl'
        cmd, env, wd = worker_call(t, FAKE_AU_LOG=str(log))
        r = sh([sys.executable, str(REPO / 'panel.py'), str(wd), '10', '测试', 'qoder', 'workbuddy'], env=env)
        rows = [json.loads(x) for x in log.read_text().splitlines()]
        check(r.returncode == 0 and len(rows) == 2, r.stdout + r.stderr)
        check(rows[0]['panel'] == rows[1]['panel'] and Path(rows[0]['panel']).is_dir(), 'panel env 未透传')



def route_config_read_race():
    with tempfile.TemporaryDirectory() as td:
        local = Path(td) / 'local.json'
        code = """import os, runpy, sys
from pathlib import Path
import seats_config as c
original = c.load_seats
calls = 0
def racing(*args, **kwargs):
    global calls
    calls += 1
    result = original(*args, **kwargs)
    if calls == 1:
        Path(os.environ['AIW_CONFIG']).write_text('{"seats": []}')
    return result
c.load_seats = racing
sys.argv = ['route.py', sys.argv[1]]
runpy.run_path('route.py', run_name='__main__')
"""
        for mode, rc in (('--hook', 0), ('--check', 1)):
            local.write_text('{}')
            result = sh([sys.executable, '-c', code, mode], cwd=REPO,
                        env=dict(os.environ, AIW_CONFIG=str(local)), input='{}')
            check(result.returncode == rc and 'seats' in result.stderr and 'Traceback' not in result.stderr, repr(result))
            if mode == '--hook':
                check('警告' in result.stderr, result.stderr)



def route_context_hint():
    import fcntl
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        home, env = fake_home(root)
        harness = home / '.claude/aiw'
        transcript = root / 'transcript.jsonl'
        payload = {'prompt': '会诊', 'cwd': str(root), 'session_id': 'fixture-session',
                   'transcript_path': str(transcript)}
        def write(ctx, padding=0):
            records = [{'type': 'assistant', 'message': {'id': 'fixture-message',
                        'usage': {'input_tokens': 1000, 'cache_read_input_tokens': 1000,
                                  'cache_creation_input_tokens': amount - 2000}}}
                       for amount in (250000, ctx)]
            transcript.write_text('\n'.join(json.dumps(r) for r in records) + '\n' +
                                  json.dumps({'type': 'user', 'padding': 'x' * padding}) + '\n' +
                                  json.dumps({'type': 'system'}) + '\n')
        def run(extra=None, override=None):
            result = subprocess.run([sys.executable, str(harness / 'route.py'), '--hook'],
                                    env=dict(env, **(override or {})), cwd=root,
                                    input=json.dumps(dict(payload, **(extra or {}))),
                                    capture_output=True, text=True, timeout=2)
            check(result.returncode == 0 and not result.stderr, repr(result))
            output = json.loads(result.stdout) if result.stdout else {}
            text = output.get('hookSpecificOutput', {}).get('additionalContext', '')
            hints = [line for line in text.splitlines() if line.startswith('[上下文]')]
            check(text.count('[上下文]') == len(hints) <= 1, '上下文提示不止一行: ' + text)
            check(all(len(line) <= 120 for line in hints), '上下文提示超过 120 字符: ' + text)
            check('[派单]' in text, '上下文读取改变派单')
            row = json.loads((root / 'state/router-log.jsonl').read_text().splitlines()[-1])
            check('ctx' in row and row['rule'] == 'panel', str(row))
            return text, row
        write(199999)
        text, row = run()
        check('[上下文]' not in text and row['ctx'] == 199999, text)
        write(200000, 600000)
        text, row = run()
        check('约 20 万' in text and row['ctx'] == 200000, text)
        check('新档位' in text and '新事' in text and 'aiw handoff --template' in text and
              'aiw handoff <文件>' in text and '/compact' not in text and text.count('[上下文]') == 1, text)
        text, row = run()
        check(text.count('[上下文]') == 1 and '新事' in text and 'aiw handoff' in text and
              '/compact' not in text and '新档位' not in text and '--template' not in text, '同 bucket 未仅输出条件提示: ' + text)
        check((root / 'state/context-hints/fixture-session').read_text() == '2', '条件提示改变档位标记')
        write(299999)
        text, row = run()
        check('新事' in text and '新档位' not in text and '--template' not in text,
              '同 bucket 边界重复档位提醒: ' + text)
        write(300000)
        text, row = run()
        check('约 30 万' in text and '新档位' in text, '下一 bucket 未提示')
        write(600000)
        check('[上下文]' not in run(override={'AIW_HANDOFF_AT': '0'})[0], '未关闭')
        text, row = run({'turn_id': 'fixture-turn'})
        check('[上下文]' not in text and row['ctx'] is None, text)
        for extra in ({'transcript_path': str(root / 'missing')}, {'session_id': '../invalid'},
                      {'session_id': 'x' * 81}, {'session_id': ''}):
            check('[上下文]' not in run(extra)[0], str(extra))
        transcript.write_text('{broken\n' + json.dumps({'type': 'assistant', 'message': {'usage': None}}))
        check('[上下文]' not in run()[0], '损坏 transcript 提示')
        write(700000)
        check('[上下文]' not in run(override={'AIW_HANDOFF_STEP': '0'})[0], '非法 step 提示')
        check('[上下文]' not in run(override={'AIW_HANDOFF_AT': 'bad'})[0], '非法阈值提示')
        for key in ('AIW_HANDOFF_AT', 'AIW_HANDOFF_STEP'):
            check('[上下文]' not in run(override={key: '-1'})[0], '负数配置提示: ' + key)
        check('[上下文]' not in run(override={'AIW_HANDOFF_STEP': 'bad'})[0], '非法 step 值提示')
        write(850000, 4 * 1024 * 1024 + 100)
        text, row = run()
        check('[上下文]' not in text and row['ctx'] is None, '读取超过 4MB')
        write(750000)
        text, row = run(override={'AIW_HANDOFF_STEP': '50000'})
        check('约 75 万' in text, '自定义 step 失效')
        check((root / 'state/context-hints/fixture-session').read_text() == '15', 'bucket 未原子保存')
        compact_session = {'session_id': 'compact-session'}
        marker = root / 'state/context-hints/compact-session'
        write(450000)
        check('约 45 万' in run(compact_session)[0], '压缩用例首次未提示')
        write(620000)
        check('约 62 万' in run(compact_session)[0], '压缩用例下一 bucket 未提示')
        write(80000)
        check('[上下文]' not in run(compact_session)[0], '压缩后低于阈值仍提示')
        check(not marker.exists(), '压缩后节流标记未删除')
        write(410000)
        check('约 41 万' in run(compact_session)[0], '压缩后重新达到阈值未提示')

        fifo = root / 'transcript.fifo'
        os.mkfifo(fifo)
        text, row = run({'transcript_path': str(fifo)})
        check('[上下文]' not in text and row['ctx'] is None, 'FIFO 不应提醒或阻塞')
        write(700000)
        with (root / 'state/context-hints/.compact-session.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            check('[上下文]' not in run(compact_session)[0], '持锁时仍提醒')
            check(marker.read_text() == '4', '持锁时修改标记')
            write(80000)
            check('[上下文]' not in run(compact_session)[0], '持锁且低于阈值时提醒')
            check(marker.read_text() == '4', '持锁且低于阈值时删除标记')
        write(700000)
        for invalid in ('9' * 100000, 'not-a-number', '7\n', ''):
            marker.write_text(invalid)
            check('约 70 万' in run(compact_session)[0], '非法标记未按 -1 提醒')
            check(marker.read_text() == '7', '非法标记未正常替换')



def route_cross_session():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        home, env = fake_home(root)
        harness = home / '.claude/aiw'
        settings = json.loads((harness / 'seats.json').read_text())
        settings['findings'] = {'hint': True}
        (harness / 'seats.json').write_text(json.dumps(settings))
        folder = root / 'state/findings'
        folder.mkdir(parents=True)
        (folder / 'count-cache.json').write_text(json.dumps({'open': 120, 'last_triage': None}))
        transcript = root / 'transcript.jsonl'
        transcript.write_text(json.dumps({'type': 'assistant', 'message': {'usage': {'input_tokens': 450000}}}))
        def run(prompt):
            result = sh([sys.executable, str(harness / 'route.py'), '--hook'], env=env, cwd=root,
                        input=json.dumps({'prompt': prompt, 'cwd': str(root), 'session_id': 'cross-fixture',
                                          'transcript_path': str(transcript)}))
            check(result.returncode == 0, repr(result))
            output = json.loads(result.stdout) if result.stdout else {}
            text = output.get('hookSpecificOutput', {}).get('additionalContext', '')
            row = json.loads((root / 'state/router-log.jsonl').read_text().splitlines()[-1])
            return text, row
        text, row = run(' \n<cross-session-message source="fixture">会诊</cross-session-message>')
        check('[派单]' not in text and '[缓冲区]' not in text and '约 45 万' in text, text)
        check(row['rule'] is None and row['cross_session'] is True and row['ctx'] == 450000, str(row))
        check(not (folder / 'hint.json').exists(), '跨会话消耗缓冲区节流')
        text, row = run('会诊')
        check('[派单]' in text and '[缓冲区]' in text and row['rule'] == 'panel', text)
        check('cross_session' not in row, str(row))



def handoff_cli():
    import re
    import shlex
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        home, env = fake_home(root)
        harness = home / '.claude/aiw'
        workdir = root / "demo project's"
        workdir.mkdir()
        file = workdir / "handoff file's.md"
        required = ['目标', '现状', '已定决策', '下一步', '相关文件']
        file.write_text('\n\n'.join('## ' + s + '\nfixture' for s in required))
        orca = root / 'fakebin/orca'
        argv_file = root / 'argv.json'
        orca.write_text('#!' + sys.executable + '\nimport json, os, sys\n'
                        'from pathlib import Path\n'
                        'Path(os.environ["FAKE_ORCA_ARGV"]).write_text(json.dumps(sys.argv[1:]))\n'
                        'print("{\\"terminal\\": \\"fixture-terminal\\"}")\n'
                        'sys.exit(int(os.environ.get("FAKE_ORCA_RC", "0")))\n')
        orca.chmod(0o755)
        env.update(AIW_ORCA_BIN=str(orca), FAKE_ORCA_ARGV=str(argv_file))
        def run(*args, override=None):
            return sh([str(harness / 'bin/aiw'), 'handoff', *map(str, args)],
                      env=dict(env, **(override or {})), cwd=workdir)
        result = run('--template')
        check(result.returncode == 0 and all('## ' + s in result.stdout for s in required), repr(result))
        result = run('--path', '--workdir', workdir)
        suggested = Path(result.stdout.strip())
        check(result.returncode == 0 and suggested.parent == root / 'state/handoffs' and
              suggested.parent.is_dir() and re.fullmatch(r'\d{8}-\d{6}-demo-project-s.md', suggested.name), repr(result))
        broken = root / 'broken.md'
        broken.write_text('## 目标\nfixture')
        result = run(broken)
        check(result.returncode == 1 and all('## ' + s in result.stderr for s in required[1:]), repr(result))
        for fence in ('```', '~~~'):
            broken.write_text('## 目标\nfixture\n' + fence + '\n' +
                              '\n'.join('## ' + s for s in required[1:]) + '\n' + fence + '\n')
            result = run(broken)
            check(result.returncode == 1 and '交接文件缺少小节' in result.stderr and
                  all('## ' + s in result.stderr for s in required[1:]), repr(result))
        broken.write_text('')
        check(run(broken).returncode == 1, '空文件未拒绝')
        check(run(root / 'missing.md').returncode == 1, '不存在文件未拒绝')
        check(run(file, '--workdir', root / 'missing').returncode == 1, '不存在目录未拒绝')
        result = run(file, '--workdir', workdir)
        prompt = f'读交接文件 {file.resolve()}，先核对现状，再按「下一步」继续。'
        expected = ['terminal', 'create', '--worktree', 'path:' + str(workdir.resolve()),
                    '--title', '接续·demo-project-s', '--command', 'claude ' + shlex.quote(prompt), '--focus', '--json']
        check(result.returncode == 0 and '已在 Orca 新开会话' in result.stdout and
              'fixture-terminal' in result.stdout and '旧会话可以关掉' in result.stdout, repr(result))
        check(json.loads(argv_file.read_text()) == expected, argv_file.read_text())
        result = run(file, override={'FAKE_ORCA_RC': '3'})
        check(result.returncode == 0 and 'Orca 返回 3' in result.stdout, repr(result))
        fallback = result.stdout.splitlines()[-1]
        check(shlex.split(fallback) == ['cd', str(workdir.resolve()), '&&', 'claude', prompt], fallback)
        argv_file.unlink()
        result = run(file, '--dry-run')
        check(result.returncode == 0 and json.loads(result.stdout) == [str(orca), *expected] and
              not argv_file.exists(), repr(result))
        result = run(file, override={'AIW_ORCA_BIN': str(root / 'missing-orca')})
        check(result.returncode == 0 and 'Orca 启动失败' in result.stdout and
              shlex.split(result.stdout.splitlines()[-1]) == ['cd', str(workdir.resolve()), '&&', 'claude', prompt], repr(result))
        check(run(file, override={'AIW_NESTED': '1'}).returncode == 64, '执行方可开会话')
        check(run(file, '--unknown').returncode == 64, '未知参数退出码错误')



CASES = {case.__name__: case for case in (
    route_textwork,
    route_override,
    map_interrupt,
    map_batch,
    panel_compact_context,
    panel_attachment_snapshot,
    panel_attachment_preflight_failure,
    panel_prior,
    panel,
    panel_opinion_threshold,
    panel_free,
    route_seat_check,
    test_panel_sets_panel_dir,
    route_config_read_race,
    route_context_hint,
    route_cross_session,
    handoff_cli,
)}
