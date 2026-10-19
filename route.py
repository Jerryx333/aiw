#!/usr/bin/env python3
"""派单：判断一条消息该谁接，输出要注入的指令。只注入，不拦截。

    aiw route "帮我批量给 scripts 下的审计脚本补测试"
    aiw route --codex "…"            # 看 Codex 当主会话时的派单
    python3 route.py --hook          # Claude Code / Codex 的 UserPromptSubmit 钩子（stdin 为钩子 JSON，两家格式相同）

规则来源（先项目、后本机、后全局，第一条命中即停）：
    <项目根>/.claude/router-rules.json        项目特例（可选）
    ~/.config/aiw/router-rules.local.json    本机（AIW_ROUTER_RULES 可指定）
    ~/.claude/aiw/router-rules.json    全局
项目根 = 从当前目录往上找，第一个含 .claude/router-rules.json 或 .git 的目录。
    aiw route --check                # 只校验规则（全局 + 当前项目），坏了返回 1
项目文件可写 "disable": ["规则id"] 关掉某条全局规则。
每次判定追加到 state/router-log.jsonl（带主机名和项目名）。
"""
from __future__ import annotations

import datetime as dt
import fcntl
import json
import os
from env_compat import getenv, write_tui_session
import re
import socket
import stat
import sys
import tempfile
import time
from pathlib import Path

import findings
import dispatch

HERE = Path(__file__).resolve().parent
STATE = Path(getenv("AIW_STATE", HERE / "state"))
LOG = STATE / "router-log.jsonl"


def dispatch_off_text(until) -> str:
    return f"[派单已关闭] 用户已用 aiw off 关闭派单（{dispatch.until_text(until)}），本会话所有工作由主会话自己完成，不调用 aiw write/review/draft/panel/goal；用户点名派单时照做。"


def off_notice_due(session) -> bool:
    """关闭提示每个会话只注入一次（命中派单规则的消息除外）；无会话编号或记录损坏时照常注入。"""
    if not isinstance(session, str) or not session:
        return True
    path = STATE / "off-notified.json"
    now = time.time()
    try:
        seen = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(seen, dict):
            seen = {}
    except (OSError, ValueError):
        seen = {}
    seen = {k: v for k, v in seen.items() if isinstance(v, (int, float)) and now - v < 2 * 86400}
    due = session not in seen
    seen[session] = now
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
        tmp.write_text(json.dumps(seen), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass
    return due


def dispatch_off_hook() -> None:
    off, until = dispatch.is_off(STATE)
    if off:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": dispatch_off_text(until)}}, ensure_ascii=False))


# Validate before importing worker (which also serves non-hook commands).
from seats_config import ConfigError, load_seats, ROLE_NAMES, BUILTIN_COMMANDS
try:
    load_seats(Path(__file__).resolve().parent)
    from ai_worker import errand_names, StateError
except (ConfigError, SystemExit) as error:
    hook = __name__ == '__main__' and '--hook' in sys.argv
    print(f"配置{'警告' if hook else '错误'}：{error}", file=sys.stderr)
    if hook:
        dispatch_off_hook()
    raise SystemExit(0 if hook else 1)


def context_hint(payload: dict) -> tuple[int | None, str]:
    """有界读取 transcript 尾部；提醒失败不得影响派单或用户消息。"""
    ctx = None
    temporary = None
    fd = None
    try:
        if "turn_id" in payload:
            return None, ""
        fd = os.open(payload["transcript_path"], os.O_RDONLY | os.O_NONBLOCK)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None, ""
        transcript = os.fdopen(fd, "rb")
        fd = None  # fdopen 成功后由文件对象负责关闭
        with transcript:
            size = transcript.seek(0, os.SEEK_END)
            window = 512 * 1024
            while True:
                start = max(0, size - window)
                partial = False
                if start:
                    transcript.seek(start - 1)
                    partial = transcript.read(1) != b"\n"
                transcript.seek(start)
                lines = transcript.read(window).splitlines()
                if partial:
                    lines = lines[1:]  # 首行可能被截断，不能当完整记录
                for line in reversed(lines):
                    try:
                        record = json.loads(line)
                    except (ValueError, UnicodeError):
                        continue
                    if isinstance(record, dict) and record.get("type") == "assistant":
                        message = record.get("message")
                        if isinstance(message, dict) and "usage" in message:
                            usage = message["usage"]
                            values = [usage.get(k, 0) for k in
                                      ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")]
                            if not all(type(v) is int and v >= 0 for v in values):
                                return None, ""
                            ctx = sum(values)
                            break
                if ctx is not None or start == 0 or window >= 4 * 1024 * 1024:
                    break
                window *= 2
        if ctx is None:
            return None, ""
        threshold = int(os.environ.get("AIW_HANDOFF_AT", "200000"))
        step = int(os.environ.get("AIW_HANDOFF_STEP", "100000"))
        session = payload.get("session_id")
        if threshold <= 0 or step <= 0:
            return ctx, ""
        if not isinstance(session, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", session):
            return ctx, ""
        directory = STATE / "context-hints"
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / f".{session}.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return ctx, ""
            marker = directory / session
            if ctx < threshold:
                marker.unlink(missing_ok=True)
                return ctx, ""
            bucket = ctx // step
            previous = -1
            try:
                with marker.open("rb") as source:
                    content = source.read(32)
                    if os.fstat(source.fileno()).st_size <= 32 and re.fullmatch(rb"[0-9]+", content):
                        previous = int(content)
            except FileNotFoundError:
                pass
            if bucket <= previous:
                return ctx, (f"[上下文] 约 {(ctx + 5000) // 10000} 万 token。无关新事先建议 aiw handoff 交接到新会话；"
                             "同一件事继续即可；用户拒绝过不再提。")
            with tempfile.NamedTemporaryFile(mode="w", dir=directory, delete=False) as out:
                temporary = Path(out.name)
                out.write(str(bucket))
            temporary.replace(marker)
            temporary = None
            return ctx, (f"[上下文] 约 {(ctx + 5000) // 10000} 万 token。新档位：同一件事继续，自动压缩兜底；"
                         "无关新事在任务交界建议交接，不打断排查；已拒绝不提。同意后 aiw handoff --template 写文件，aiw handoff <文件>。")
    except Exception:
        return ctx, ""
    finally:
        if fd is not None:
            os.close(fd)
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def project_root(start: Path) -> Path | None:
    p = start.resolve()
    for d in [p, *p.parents]:
        if (d / ".claude" / "router-rules.json").exists() or (d / ".git").exists():
            return d
    return None


class RuleError(Exception):
    pass


def check_rules(data: dict, src: Path) -> tuple[list[str], list[dict]]:
    """校验结构并预编译正则；任何一处不对就整份拒绝，错误里带文件和规则 id。"""
    if not isinstance(data, dict):
        raise RuleError(f"{src}: 顶层应为对象")
    escape = data.get("escape", [])
    if not isinstance(escape, list) or not all(isinstance(e, str) for e in escape):
        raise RuleError(f"{src}: escape 应为字符串列表")
    rules = data.get("rules", [])
    if not isinstance(rules, list):
        raise RuleError(f"{src}: rules 应为列表")
    out = []
    for i, r in enumerate(rules):
        rid = r.get("id", f"#{i}") if isinstance(r, dict) else f"#{i}"
        if not isinstance(r, dict):
            raise RuleError(f"{src} 规则 {rid}: 应为对象")
        for k, typ in (("id", str), ("seat", str), ("instruction", str), ("patterns", list)):
            if not isinstance(r.get(k), typ):
                raise RuleError(f"{src} 规则 {rid}: 缺少字段 {k} 或类型不对")
        try:
            compiled = [re.compile(p, re.I) for p in r["patterns"]]
        except (re.error, TypeError) as e:
            raise RuleError(f"{src} 规则 {rid}: 正则无效（{e}）") from e
        out.append(dict(r, _re=compiled))
    return escape, out


def read_rules(src: Path) -> tuple[list[str], list[dict], set[str]]:
    try:
        data = json.loads(src.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise RuleError(f"{src}: 读不了或不是合法 JSON（{e}）") from e
    escape, rules = check_rules(data, src)
    disable = data.get("disable", [])
    if not isinstance(disable, list) or not all(isinstance(x, str) for x in disable):
        raise RuleError(f"{src}: disable 应为字符串列表")
    return escape, rules, set(disable)


def local_rules_path():
    explicit = os.environ.get("AIW_ROUTER_RULES")
    if explicit:
        return Path(explicit).expanduser()
    path = Path.home() / ".config/aiw/router-rules.local.json"
    return path if path.exists() or path.is_symlink() else None


def load_rules(cwd: Path, *, strict=False) -> tuple[list[str], list[dict], Path | None]:
    escape, glob_rules, _ = read_rules(HERE / "router-rules.json")
    rules = [dict(r, source="global") for r in glob_rules]
    root = project_root(cwd)
    proj_file = root / ".claude/router-rules.json" if root else None
    layers = [(local_rules_path(), "local")]
    if proj_file and (proj_file.exists() or proj_file.is_symlink()):
        layers.append((proj_file, "project"))
    for path, source in layers:
        if path is None:
            continue
        try:
            extra_escape, extra_rules, disabled = read_rules(path)
        except RuleError as error:
            if strict:
                raise
            print(f"[route] 忽略损坏的{source}规则：{error}", file=sys.stderr)
            continue
        replaced = disabled | {r["id"] for r in extra_rules}
        escape = extra_escape + escape
        if source == "local":
            overrides = {r["id"]: dict(r, source=source) for r in extra_rules}
            existing = {r["id"] for r in rules}
            rules = [overrides[r["id"]] for r in extra_rules if r["id"] not in existing] + [
                overrides.get(r["id"], r) for r in rules
                if r["id"] not in disabled or r["id"] in overrides
            ]
        else:
            rules = [dict(r, source=source) for r in extra_rules] + [r for r in rules if r["id"] not in replaced]
    return escape, rules, root


def check_errand_rule(rule: dict) -> None:
    try:
        errands = errand_names()
    except StateError as e:
        raise RuleError(str(e)) from e
    named = {rule["seat"]}
    for field in ("instruction", "instruction_codex"):
        named.update(re.findall(r"[A-Za-z0-9_][A-Za-z0-9_-]*", rule.get(field, "")))
    if errands.intersection(named) or "errand" in named:
        raise RuleError(f"规则 {rule['id']}: 跑腿供应商不能进入 route，请直接点名 aiw errand")


def route(msg: str, cwd: Path, main: str | None = None) -> dict:
    m = msg.strip()
    escape, rules, root = load_rules(cwd)
    base = {"project": root.name if root else None}
    if not m or (m.startswith("/") and not m.startswith("/goal")):
        return {**base, "rule": None, "escape": False, "instruction": ""}
    if any(e in m for e in escape):
        return {**base, "rule": None, "escape": True, "instruction": ""}
    for r in rules:
        if any(rx.search(m) for rx in r["_re"]):
            check_errand_rule(r)
            ins = r.get("instruction_codex") if main == "codex" and r.get("instruction_codex") else r["instruction"]
            return {**base, "rule": r["id"], "source": r["source"], "seat": r["seat"], "escape": False, "instruction": ins, "main": main}
    return {**base, "rule": None, "escape": False, "instruction": ""}


def check_seats(rules: list[dict], config: dict) -> None:
    known = set(config["seats"])
    for seat in config.get("panel_default", []):
        if seat not in known:
            raise RuleError(f"panel_default: 未知座位 {seat}")
    for seat in config.get("panel_free", []):
        if seat not in known:
            raise RuleError(f"panel_free: 未知座位 {seat}")
        if config["seats"][seat].get("kind") != "agent_use":
            raise RuleError(f"panel_free: {seat} 必须是 agent_use 座位")
        if seat not in config["panel_default"]:
            raise RuleError(f"panel_free: {seat} 不在 panel_default 中")
    commands = BUILTIN_COMMANDS | ROLE_NAMES
    for r in rules:
        check_errand_rule(r)
        if r["seat"] not in known | commands | {"main", "main-plan"}:
            raise RuleError(f"规则 {r['id']}: 未知座位 {r['seat']}")
        for field, main in (("instruction", "claude"), ("instruction_codex", "codex")):
            text = r.get(field, "")
            named = set(re.findall(r"\baiw\s+([A-Za-z0-9_][A-Za-z0-9_-]*)", text))
            unknown = named - known - commands
            if unknown:
                raise RuleError(f"规则 {r['id']} {field}: 未知座位 {', '.join(sorted(unknown))}")
            if r["seat"] == "panel" and "panel_default" not in text:
                text = re.sub(r"\b[A-Z_][A-Z0-9_]*=[A-Za-z0-9_-]+", "", text)
                listed = {s for s in known if re.search(r"(?<![A-Za-z0-9_-])" + re.escape(s) + r"(?![A-Za-z0-9_-])", text)}
                expected = set(config["panel_default"]) - set(config.get("self_seats", {}).get(main, [])) - set(config.get("main_exclude", {}).get(main, []))
                if listed != expected:
                    raise RuleError(f"规则 {r['id']} {field}: 多出 {sorted(listed - expected)}；缺少 {sorted(expected - listed)}")



def main() -> int:
    hook = "--hook" in sys.argv
    ctx, context_text = None, ""
    cross_session = False
    dispatch_off, dispatch_until = False, None
    session_id = None
    try:
        config = load_seats(HERE)
    except ConfigError as error:
        print(f"配置{'警告' if hook else '错误'}：{error}", file=sys.stderr)
        if hook:
            dispatch_off_hook()
        return 0 if hook else 1
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    cwd = Path.cwd()
    main_ = "codex" if "--codex" in sys.argv else ("claude" if "--claude" in sys.argv else None)
    if "--check" in sys.argv:
        try:
            _, rules, root = load_rules(cwd, strict=True)
            check_seats(rules, config)
        except RuleError as e:
            print(e, file=sys.stderr)
            return 1
        print(f"规则有效：{len(rules)} 条（项目 {root.name if root else '无'}）")
        return 0
    if hook:
        if os.environ.get("AIW_NESTED"):
            return 0  # 本身就是被 aiw 外包出来的会话，不再派单
        try:
            payload = json.load(sys.stdin)
        except Exception:
            return 0  # 钩子永远不因解析失败挡住用户消息
        if not isinstance(payload, dict):
            return 0
        if not isinstance(payload.get("prompt", ""), str) or not isinstance(payload.get("cwd", str(cwd)), str):
            return 0
        if "turn_id" in payload and payload.get("session_id"):
            write_tui_session(STATE, payload["session_id"], payload.get("transcript_path"))
        msg = payload.get("prompt", "")
        session_id = payload.get("session_id")
        cwd = Path(payload.get("cwd") or cwd)
        # Codex 的钩子输入带 turn_id（Codex 扩展字段）；Claude Code 没有
        main_ = main_ or ("codex" if "turn_id" in payload else "claude")
        ctx, context_text = context_hint(payload)
        cross_session = msg.lstrip().startswith("<cross-session-message")
        dispatch_off, dispatch_until = dispatch.is_off(STATE)
    else:
        msg = " ".join(args) if args else sys.stdin.read()
    try:
        if cross_session:
            root = project_root(cwd)
            res = {"project": root.name if root else None, "rule": None, "escape": False, "instruction": ""}
        else:
            res = route(msg, cwd, main_)
    except RuleError as e:
        print(f"[route] 全局规则无效，本条不派单：{e}", file=sys.stderr)
        return 0 if hook else 1  # 钩子永远不挡用户消息；命令行要让人看到失败
    except Exception as e:
        if not hook:
            raise
        print(f"[route] 派单异常：{type(e).__name__}: {str(e).replace(chr(10), ' ')}", file=sys.stderr)
        return 0
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": dt.datetime.now().astimezone().isoformat(timespec="seconds"), "host": socket.gethostname(),
                                "project": res["project"], "rule": res["rule"], "source": res.get("source"),
                                "escape": res["escape"], "len": len(msg), "ctx": ctx,
                                **({"dispatch_off": True} if dispatch_off else {}),
                                **({"cross_session": True} if cross_session else {})}, ensure_ascii=False) + "\n")
    except OSError:
        pass
    if hook:
        context = ["[派单] " + res["instruction"]] if res["instruction"] else []
        if dispatch_off:
            # 关闭期间只在会话第一条和本来会派单的消息上提醒，其余轮次不占上下文。
            due = off_notice_due(session_id)  # 先登记会话，命中规则的消息也算提示过
            context = [dispatch_off_text(dispatch_until)] if res["instruction"] or due else []
        hint = findings.hint_text() if not cross_session and not dispatch_off else ""
        if hint:
            context.append(hint)
        if context_text:
            context.append(context_text)
        if context:
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": "\n\n".join(context)}}, ensure_ascii=False))
        return 0
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
