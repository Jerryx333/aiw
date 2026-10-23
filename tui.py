#!/usr/bin/env python3
"""Codex main-session tmux wrapper and its status callback."""
import json
import os
import re
import stat
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tomllib
import uuid

from dispatch import is_off
from env_compat import getenv, valid_tui_id

HERE = Path(__file__).resolve().parent
STATUS_EXTRA_TIMEOUT = 3


SGR = re.compile(r'\x1b\[([0-9;]*)m')
ESCAPE = re.compile(
    r'\x1b\].*?(?:\x07|\x1b\\|$)'
    r'|\x1b[P^_X].*?(?:\x1b\\|$)'
    r'|\x1b\[[0-?]*[ -/]*(?:[@-~]|$)'
    r'|\x1b[ -/]*[0-~]?|\x1b', re.DOTALL)
COLORS = ('black', 'red', 'green', 'yellow', 'blue', 'magenta', 'cyan', 'white')


def _clean_extra(text, keep_newlines=False):
    # Strip controls inside escape sequences too, before recognizing them.
    controls = r'[\x00-\x06\x08-\x09\x0b-\x1a\x1c-\x1f\x7f-\x9f]' if keep_newlines else r'[\x00-\x06\x08-\x1a\x1c-\x1f\x7f-\x9f]'
    text = re.sub(controls, '', text)
    text = ESCAPE.sub(lambda m: m[0] if SGR.fullmatch(m[0]) else '', text)
    # The only ESC bytes left belong to retained SGR sequences.
    controls = r'[\x00-\x09\x0b-\x1a\x1c-\x1f\x7f-\x9f]' if keep_newlines else r'[\x00-\x1a\x1c-\x1f\x7f-\x9f]'
    return re.sub(controls, '', text)


def _extra_text(text):
    text = _clean_extra(text)
    # Count visible characters before escaping hashes or inserting tmux styles.
    visible = len(SGR.sub('', text))
    limit = 119 if visible > 120 else 120
    used = 0
    parts = []
    position = 0
    for match in SGR.finditer(text):
        chunk = text[position:match.start()]
        take = chunk[:max(0, limit - used)]
        parts.append(take.replace('#', '##'))
        used += len(take)
        if used >= limit and visible > 120:
            break
        for code in match[1].split(';'):
            number = int(code or '0')
            if number in (0, 39):
                parts.append('#[default]')
            elif 30 <= number <= 37:
                parts.append('#[fg=' + COLORS[number - 30] + ']')
            elif 90 <= number <= 97:
                parts.append('#[fg=bright' + COLORS[number - 90] + ']')
        position = match.end()
    else:
        parts.append(text[position:position + max(0, limit - used)].replace('#', '##'))
    if visible > 120:
        parts.append('…')
    return _clean_extra(''.join(parts)) if visible else ''


def _extra_status():
    command = os.environ.get('AIW_TUI_STATUS_EXTRA', '')
    if command:
        try:
            argv = shlex.split(command)
            if argv:
                result = subprocess.run(argv, stdin=subprocess.DEVNULL,
                                        capture_output=True, text=True,
                                        env=dict(os.environ, AIW_TUI_STATUS_ANSI='1'),
                                        timeout=STATUS_EXTRA_TIMEOUT, check=True)
                output = _clean_extra(result.stdout, keep_newlines=True)
                extra = next((s.strip() for s in output.split('\n') if SGR.sub('', s).strip()), '')
                return _extra_text(extra)
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    return ''


def _mapping(tui_id):
    if valid_tui_id(tui_id):
        try:
            return (Path(getenv('AIW_STATE', HERE / 'state')) / 'tui' / tui_id).read_text().splitlines()
        except (OSError, UnicodeError):
            pass
    return []


def _rollout(path):
    context, tokens = {}, None
    fd = None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return context, tokens
        with os.fdopen(fd, 'rb') as stream:
            fd = None
            size = stream.seek(0, os.SEEK_END)
            start = max(0, size - 512 * 1024)
            stream.seek(max(0, start - 1))
            partial = start and stream.read(1) != b'\n'
            stream.seek(start)
            lines = stream.read(512 * 1024).splitlines()
            if partial:
                lines = lines[1:]
        found_context = found_tokens = False
        for line in reversed(lines):
            try:
                record = json.loads(line)
                payload = record.get('payload', {})
                if not isinstance(payload, dict):
                    continue
                if not found_context and record.get('type') == 'turn_context':
                    context, found_context = payload, True
                if not found_tokens and record.get('type') == 'event_msg' and payload.get('type') == 'token_count':
                    found_tokens = True
                    value = payload['info']['last_token_usage']['input_tokens']
                    if type(value) is int and value >= 0:
                        tokens = value
                if found_context and found_tokens:
                    break
            except (ValueError, AttributeError, KeyError, TypeError):
                continue
    except (OSError, ValueError):
        pass
    finally:
        if fd is not None:
            os.close(fd)
    return context, tokens


def _token_text(value):
    if value < 1000:
        return str(value)
    return f'{value / 1000000:.1f}M' if value >= 1000000 else f'{value / 1000:.1f}k'


def _color(color, value):
    return '#[fg=' + color + ']' + str(value).replace('#', '##')


def status_lines(tui_id, pane_path=''):
    mapping = _mapping(tui_id)
    transcript = mapping[1] if len(mapping) > 1 else ''
    context, tokens = _rollout(transcript) if transcript else ({}, None)
    if not transcript:
        try:
            home = Path(os.environ.get('CODEX_HOME', '~/.codex')).expanduser()
            with (home / 'config.toml').open('rb') as stream:
                config = tomllib.load(stream)
            context = {'model': config.get('model'), 'effort': config.get('model_reasoning_effort')}
        except (OSError, ValueError):
            pass
    parts = []
    model = []
    for label, key, color in (('模型', 'model', 'cyan'), ('思考', 'effort', 'magenta')):
        if context.get(key):
            model.append(_color('brightblack', label + ' ') + _color(color, context[key]))
    if model:
        parts.append(' '.join(model))
    cwd = context.get('cwd') or pane_path
    if cwd:
        def git(*args):
            return subprocess.run(['git', '-C', cwd, *args], capture_output=True,
                                  text=True, timeout=2, check=True).stdout
        try:
            branch = git('rev-parse', '--abbrev-ref', 'HEAD').strip()
            if branch:
                parts.append(_color('yellow', '⎇ ' + branch))
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
        try:
            added = deleted = 0
            for row in git('diff', '--numstat', 'HEAD').splitlines():
                fields = row.split('\t')
                if len(fields) >= 3:
                    added += int(fields[0]) if fields[0].isdigit() else 0
                    deleted += int(fields[1]) if fields[1].isdigit() else 0
            parts.append(_color('brightblack', f'改动 (+{added},-{deleted})'))
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    separator = _color('brightblack', ' | ')
    line2 = []
    if tokens is not None:
        line2.append(_color('brightblack', '上下文 ') + _color('green', _token_text(tokens)))
    extra = _extra_status()
    if extra:
        line2.append('#[default]' + extra if tokens is not None else extra)
    return separator.join(parts), separator.join(line2), _dispatch_status(tui_id)


def status(tui_id, pane_path=None, session=None):
    if pane_path is None and session is None:
        line = _dispatch_status(tui_id)
        extra = _extra_status()
        return line + ' │ ' + extra if extra else line
    lines = status_lines(tui_id, pane_path or '')
    if session:
        for key, line in zip(('@aiw_line2', '@aiw_line3'), lines[1:]):
            try:
                # status-format 会再经 strftime 展开，% 必须写成 %%。
                subprocess.run(['tmux', 'set-option', '-t', session, key, line.replace('%', '%%')],
                               capture_output=True, timeout=2, check=True)
            except (OSError, subprocess.SubprocessError):
                pass
    return lines[0]


def _dispatch_status(tui_id):
    off, _ = is_off(Path(getenv('AIW_STATE', HERE / 'state')))
    idle = '派单已关闭' if off else '○ 派单空闲'
    try:
        if not valid_tui_id(tui_id):
            return idle
        mapping = _mapping(tui_id)
        thread = mapping[0].strip() if mapping else ''
        board = shutil.which('aiw-board')
        if not board:
            return ('派单 ?（未安装 aiw-board）' + (' ·已关闭' if off else '')) if thread else idle
        if not thread:
            thread = 'aiw-tui-' + tui_id
        result = subprocess.run([board, 'statusline'], input=json.dumps({'session_id': thread}),
                                capture_output=True, text=True, timeout=10)
        line = result.stdout.splitlines()[0] if result.stdout else '派单 ?'
        if off:
            return line.replace('派单空闲', '派单已关闭') if '派单空闲' in line else line + ' ·已关闭'
        return line
    except Exception:
        return '派单 ? ·已关闭' if off else '派单 ?'


def _has_status_line_override(args):
    arguments = iter(args)
    for arg in arguments:
        if arg == '--':
            break
        if arg in ('-c', '--config'):
            value = next(arguments, '')
        elif arg.startswith('--config='):
            value = arg[len('--config='):]
        elif arg.startswith('-c'):
            value = arg[2:]
        else:
            continue
        if value.partition('=')[0].strip() == 'tui.status_line':
            return True
    return False


def launch(args):
    if not shutil.which('tmux'):
        print('需要 tmux：brew install tmux', file=sys.stderr)
        return 127
    tui_id = uuid.uuid4().hex
    os.environ['AIW_TUI_ID'] = tui_id
    os.environ['AIW_STATE'] = str(Path(getenv('AIW_STATE', HERE / 'state')).absolute())
    # tmux's server may predate this shell's PATH, Python and state settings.
    environment = ['/usr/bin/env', 'PATH=' + os.environ.get('PATH', ''),
                   'AIW_PYTHON=' + sys.executable,
                   'AIW_STATE=' + os.environ['AIW_STATE']]
    if 'CODEX_HOME' in os.environ:
        environment.append('CODEX_HOME=' + os.environ['CODEX_HOME'])
    if 'AIW_TUI_STATUS_EXTRA' in os.environ:
        environment.append('AIW_TUI_STATUS_EXTRA=' + os.environ['AIW_TUI_STATUS_EXTRA'])
    callback = shlex.join(environment + [str(HERE / 'bin' / 'aiw'), 'tui-status', tui_id])
    options = [('status', 'on'), ('status-interval', '15'), ('status-left', ''),
               ('status-right-length', '200'), ('status-right', '#(' + callback + ')')]
    if os.environ.get('TMUX'):
        for key, value in options:
            subprocess.run(['tmux', 'set-option', key, value], check=True)
        os.execvp('codex', ['codex', *args])
    else:
        if not _has_status_line_override(args):
            args = [*args, '-c', 'tui.status_line=[]']
        name = 'aiw-' + tui_id
        callback += ' #{q:pane_current_path} ' + shlex.quote(name)
        command = 'exec ' + shlex.join(environment + ['AIW_TUI_ID=' + tui_id, 'codex', *args])
        created = subprocess.run(['tmux', 'new-session', '-d', '-s', name, command])
        if created.returncode:
            return created.returncode
        argv = ['tmux']
        for key, value in [('status', '3'), ('status-interval', '15'),
                           ('status-justify', 'left'), ('status-style', 'bg=default,fg=colour250'),
                           ('status-format[0]', '#(' + callback + ')'),
                           ('status-format[1]', '#{E:@aiw_line2}'),
                           ('status-format[2]', '#{E:@aiw_line3}'), ('status-left', ''),
                           ('status-right', ''), ('window-status-format', ''),
                           ('window-status-current-format', '')]:
            if len(argv) > 1:
                argv += [';']
            argv += ['set-option', '-t', name, key, value]
        configured = subprocess.run(argv)
        if configured.returncode:
            return configured.returncode
        os.execvp('tmux', ['tmux', 'attach-session', '-t', name])


if __name__ == '__main__':
    if sys.argv[1] == 'status':
        print(status(sys.argv[2] if len(sys.argv) > 2 else '',
                     sys.argv[3] if len(sys.argv) > 3 else None,
                     sys.argv[4] if len(sys.argv) > 4 else None))
    else:
        sys.exit(launch(sys.argv[2:]))
