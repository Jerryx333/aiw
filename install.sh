#!/usr/bin/env bash
# 安装 / 更新 aiw 到本机。幂等：重复跑不会重复追加，改动前先备份。
#   git clone <仓库> ~/.claude/aiw && ~/.claude/aiw/install.sh
#   --minimal：只装 aiw 命令（~/.local/bin/aiw 和 ~/.claude/aiw），不改 CLAUDE.md、settings.json、Codex/pi/omp 配置；
#              之后不带 --minimal 重跑即可接入规则和钩子。
#   --claude-hook=settings|plugin：默认 settings；plugin 删除 settings 中的派单钩子，交给插件管理。
#   --with-agent-use：同时安装 / 更新公开核心、现成适配器并注册 provider（座位需自行在本地配置添加）。
# 做六件事：
#   1. ~/.claude/CLAUDE.md 引入 @~/.claude/aiw/CLAUDE.md（已有内容保留）
#   2. ~/.claude/settings.json 加 UserPromptSubmit 派单钩子和 40 万 token 自动压缩兜底（已有设置保留）
#   3. ~/.local/bin/aiw 软链到本仓库；~/.local/bin 不在 PATH 时只提示，不改 shell 配置
#   4. 本机有 Codex（~/.codex 或 codex 命令）时：~/.codex/AGENTS.md 写入/刷新标记块，~/.codex/hooks.json 加派单钩子
#      pi / omp：目录或命令存在时写 ~/.pi/agent/AGENTS.md、~/.omp/agent/AGENTS.md，不装钩子
#      （跳过：AIW_NO_CODEX=1 / AIW_NO_PI=1 / AIW_NO_OMP=1）
#   5. 可选安装 agent-use + agent-use-adapters，保留已有注册表和座位
#   6. 校验派单规则，跑一次 aiw doctor
# 先预检所有要改的 JSON，坏了一处都不动；JSON/标记块用临时文件原子替换。
# 兼容 macOS 自带 bash 3.2。
# 卸载：uninstall.sh
set -euo pipefail

CLAUDE_HOOK=settings
WITH_AGENT_USE=0
MINIMAL=0
for arg in "$@"; do
  case "$arg" in
    --claude-hook=settings|--claude-hook=plugin) CLAUDE_HOOK="${arg#*=}" ;;
    --with-agent-use) WITH_AGENT_USE=1 ;;
    --minimal) MINIMAL=1 ;;
    *) echo "用法：$0 [--minimal] [--claude-hook=settings|plugin] [--with-agent-use]" >&2; exit 64 ;;
  esac
done

src="${BASH_SOURCE[0]}"
while [ -L "$src" ]; do
  dir="$(cd -P "$(dirname "$src")" && pwd)"
  src="$(readlink "$src")"
  case "$src" in /*) ;; *) src="${dir}/${src}" ;; esac
done
H="$(cd -P "$(dirname "$src")" && pwd)"
CLAUDE_DIR="${HOME}/.claude"
STAMP="$(date +%Y%m%d-%H%M%S)"
PY="${AIW_PYTHON:-python3}"

command -v "$PY" >/dev/null || { echo "需要 Python 3.11 及以上；可用 AIW_PYTHON 指定解释器" >&2; exit 1; }

if ! "$PY" -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
  "$PY" -c 'import sys; print("当前 Python " + sys.version.split()[0] + "，需要 Python 3.11 及以上；可用 AIW_PYTHON 指定解释器", file=sys.stderr)'
  exit 1
fi

# 已有安装入口必须属于这份 checkout；包括失效软链。
"$PY" - "${CLAUDE_DIR}/aiw" "$H" <<'PYEOF'
import os, sys
path, repo = sys.argv[1:]
if os.path.lexists(path) and os.path.realpath(path) != os.path.realpath(repo):
    sys.exit(f"~/.claude/aiw 指向另一份 checkout：{os.path.realpath(path)}，请先处理")
PYEOF

# 0. 预检：要改的 JSON 先全部解析一遍，任何一个坏了就一处都不动（最小安装不改这些文件，跳过）
CHECK_FILES=("${CLAUDE_DIR}/settings.json")
if [ "$MINIMAL" = 1 ]; then
  CHECK_FILES=()
elif [ -z "${AIW_NO_CODEX:-}" ] && { [ -d "${HOME}/.codex" ] || command -v codex >/dev/null 2>&1; }; then
  CHECK_FILES+=("${HOME}/.codex/hooks.json")
fi
"$PY" - ${CHECK_FILES[@]+"${CHECK_FILES[@]}"} <<'PYEOF'
import json, sys
from pathlib import Path
bad = []
for f in map(Path, sys.argv[1:]):
    if f.exists():
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("顶层必须是对象")
            hooks = data.get("hooks", {})
            if not isinstance(hooks, dict):
                raise ValueError("hooks 必须是对象")
            groups = hooks.get("UserPromptSubmit", [])
            if not isinstance(groups, list):
                raise ValueError("hooks.UserPromptSubmit 必须是列表")
            for group in groups:
                if not isinstance(group, dict):
                    raise ValueError("UserPromptSubmit 每个组必须是对象")
                if not isinstance(group.get("hooks", []), list):
                    raise ValueError("组内 hooks 必须是列表")
        except (OSError, ValueError) as e:
            bad.append(f"{f}（{e}）")
if bad:
    sys.exit("✗ 以下文件 JSON 语法或结构不合法，没有做任何改动。修好后重跑：\n  " + "\n  ".join(bad))
PYEOF

mkdir -p "${CLAUDE_DIR}" "${H}/state"
if [ ! -e "${CLAUDE_DIR}/aiw" ] && [ ! -L "${CLAUDE_DIR}/aiw" ]; then
  ln -s "$H" "${CLAUDE_DIR}/aiw"
fi
# 最小安装标记只用 bash 重定向和 Python 处理，不依赖 rm / touch（测试环境 PATH 受限）。
if [ "$MINIMAL" = 1 ]; then
  : > "${H}/state/minimal"
  echo "· 最小安装：不改 CLAUDE.md、settings.json 和 Codex / pi / omp 配置"
else
  "$PY" -c 'import os, sys
try:
    os.remove(sys.argv[1])
except FileNotFoundError:
    pass' "${H}/state/minimal"
fi

if [ "$MINIMAL" = 0 ]; then
# 1. CLAUDE.md
"$PY" - "$CLAUDE_DIR/CLAUDE.md" "$HOME/.config/aiw/CLAUDE.personal.md" "$STAMP" <<'PYEOF'
import shutil, sys
from pathlib import Path
md, personal, stamp = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
IMPORT_LINE = '@~/.claude/aiw/CLAUDE.md'
PERSONAL_LINE = '@~/.config/aiw/CLAUDE.personal.md'
old = md.read_text(encoding="utf-8") if md.exists() else ""
new = old
# Collapse duplicate project imports, preserving the first position.
seen = False
lines = []
for line in new.splitlines(keepends=True):
    if line.rstrip('\r\n') == IMPORT_LINE:
        if seen:
            continue
        seen = True
    lines.append(line)
new = ''.join(lines)
if IMPORT_LINE not in new.splitlines():
    new += ("\n" if new else "") + IMPORT_LINE + "\n"
if personal.exists():
    lines = [line for line in new.splitlines(keepends=True) if line.rstrip("\r\n") != PERSONAL_LINE]
    index = next(i for i, line in enumerate(lines) if line.rstrip("\r\n") == IMPORT_LINE)
    if not lines[index].endswith("\n"):
        lines[index] += "\n"
    lines.insert(index + 1, PERSONAL_LINE + "\n")
    new = "".join(lines)
elif personal.is_symlink():
    print(f"⚠ {personal} 是断链，视为无个人规则")
if new != old:
    if md.exists():
        shutil.copy2(md, md.with_name(md.name + ".bak-" + stamp))
    tmp = md.with_name(md.name + ".tmp-aiw")
    tmp.write_text(new, encoding="utf-8")
    tmp.replace(md)
    print(f"✓ {md} 更新规则引入")
else:
    print(f"✓ {md} 已引入规则")
PYEOF

# 2. settings.json 钩子和自动压缩兜底（用 python 合并 JSON，不覆盖已有设置）
"$PY" - "${CLAUDE_DIR}/settings.json" "$STAMP" "$CLAUDE_HOOK" <<'PYEOF'
import json, os, shutil, sys
from pathlib import Path
def atomic_write(p, text):
    tmp = p.with_name(p.name + ".tmp-aiw")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(p)
p, stamp = Path(sys.argv[1]), sys.argv[2]
cmd = 'python3 "$HOME/.claude/aiw/route.py" --hook'
data = {}
if p.exists():
    raw = p.read_text(encoding="utf-8")
    try:
        data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as e:
        sys.exit(f"✗ {p} 不是合法 JSON（{e}），没有改动。修好后重跑。")
before = json.dumps(data, ensure_ascii=False, sort_keys=True)
compact_changed = False
if os.environ.get("AIW_NO_AUTOCOMPACT") == "1":
    compact_result = "· 自动压缩兜底：跳过（AIW_NO_AUTOCOMPACT=1）"
elif "CLAUDE_CODE_AUTO_COMPACT_WINDOW" in os.environ:
    compact_result = "· 自动压缩兜底：跳过（已设置 CLAUDE_CODE_AUTO_COMPACT_WINDOW）"
elif "autoCompactWindow" in data:
    compact_result = "✓ 自动压缩兜底：已有 autoCompactWindow 值，保留"
else:
    data["autoCompactWindow"] = 400000
    compact_changed = True
    compact_result = "✓ 自动压缩兜底：写入 autoCompactWindow = 400000"
if sys.argv[3] == "plugin":
    hooks = data.get("hooks", {})
    groups = hooks.get("UserPromptSubmit", [])
    kept_groups = []
    changed = False
    for group in groups:
        items = group.get("hooks", [])
        kept = [h for h in items if not (
            isinstance(h, dict) and isinstance(h.get("command"), str)
            and 'aiw/route.py' in h["command"])]
        if len(kept) == len(items):
            kept_groups.append(group)
        else:
            changed = True
            if kept or any(k != "hooks" for k in group):
                kept_groups.append(dict(group, hooks=kept))
    if changed:
        if kept_groups:
            hooks["UserPromptSubmit"] = kept_groups
        else:
            hooks.pop("UserPromptSubmit", None)
    if changed or compact_changed:
        if p.exists():
            shutil.copy2(p, p.with_name(p.name + ".bak-" + stamp))
        atomic_write(p, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    if changed:
        print(f"✓ 派单钩子由插件管理，已删除 {p} 中的派单钩子（备份 {p.name}.bak-{stamp}）")
    else:
        print("✓ 派单钩子由插件管理，settings.json 无需改动" if not compact_changed
              else "✓ 派单钩子由插件管理，settings.json 无派单钩子")
    print(compact_result)
    sys.exit(0)
hooks = data.setdefault("hooks", {})
groups = hooks.setdefault("UserPromptSubmit", [])
installed = False
for group in groups:
    kept = []
    for hook in group.get("hooks", []):
        mine = (isinstance(hook, dict) and isinstance(hook.get("command"), str)
                and 'aiw/route.py' in hook["command"])
        if not mine:
            kept.append(hook)
        elif not installed:
            kept.append(dict(hook, type="command", command=cmd, timeout=10))
            installed = True
    if "hooks" in group:
        group["hooks"] = kept
if not installed:
    groups.append({"hooks": [{"type": "command", "command": cmd, "timeout": 10}]})
if json.dumps(data, ensure_ascii=False, sort_keys=True) != before:
    if p.exists():
        shutil.copy2(p, p.with_name(p.name + ".bak-" + stamp))
    atomic_write(p, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    print(f"✓ {p} 合并派单钩子")
else:
    print(f"✓ {p} 已有派单钩子")
print(compact_result)
PYEOF
fi

# 3. aiw 命令
chmod +x "${H}/bin/aiw" "${H}/install.sh"
mkdir -p "${HOME}/.local/bin"
ln -sfn "${H}/bin/aiw" "${HOME}/.local/bin/aiw"
echo "✓ ~/.local/bin/aiw → ${H}/bin/aiw"
case ":${PATH}:" in
  *":${HOME}/.local/bin:"*) ;;
  *) echo "· ~/.local/bin 不在 PATH：把 export PATH=\"\$HOME/.local/bin:\$PATH\" 加进 shell 配置（如 ~/.zshrc）后新开终端；本脚本不改 shell 配置" ;;
esac

# 4. Codex / pi / omp 全局上下文
write_agents_block() {
  mkdir -p "$1"
  "$PY" - "$1" "${H}/AGENTS.harness.md" "$STAMP" <<'PYEOF'
import json, shutil, sys
from pathlib import Path
def atomic_write(p, text):
    tmp = p.with_name(p.name + ".tmp-aiw")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(p)
d, src, stamp = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
BEGIN, END = "<!-- aiw:begin（install.sh 生成，勿手改） -->", "<!-- aiw:end -->"
content = src.read_text(encoding='utf-8').strip()
personal = Path.home() / '.config/aiw/AGENTS.personal.md'
if personal.exists():
    content += "\n\n" + personal.read_text(encoding='utf-8').strip()
elif personal.is_symlink():
    print(f"⚠ {personal} 是断链，视为无个人规则")
block = f"{BEGIN}\n{content}\n{END}\n"
md = d / "AGENTS.md"
old = md.read_text(encoding="utf-8") if md.exists() else ""
if BEGIN in old and END in old:
    head, rest = old.split(BEGIN, 1)
    new = head + block + rest.split(END, 1)[1].lstrip("\n")
else:
    new = old + ("\n" if old and not old.endswith("\n") else "") + ("\n" if old else "") + block
if new == old:
    print(f"✓ {md} 已是最新")
else:
    if md.exists():
        shutil.copy2(md, md.with_name(md.name + ".bak-" + stamp))
    atomic_write(md, new)
    print(f"✓ {md} 写入 aiw 标记块" + ("（备份 AGENTS.md.bak-" + stamp + "）" if old else ""))
PYEOF
}

# 4. Codex（主会话也可以是 Codex）
CODEX_DIR="${HOME}/.codex"
if [ "$MINIMAL" = 1 ]; then
  :
elif [ -z "${AIW_NO_CODEX:-}" ] && { [ -d "$CODEX_DIR" ] || command -v codex >/dev/null 2>&1; }; then
  mkdir -p "$CODEX_DIR"
  write_agents_block "$CODEX_DIR"
  "$PY" - "$CODEX_DIR" "$STAMP" <<'PYEOF'
import json, shutil, sys
from pathlib import Path
d, stamp = Path(sys.argv[1]), sys.argv[2]
def atomic_write(p, text):
    tmp = p.with_name(p.name + ".tmp-aiw")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(p)
hp = d / "hooks.json"
data = {}
if hp.exists():
    raw = hp.read_text(encoding="utf-8")
    try:
        data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as e:
        sys.exit(f"✗ {hp} 不是合法 JSON（{e}），没有改动。修好后重跑。")
groups = data.setdefault("hooks", {}).setdefault("UserPromptSubmit", [])
cmd = 'python3 "$HOME/.claude/aiw/route.py" --hook'
before = json.dumps(data, ensure_ascii=False, sort_keys=True)
installed = False
for group in groups:
    kept = []
    for hook in group.get("hooks", []):
        mine = (isinstance(hook, dict) and isinstance(hook.get("command"), str)
                and 'aiw/route.py' in hook["command"])
        if not mine:
            kept.append(hook)
        elif not installed:
            kept.append(dict(hook, type="command", command=cmd, timeout=10))
            installed = True
    if "hooks" in group:
        group["hooks"] = kept
if not installed:
    groups.append({"hooks": [{"type": "command", "command": cmd, "timeout": 10}]})
if json.dumps(data, ensure_ascii=False, sort_keys=True) != before:
    if hp.exists():
        shutil.copy2(hp, hp.with_name(hp.name + ".bak-" + stamp))
    atomic_write(hp, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    print(f"✓ {hp} 合并派单钩子")
else:
    print(f"✓ {hp} 已有派单钩子")
PYEOF
else
  echo "· 未发现 Codex，跳过 Codex 接入"
fi

for main in pi omp; do
  case "$main" in
    pi) disabled="${AIW_NO_PI:-}" ;;
    omp) disabled="${AIW_NO_OMP:-}" ;;
  esac
  if [ "$MINIMAL" = 1 ]; then
    :
  elif [ "$disabled" = 1 ]; then
    echo "· 跳过 $main 接入"
  elif [ -d "${HOME}/.${main}" ] || command -v "$main" >/dev/null 2>&1; then
    write_agents_block "${HOME}/.${main}/agent"
  else
    echo "· 未发现 ${main}，跳过接入"
  fi
done

# 5. 校验派单规则（坏了只提示，不中断）
"$PY" "${H}/route.py" --check || echo "↑ 派单规则有问题，钩子会跳过派单，修好后再跑 aiw route --check。"

# 可选：agent-use 生态（--with-agent-use）
source "${H}/agent_use_setup.sh"
aiw_agent_use_paths
if [ "$WITH_AGENT_USE" = 1 ]; then
  aiw_install_repo "$AU" agent-use "${AIW_AGENT_USE_REPO:-https://github.com/Jerryx333/agent-use.git}" || :
  if aiw_install_repo "$ADAPTERS" agent-use-adapters "${AIW_ADAPTERS_REPO:-https://github.com/Jerryx333/agent-use-adapters.git}"; then
    if bash "$ADAPTERS/scripts/install.sh"; then
      echo "✓ agent-use-adapters 注册表安装完成"
    fi
  fi
fi
# 6. 体检（失败不中断安装）
echo
"${H}/bin/aiw" doctor || echo "↑ 有座位没装好或未在 PATH，装好后再跑 aiw doctor。"
echo
if [ "$MINIMAL" = 1 ]; then
  echo "最小安装完成：只装了 aiw 命令，没有改 CLAUDE.md、settings.json、Codex / pi / omp 配置和 shell 配置。要接派单规则和钩子，重跑 install.sh（不带 --minimal）。"
else
  echo "完成。下一步：按需完成各 CLI 的官方登录（如 codex login），然后 aiw doctor"
fi
if [ "$WITH_AGENT_USE" = 0 ] && [ ! -f "$AU/scripts/run_agent.py" ]; then
  echo '· 想把更多模型接进来，可重跑 install.sh --with-agent-use（会克隆 agent-use 和 agent-use-adapters）。'
fi
echo '· 使用 agent-use 座位前，请自行在 ~/.config/aiw/seats.local.json（或 AIW_CONFIG）中添加座位，示例见 https://github.com/Jerryx333/aiw/blob/main/docs/guide.zh-CN.md#接入更多模型可选'
