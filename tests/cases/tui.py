"""Codex main-session identity and tmux status integration."""
from support import (Path, REPO, check, json, os, sh, subprocess, sys,
                     tempfile, wait_until, worker_call)
import shlex
import shutil
import time


def tui_sessions():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        for extra, expected, mapped in (
            ({'CODEX_THREAD_ID': 'thread-one', 'AIW_TUI_ID': 'valid-id'}, 'thread-one', True),
            ({'CODEX_THREAD_ID': 'thread-two', 'CLAUDE_CODE_SESSION_ID': 'claude'}, 'claude', False),
            ({'CODEX_THREAD_ID': 'thread-three', 'AIW_TUI_ID': '../escape'}, 'thread-three', False),
            
        ):
            state = t / expected
            cmd, env, wd = worker_call(t, FAKE_AU_QODER='brief', AIW_STATE=str(state), **extra)
            p = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            path = state / 'inflight' / f'{p.pid}.json'
            try:
                wait_until(lambda: path.exists() or p.poll() is not None, 'inflight not ready')
                check(path.exists() and json.loads(path.read_text())['session'] == expected, 'inflight session')
                out, err = p.communicate(timeout=30)
                check(p.returncode == 0, out + err)
                rows = [json.loads(line) for line in (state / 'seat-ledger.jsonl').read_text().splitlines()]
                check(all(row['session'] == expected for row in rows), str(rows))
                if mapped:
                    check((state / 'tui/valid-id').read_text() == expected + '\n', 'mapping')
                else:
                    check(not (state / 'tui').exists() and not (t / 'escape').exists(), 'unexpected mapping')
            finally:
                if p.poll() is None:
                    p.terminate()
                p.communicate(timeout=30)
        from env_compat import write_tui_session
        from unittest.mock import patch
        with patch.dict(os.environ, CODEX_THREAD_ID='nested-thread', AIW_TUI_ID='nested', AIW_NESTED='1'):
            write_tui_session(t / 'nested')
        check(not (t / 'nested/tui').exists(), 'nested mapping')
        with patch.dict(os.environ, CODEX_THREAD_ID='new-thread', AIW_TUI_ID='keep'):
            folder = t / 'tui'; folder.mkdir()
            (folder / 'keep').write_text('old-thread\nrollout.jsonl\n')
            write_tui_session(t)
            check((folder / 'keep').read_text() == 'new-thread\nrollout.jsonl\n', 'transcript lost')
        state = t / 'blocked'; state.mkdir(); (state / 'tui').write_text('blocked')
        cmd, env, _ = worker_call(t, AIW_STATE=str(state), CODEX_THREAD_ID='thread', AIW_TUI_ID='valid')
        r = sh(cmd, env=env)
        check(r.returncode == 0 and '警告：写 tui 会话失败' in r.stderr, r.stderr)


def tui_status():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); b = t / 'bin'; b.mkdir()
        env = dict(os.environ, PATH=str(b), AIW_PYTHON=sys.executable, AIW_STATE=str(t / 'state'))
        env.pop('AIW_TUI_STATUS_EXTRA', None)
        # bin/aiw needs dirname; keep only that utility, excluding installed board/tmux.
        (b / 'dirname').symlink_to(shutil.which('dirname'))
        cmd = ['/bin/bash', str(REPO / 'bin/aiw'), 'tui-status']
        for ident in ('missing', '../escape', ''):
            r = sh(cmd + [ident], env=env)
            check(r.returncode == 0 and r.stdout == '○ 派单空闲\n', repr(r))
        folder = t / 'state/tui'; folder.mkdir(parents=True); (folder / 'ok').write_text('thread\n')
        r = sh(cmd + ['ok'], env=env)
        check(r.returncode == 0 and r.stdout == '派单 ?（未安装 aiw-board）\n', repr(r))
        board = b / 'aiw-board'
        board.write_text(f'#!{sys.executable}\nimport sys\nfrom pathlib import Path\nPath({str(t / "stdin")!r}).write_text(sys.stdin.read())\nprint("summary  ")\nprint("ignored")\n')
        board.chmod(0o755)
        (folder / 'empty').write_text(' \n')
        for ident in ('missing', 'empty', 'ok'):
            r = sh(cmd + [ident], env=env)
            check(r.returncode == 0 and r.stdout == 'summary  \n', repr(r))
            session = 'thread' if ident == 'ok' else 'aiw-tui-' + ident
            check(json.loads((t / 'stdin').read_text()) == {'session_id': session}, 'board stdin')
        (t / 'stdin').unlink()
        for ident in ('../escape', ''):
            r = sh(cmd + [ident], env=env)
            check(r.returncode == 0 and r.stdout == '○ 派单空闲\n', repr(r))
            check(not (t / 'stdin').exists(), 'invalid id called board')
        r = sh(cmd + ['ok'], env=env)
        check(r.returncode == 0 and r.stdout == 'summary  \n', repr(r))
        check(json.loads((t / 'stdin').read_text()) == {'session_id': 'thread'}, 'board stdin')
        board.write_text('#!/bin/sh\nexit 1\n')
        r = sh(cmd + ['ok'], env=env)
        check(r.returncode == 0 and len(r.stdout.splitlines()) == 1 and not r.stderr, repr(r))


def tui_status_extra():
    with tempfile.TemporaryDirectory(prefix='aiw extra ') as td:
        t = Path(td); b = t / 'bin'; b.mkdir()
        (b / 'dirname').symlink_to(shutil.which('dirname'))
        env = dict(os.environ, PATH=str(b), AIW_PYTHON=sys.executable, AIW_STATE=str(t / 'state'))
        env.pop('AIW_TUI_STATUS_EXTRA', None)
        cmd = ['/bin/bash', str(REPO / 'bin/aiw'), 'tui-status', 'ok']
        baseline = sh(cmd, env=env).stdout
        script = t / 'extra command'

        def write_extra(body):
            script.write_text(f'#!{sys.executable}\n' + body)
            script.chmod(0o755)
            return dict(env, AIW_TUI_STATUS_EXTRA=shlex.join([str(script), 'two words']))

        extra_env = write_extra('import sys\nassert sys.stdin.read() == ""\nassert sys.argv[1:] == ["two words"]\nprint("extra #1\\nsecond")\n')
        r = sh(cmd, env=extra_env)
        check(r.returncode == 0 and r.stdout == baseline.rstrip('\n') + ' │ extra ##1\n'
              and not r.stderr, repr(r))
        extra_env = write_extra('print("  \\n  extra #1  \\nsecond")\n')
        check(sh(cmd, env=extra_env).stdout.endswith(' │ extra ##1\n'), 'first nonempty line')
        for body in ('import sys\nprint("ignored")\nprint("error", file=sys.stderr)\nsys.exit(1)\n',
                     'pass\n', 'print("  ")\n'):
            r = sh(cmd, env=write_extra(body))
            check(r.returncode == 0 and r.stdout == baseline and not r.stderr, repr(r))
        for command in (str(t / 'missing'), "'unclosed", '   ', ''):
            r = sh(cmd, env=dict(env, AIW_TUI_STATUS_EXTRA=command))
            check(r.returncode == 0 and r.stdout == baseline and not r.stderr, repr(r))
        for output, expected in (('x' * 200, 'x' * 119 + '…'),
                                 ('x' * 118 + '#tail', 'x' * 118 + '##…'),
                                 ('#' * 100, '##' * 100),
                                 ('x' * 120, 'x' * 120)):
            r = sh(cmd, env=write_extra(f'print({output!r})\n'))
            extra = r.stdout.rstrip('\n').split(' │ ')[-1]
            check(extra == expected and len(extra.replace('##', '#')) <= 120 and not r.stderr, repr(r))
        r = sh(cmd, env=write_extra("print('\\x1b[36mextra #1\\x1b[0m')\n"))
        check(r.stdout == baseline.rstrip('\n') + ' │ #[fg=cyan]extra ##1#[default]\n', 'ANSI command output')
        extra_env = write_extra('import os\nprint(os.environ.get("AIW_TUI_STATUS_ANSI", "missing"))\n')
        extra_env['AIW_TUI_STATUS_ANSI'] = '0'
        r = sh(cmd, env=extra_env)
        check(r.stdout == baseline.rstrip('\n') + ' │ 1\n', 'single-line ANSI capability')
        import tui
        from unittest.mock import patch
        with patch.dict(os.environ, extra_env):
            check(tui.status_lines('ok')[1] == '1', 'three-line ANSI capability')
        dirty = '\x1b[2J\x1b]0;title\x07\x1b(B\x07\x7f\x9b\x1b[90mextra\x1b[0m'
        r = sh(cmd, env=write_extra(f'print({dirty!r})\n'))
        check(r.stdout == baseline.rstrip('\n') + ' │ #[fg=brightblack]extra#[default]\n'
              and not any(c in r.stdout for c in ('\x1b', '\x07', '\x7f', '\x9b')), 'clean ANSI command output')
        r = sh(cmd, env=write_extra("print('\\x1b[2J\\x07\\nextra')\n"))
        check(r.stdout == baseline.rstrip('\n') + ' │ extra\n', 'first cleaned nonempty line')
        # Block until killed by the shortened timeout, without sleeping.
        extra_env = write_extra('import signal\nsignal.pause()\n')
        timeout_cmd = [sys.executable, '-c',
                       'import tui; tui.STATUS_EXTRA_TIMEOUT = 0.1; print(tui.status("ok"))']
        r = sh(timeout_cmd, env=extra_env, cwd=REPO)
        check(r.returncode == 0 and r.stdout == baseline and not r.stderr, repr(r))
        extra_env = write_extra('print("extra #1")\n')
        folder = t / 'state/tui'; folder.mkdir(parents=True); (folder / 'ok').write_text('thread\n')
        for board_body in (None, 'print("summary #raw  ")\n',
                           'pass\n', 'raise RuntimeError("board failed")\n'):
            if board_body is not None:
                board = b / 'aiw-board'
                board.write_text(f'#!{sys.executable}\n' + board_body)
                board.chmod(0o755)
            for ident in ('ok', '../escape'):
                base = sh(cmd[:-1] + [ident], env=env).stdout.rstrip('\n')
                r = sh(cmd[:-1] + [ident], env=extra_env)
                check(r.returncode == 0 and r.stdout == base + ' │ extra ##1\n'
                      and not r.stderr, repr(r))


def tui_launch():
    with tempfile.TemporaryDirectory(prefix='aiw space ') as td:
        t = Path(td); b = t / 'bin'; b.mkdir(); log = t / 'tmux.jsonl'; result = t / 'codex.json'
        (b / 'dirname').symlink_to(shutil.which('dirname'))
        tmux = b / 'tmux'
        tmux.write_text(f'#!{sys.executable}\nimport json, os, sys\nwith open({str(log)!r}, "a") as f: f.write(json.dumps(sys.argv[1:]) + "\\n")\nif sys.argv[1] == "new-session": sys.exit(int(os.environ.get("FAKE_TMUX_RC", "0")))\nif sys.argv[1] == "attach-session": sys.exit(int(os.environ.get("FAKE_ATTACH_RC", "0")))\n')
        tmux.chmod(0o755)
        codex = b / 'codex'
        codex.write_text(f'#!{sys.executable}\nimport os, json, sys\nfrom pathlib import Path\nPath({str(result)!r}).write_text(json.dumps([sys.argv[1:], os.environ.get("AIW_TUI_ID"), os.environ.get("AIW_STATE")]))\n')
        codex.chmod(0o755)
        # Exercise a repository entry path containing spaces as well.
        repo = t / 'repo space'; (repo / 'bin').mkdir(parents=True)
        for name in ('tui.py', 'dispatch.py', 'env_compat.py', 'bin/aiw'):
            shutil.copy(REPO / name, repo / name)
        env = dict(os.environ, PATH=str(b), AIW_PYTHON=sys.executable, AIW_STATE=str(t / 'state space'))
        env.pop('TMUX', None)
        env.pop('AIW_TUI_STATUS_EXTRA', None)
        args = ['--model', 'two words', "quote'", '$(touch bad)', 'semi;colon', '', 'line\nbreak']
        cmd = ['/bin/bash', str(repo / 'bin/aiw'), 'tui', *args]
        r = sh(cmd, env=env); check(r.returncode == 0, r.stderr)
        calls = [json.loads(line) for line in log.read_text().splitlines()]
        check(len(calls) == 3, str(calls))
        created, options, attached = calls
        check(created[:3] == ['new-session', '-d', '-s'], str(calls))
        check(attached == ['attach-session', '-t', created[3]], str(calls))
        argv = ['new-session', '-s', created[3], created[4], ';', *options]
        launch = created[4]
        parsed = shlex.split(launch)
        check(parsed[-2:] == ['-c', 'tui.status_line=[]']
              and parsed[-len(args)-2:-2] == args and parsed[-len(args)-3] == 'codex', launch)
        callback = argv[argv.index('status-format[0]') + 1]
        expected = [('status', '3'), ('status-interval', '15'), ('status-justify', 'left'),
                    ('status-style', 'bg=default,fg=colour250'), ('status-format[0]', callback),
                    ('status-format[1]', '#{E:@aiw_line2}'),
                    ('status-format[2]', '#{E:@aiw_line3}'), ('status-left', ''), ('status-right', ''),
                    ('window-status-format', ''), ('window-status-current-format', '')]
        expected_argv = []
        for key, value in expected:
            expected_argv += [';', 'set-option', '-t', argv[2], key, value]
        check(argv[4:] == expected_argv and '-g' not in argv, str(argv))
        check(callback.startswith('#(') and callback.endswith(')'), callback)
        callback_args = shlex.split(callback[2:-1])
        check(not any(a.startswith('AIW_TUI_STATUS_EXTRA=') for a in callback_args), callback)
        check(callback_args[-5:-3] == [str(repo.resolve() / 'bin/aiw'), 'tui-status'], callback)
        tui_id = callback_args[-3]
        check(callback_args[-2:] == ['#{q:pane_current_path}', argv[2]], callback)
        check(all(c.isalnum() or c in '_-' for c in tui_id), tui_id)
        check(argv[2] == 'aiw-' + tui_id, str(argv))
        check('AIW_TUI_ID=' + tui_id in parsed, launch)
        check(sh(['/bin/sh', '-c', launch], env=env).returncode == 0, launch)
        check(json.loads(result.read_text()) == [args + ['-c', 'tui.status_line=[]'], tui_id, env['AIW_STATE']], result.read_text())
        for overrides in (['-c', 'tui.status_line=["model"]'],
                          ['--config', 'tui.status_line=["model"]'],
                          ['--config=tui.status_line=[]'], ['-ctui.status_line=[]'],
                          ['-c', 'model="example"']):
            for inside in (False, True):
                log.unlink()
                r = sh(cmd + overrides, env=dict(env, TMUX='fake') if inside else env)
                check(r.returncode == 0, r.stderr)
                if not inside:
                    call = json.loads(log.read_text().splitlines()[0])
                    check(sh(['/bin/sh', '-c', call[4]], env=env).returncode == 0, call[4])
                expected_args = args + overrides
                if not inside and overrides == ['-c', 'model="example"']:
                    expected_args += ['-c', 'tui.status_line=[]']
                check(json.loads(result.read_text())[0] == expected_args, result.read_text())
        for failure_env, expected_rc in (({'FAKE_TMUX_RC': '23'}, 23), ({'FAKE_ATTACH_RC': '19'}, 19)):
            log.unlink()
            r = sh(cmd, env=dict(env, **failure_env))
            calls = [json.loads(line) for line in log.read_text().splitlines()]
            check(r.returncode == expected_rc, repr(r))
            if 'FAKE_TMUX_RC' in failure_env:
                check(len(calls) == 1 and calls[0][0] == 'new-session', str(calls))
        log.unlink()
        r = sh(cmd, env=dict(env, TMUX='fake', AIW_STATE='state rel'), cwd=t)
        check(r.returncode == 0, r.stderr)
        calls = [json.loads(line) for line in log.read_text().splitlines()]
        check(len(calls) == 5 and all(a[0] == 'set-option' and '-g' not in a for a in calls), str(calls))
        recorded = json.loads(result.read_text())
        check(recorded[0] == args, result.read_text())
        check(Path(recorded[2]).is_absolute() and recorded[2] == str(t.resolve() / 'state rel'), result.read_text())
        callback = next(a[2] for a in calls if a[1] == 'status-right')
        callback_args = shlex.split(callback[2:-1])
        check('AIW_STATE=' + recorded[2] in callback_args, callback)
        check(not any(a.startswith('AIW_TUI_STATUS_EXTRA=') for a in callback_args), callback)
        extra = shlex.join([str(t / 'extra command'), "quote'", '$(touch bad)', 'line\nbreak'])
        for inside in (False, True):
            log.unlink()
            launch_env = dict(env, AIW_TUI_STATUS_EXTRA=extra)
            if inside:
                launch_env['TMUX'] = 'fake'
            r = sh(cmd, env=launch_env)
            check(r.returncode == 0 and not r.stderr, repr(r))
            calls = [json.loads(line) for line in log.read_text().splitlines()]
            callback = (next(a[2] for a in calls if a[1] == 'status-right') if inside
                        else calls[1][calls[1].index('status-format[0]') + 1])
            callback_args = shlex.split(callback[2:-1])
            check(callback_args[0] == '/usr/bin/env'
                  and 'AIW_TUI_STATUS_EXTRA=' + extra in callback_args[:-3], callback)
        check(not (t / 'bad').exists(), 'extra command was interpreted by shell')
        tmux.unlink()
        r = sh(cmd, env=env)
        check(r.returncode == 127 and '需要 tmux：brew install tmux' in r.stderr, repr(r))


def tui_hook_mapping():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        payload = {'turn_id': 'turn', 'session_id': 'thread',
                   'transcript_path': str(t / 'rollout.jsonl'), 'prompt': 'hello', 'cwd': str(t)}
        cmd = [sys.executable, str(REPO / 'route.py'), '--hook']
        env = dict(os.environ, AIW_STATE=str(t / 'state'))
        env.pop('AIW_TUI_ID', None)
        baseline = sh(cmd, env=env, input=json.dumps(payload))
        for ident, nested, mapped in (('valid', '', True), ('nested', '1', False), ('../escape', '', False)):
            current = dict(env, AIW_TUI_ID=ident, AIW_NESTED=nested)
            result = sh(cmd, env=current, input=json.dumps(payload))
            check(result.returncode == 0 and result.stdout == ('' if nested else baseline.stdout), repr(result))
            if mapped:
                check((t / 'state/tui/valid').read_text() == 'thread\n' + payload['transcript_path'] + '\n', 'hook mapping')
        check(not (t / 'state/tui/nested').exists() and not (t / 'state/escape').exists(), 'invalid mapping')
        blocked = t / 'blocked'; blocked.mkdir(); (blocked / 'tui').write_text('blocked')
        result = sh(cmd, env=dict(env, AIW_STATE=str(blocked), AIW_TUI_ID='valid'), input=json.dumps(payload))
        check(result.stdout == baseline.stdout and '警告：写 tui 会话失败' in result.stderr, repr(result))


def tui_render():
    import tui
    from unittest.mock import patch
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); folder = t / 'state/tui'; folder.mkdir(parents=True)
        repo = t / 'repo'; repo.mkdir()
        def git(*args):
            result = sh(['git', '-C', str(repo), *args])
            check(result.returncode == 0, result.stderr)
            return result.stdout
        git('init', '-b', 'main')
        (repo / 'file').write_text('old\nkeep\n')
        git('add', 'file'); git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-m', 'initial')
        (repo / 'file').write_text('new\nkeep\nmore\nlast\n')
        rollout = t / 'rollout.jsonl'
        context = {'type': 'turn_context', 'payload': {'model': 'gpt-6-astra', 'effort': 'high', 'cwd': str(repo)}}
        token = {'type': 'event_msg', 'payload': {'type': 'token_count', 'info': {'last_token_usage': {'input_tokens': 58900}}}}
        # Leading junk exceeds the read window; malformed records are skipped.
        rollout.write_text('x' * (600 * 1024) + '\n' + json.dumps(context) + '\n' + json.dumps(token) + '\ninvalid\n')
        (folder / 'ok').write_text('thread\n' + str(rollout) + '\n')
        with patch.dict(os.environ, AIW_STATE=str(t / 'state'), CODEX_HOME=str(t), AIW_TUI_STATUS_EXTRA=''):
            with patch.object(tui, '_dispatch_status', return_value='○ 派单空闲 │ 今日 0 单'):
                lines = tui.status_lines('ok', str(t))
                check(lines == ('#[fg=brightblack]模型 #[fg=cyan]gpt-6-astra #[fg=brightblack]思考 #[fg=magenta]high#[fg=brightblack] | #[fg=yellow]⎇ main#[fg=brightblack] | #[fg=brightblack]改动 (+3,-1)',
                                '#[fg=brightblack]上下文 #[fg=green]58.9k', '○ 派单空闲 │ 今日 0 单'), str(lines))
                context['payload']['cwd'] = str(t)
                rollout.write_text(json.dumps(context) + '\n' + json.dumps(token) + '\n')
                check('⎇' not in tui.status_lines('ok', str(repo))[0] and '改动' not in tui.status_lines('ok')[0], 'cwd precedence')
                (t / 'config.toml').write_text('model = "fallback"\nmodel_reasoning_effort = "low"\n')
                check(tui.status_lines('missing')[0] == '#[fg=brightblack]模型 #[fg=cyan]fallback #[fg=brightblack]思考 #[fg=magenta]low', 'config fallback')
                check(tui.status_lines('missing')[1] == '', 'empty line2')
                with patch.object(tui, '_extra_status', return_value='extra'):
                    check(tui.status_lines('missing')[1] == 'extra', 'extra only')
                    check(tui.status_lines('ok')[1].endswith('#[fg=brightblack] | #[default]extra'), 'context plus extra')
                rollout.write_text(json.dumps(context) + '\n' + json.dumps(token) + '\n'
                                   + json.dumps({'type': 'turn_context', 'payload': {'model': 'latest'}}) + '\n'
                                   + json.dumps({'type': 'event_msg', 'payload': {'type': 'token_count', 'info': {'last_token_usage': {'input_tokens': 999}}}}) + '\n')
                check(tui.status_lines('ok')[0] == '#[fg=brightblack]模型 #[fg=cyan]latest', 'latest context')
                check(tui.status_lines('ok')[1] == '#[fg=brightblack]上下文 #[fg=green]999', 'latest token usage')
                (t / 'config.toml').write_text('invalid')
                check(tui.status_lines('missing')[0] == '', 'invalid config')
            for value, expected in ((999, '999'), (1000, '1.0k'), (58900, '58.9k'), (1200000, '1.2M')):
                check(tui._token_text(value) == expected, str(value))
            b = t / 'bin'; b.mkdir(); log = t / 'tmux.jsonl'
            tmux = b / 'tmux'
            tmux.write_text(f'#!{sys.executable}\nimport json, os, sys\nwith open({str(log)!r}, "a") as f: f.write(json.dumps(sys.argv[1:]) + "\\n")\nif sys.argv[1] == "new-session": sys.exit(int(os.environ.get("FAKE_TMUX_RC", "0")))\nif sys.argv[1] == "attach-session": sys.exit(int(os.environ.get("FAKE_ATTACH_RC", "0")))\n')
            tmux.chmod(0o755)
            extra = shlex.join([sys.executable, '-c', 'print("q 26%")'])
            with patch.dict(os.environ, PATH=str(b) + os.pathsep + os.environ['PATH'], AIW_TUI_STATUS_EXTRA=extra):
                result = sh([sys.executable, str(REPO / 'tui.py'), 'status', 'ok', str(t), 'aiw-ok'])
                check(result.returncode == 0 and len(result.stdout.splitlines()) == 1 and not result.stderr, repr(result))
                calls = [json.loads(row) for row in log.read_text().splitlines()]
                lines = tui.status_lines('ok')
                # tmux 会对 status-format 再做 strftime 展开，% 须转义为 %%。
                check('26%' in lines[1] and calls == [['set-option', '-t', 'aiw-ok', '@aiw_line2', lines[1].replace('%', '%%')],
                                ['set-option', '-t', 'aiw-ok', '@aiw_line3', lines[2].replace('%', '%%')]], str(calls))
        check(tui._extra_text('\x1b[31;1mred#\x1b[0m') == '#[fg=red]red###[default]', 'ANSI conversion')
        for code, expected in ((39, 'default'), (90, 'fg=brightblack'), (97, 'fg=brightwhite'), (30, 'fg=black'), (37, 'fg=white')):
            check(tui._extra_text(f'\x1b[{code}mtext') == '#[' + expected + ']text', str(code))
        check(tui._extra_text('\x1b[32m' + '#' * 121 + '\x1b[39m') == '#[fg=green]' + '##' * 119 + '…', 'visible truncation')
        noise = '\x1b[2J\x1b]0;title\x07\x1b]0;title\x1b\\\x1b(B\x1bPpayload\x1b\\'
        controls = ''.join(chr(c) for c in range(32) if c != 27) + ''.join(chr(c) for c in range(127, 160))
        check(tui._extra_text(noise + controls + '\x1b[90mtext\x1b[0m') == '#[fg=brightblack]text#[default]', 'escape and control cleaning')
        check(tui._extra_text(noise + 'x' * 120) == 'x' * 120, 'cleaned visible length')
        check(tui._extra_text('text\x1b]unterminated') == 'text', 'unterminated OSC')
        check(tui._extra_text('\x1b[9\x001mtext\x1b[0m') == '#[fg=brightred]text#[default]', 'control inside SGR')


def tui_gc():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); folder = t / 'state/tui'; folder.mkdir(parents=True)
        old = folder / 'old'; old.write_text('thread'); new = folder / 'new'; new.write_text('thread')
        os.utime(old, (time.time() - 8 * 86400,) * 2)
        (folder / 'link').symlink_to(old)
        directory = folder / 'directory'; directory.mkdir(); os.utime(directory, (0, 0))
        env = dict(os.environ, AIW_STATE=str(t / 'state'))
        cmd = [sys.executable, str(REPO / 'gc.py'), '7']
        r = sh(cmd + ['--dry-run'], env=env)
        check(r.returncode == 0 and old.exists() and '预计删除 1' in r.stdout, repr(r))
        r = sh(cmd, env=env)
        check(r.returncode == 0 and not old.exists() and new.exists() and directory.exists()
              and (folder / 'link').is_symlink(), repr(r))


CASES = {f.__name__: f for f in (tui_sessions, tui_status, tui_status_extra, tui_launch, tui_hook_mapping, tui_render, tui_gc)}
