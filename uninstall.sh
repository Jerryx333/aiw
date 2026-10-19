#!/usr/bin/env bash
# 撤销 install.sh 的本机接入；保留仓库、state/、本地配置及独立安装的组件。
# --dry-run 只预览。先预检全部 JSON，改动前备份，文本用临时文件原子替换。
# 兼容 macOS 自带 bash 3.2。
set -euo pipefail
DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    *) echo "用法：$0 [--dry-run]" >&2; exit 64 ;;
  esac
done
src="${BASH_SOURCE[0]}"
while [ -L "$src" ]; do
  dir="$(cd -P "$(dirname "$src")" && pwd)"
  src="$(readlink "$src")"
  case "$src" in /*) ;; *) src="${dir}/${src}" ;; esac
done
H="$(cd -P "$(dirname "$src")" && pwd)"
PY="${AIW_PYTHON:-python3}"
command -v "$PY" >/dev/null || { echo "需要 Python 3；可用 AIW_PYTHON 指定解释器" >&2; exit 1; }
"$PY" - "$H" "$DRY_RUN" <<'PYEOF'
import datetime
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile

repo = Path(sys.argv[1]).resolve()
dry_run = sys.argv[2] == '1'
home = Path.home()
stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
paths = [home / '.claude/settings.json', home / '.codex/hooks.json']
parsed = {}
bad = []
# 先检查所有 JSON，任何一个出错都不能先改其它文件。
for path in paths:
    if not path.exists():
        continue
    try:
        data = json.loads(path.read_bytes().decode('utf-8'))
        if not isinstance(data, dict):
            raise ValueError('顶层必须是对象')
        hooks = data.get('hooks', {})
        if not isinstance(hooks, dict):
            raise ValueError('hooks 必须是对象')
        groups = hooks.get('UserPromptSubmit', [])
        if not isinstance(groups, list):
            raise ValueError('hooks.UserPromptSubmit 必须是列表')
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get('hooks', []), list):
                raise ValueError('UserPromptSubmit 每个组必须是对象，组内 hooks 必须是列表')
        parsed[path] = data
    except (OSError, ValueError) as exc:
        bad.append(f'{path}（{exc}）')
if bad:
    sys.exit('✗ 以下文件 JSON 语法或结构不合法，没有做任何改动。修好后重跑：\n  ' + '\n  '.join(bad))


def backup(path):
    shutil.copy2(path, path.with_name(path.name + '.bak-' + stamp), follow_symlinks=False)


def update(path, new):
    old = path.read_bytes() if path.exists() else None
    if old == new or new is None:
        print(f'✓ {path} 无 aiw 改动')
        return
    print(f'{"· 将修改" if dry_run else "✓ 清理"} {path}')
    if dry_run:
        return
    backup(path)
    fd, name = tempfile.mkstemp(prefix=path.name + '.tmp-aiw-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(new)
        shutil.copymode(path, name)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def edit_text(path, transform):
    new = transform(path.read_bytes().decode('utf-8')).encode('utf-8') if path.exists() else None
    update(path, new)


imports = {'@~/.claude/aiw/CLAUDE.md', '@~/.config/aiw/CLAUDE.personal.md'}
edit_text(home / '.claude/CLAUDE.md', lambda text: ''.join(
    line for line in text.splitlines(keepends=True) if line.rstrip('\r\n') not in imports))
claude_had_hook = False
for path in paths:
    data = parsed.get(path)
    if data is None:
        update(path, None)
        continue
    before = json.dumps(data, ensure_ascii=False)
    hooks = data.get('hooks', {})
    groups = hooks.get('UserPromptSubmit', [])
    kept_groups = []
    for group in groups:
        kept = []
        for hook in group.get('hooks', []):
            mine = (isinstance(hook, dict) and isinstance(hook.get('command'), str)
                    and 'aiw/route.py' in hook['command'])
            if mine:
                if path == paths[0]:
                    claude_had_hook = True
            else:
                kept.append(hook)
        if 'hooks' in group:
            group['hooks'] = kept
        if kept or any(key != 'hooks' for key in group):
            kept_groups.append(group)
    if 'UserPromptSubmit' in hooks:
        if kept_groups:
            hooks['UserPromptSubmit'] = kept_groups
        else:
            del hooks['UserPromptSubmit']
    if 'hooks' in data and not hooks:
        del data['hooks']
    new = (json.dumps(data, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    update(path, new if json.dumps(data, ensure_ascii=False) != before else path.read_bytes())

begin = '<!-- aiw:begin（install.sh 生成，勿手改） -->'
end = '<!-- aiw:end -->'
block = re.compile(r'^' + re.escape(begin) + r'\r?\n.*?^' + re.escape(end)
                   + r'(?:\r?\n(?:[ \t]*\r?\n)?|$)', re.MULTILINE | re.DOTALL)
for directory in ('.codex', '.pi/agent', '.omp/agent'):
    edit_text(home / directory / 'AGENTS.md', lambda text: block.sub('', text))


def remove_link(path, target):
    if path.is_symlink() and path.resolve() == target.resolve():
        print(f'{"· 将删除软链" if dry_run else "✓ 删除软链"} {path}')
        if not dry_run:
            path.unlink()  # 软链可由 install.sh 重建，不留 .bak 以免 PATH 里多出命令
    else:
        print(f'✓ {path} 无 aiw 改动')


remove_link(home / '.local/bin/aiw', repo / 'bin/aiw')


def clean_path(text):
    lines = text.splitlines(keepends=True)
    kept = []
    index = 0
    while index < len(lines):
        if lines[index].rstrip('\r\n') == '# aiw: on PATH':
            index += 1
            if index < len(lines) and lines[index].rstrip('\r\n') == 'export PATH="$HOME/.local/bin:$PATH"':
                index += 1
        else:
            kept.append(lines[index])
            index += 1
    return ''.join(kept)


edit_text(home / '.zshrc', clean_path)
entry = home / '.claude/aiw'
remove_link(entry, repo)
if not entry.is_symlink() and entry.resolve() == repo:
    print('· 仓库和 state/ 保留，不需要可自行删除 ~/.claude/aiw')
if not claude_had_hook:
    print('· 如装过 aiw 插件，请在 Claude Code 里停用')
print('· 保留本地配置 ~/.config/aiw/、agent-use / agent-use-adapters、仓库和 state/。')
print('· 保留 ~/.claude/settings.json 的 autoCompactWindow；不再需要自动压缩兜底时可自行删除。')
PYEOF
