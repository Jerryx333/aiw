#!/usr/bin/env python3
"""/goal 任务清单与保险丝。

    goal.py init  <goal.json>          从一句话目标生成空清单骨架（需要人填 tasks）
    goal.py next  <goal.json>          打印下一条 passes=false 的任务（按 id 顺序）
    goal.py verify <goal.json> [id] [--force] [--accept-verify-change] [--full]  跑 verify 命令，通过就把 passes 改 true；不传 id 则复验全部自动任务（相关文件未变可跳过）
    goal.py status <goal.json>         汇总：通过/未过/人工阻塞/轮次，是否触发保险丝

保险丝：max_rounds（默认 25）；连续 stall_rounds（默认 3）轮没有任务从 false 变 true 就停。
verify 只看产物和退出码，不看模型说「做完了」。
"""
from __future__ import annotations

import datetime as dt
from platform_lock import acquire_lock, release_lock
import hashlib
import json
import shlex
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ai_worker import STATE, run_group  # noqa: E402

TEMPLATE = {
    "goal": "",
    "created": "",
    "human_blockers": ["示例：需要用户在 WorkBuddy 官方流程完成登录"],
    "fuse": {"max_rounds": 25, "stall_rounds": 3},
    "rounds": [],
    "tasks": [
        {"id": "T1", "title": "示例：给 scripts/audit_*.py 补 smoke 测试", "owner": "codex",
         "verify": "cd {root} && .venv/bin/python -m pytest -q tests/test_audit_smoke.py", "passes": False,
         "paths": ["scripts/audit_*.py", "tests/test_audit_smoke.py"]},
    ],
}


def load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def save(p: Path, d: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(p)  # 原子替换，中断不会留下半份清单


class goal_lock:
    """整个 读—验收—写 周期独占；锁用独立文件，原子替换清单不影响它。"""

    def __init__(self, p: Path):
        # 锁放 aiw 的 state 里，不在项目目录留 .lock 文件
        STATE.mkdir(parents=True, exist_ok=True)
        self.path = STATE / ("goal-" + hashlib.sha1(str(p.resolve()).encode()).hexdigest()[:12] + ".lock")

    def __enter__(self):
        self.fh = self.path.open("a")
        try:
            acquire_lock(self.fh, blocking=False)
        except BlockingIOError:
            self.fh.close()
            raise
        return self

    def __exit__(self, *exc):
        release_lock(self.fh)
        self.fh.close()


def cmd_init(p: Path, goal: str) -> int:
    if p.exists():
        print(f"{p} 已存在", file=sys.stderr)
        return 1
    d = dict(TEMPLATE)
    d["goal"], d["created"] = goal, dt.date.today().isoformat()
    save(p, d)
    print(f"已生成 {p}，请填 tasks（每条必须有能执行的 verify，verify 不能比 title 宽松）")
    return 0


def cmd_next(p: Path) -> int:
    pending = [t for t in load(p)["tasks"] if not t["passes"]]
    for t in pending:
        if t.get("owner") != "human":
            print(json.dumps(t, ensure_ascii=False, indent=2))
            return 0
    print("只剩人工任务：" + "；".join(f"{t['id']} {t['title']}" for t in pending) if pending else "全部通过")
    return 0


def run_verify(t: dict, root: Path) -> tuple[bool, str]:
    cmd = t["verify"].replace("{root}", shlex.quote(str(root)))
    try:
        r = run_group(cmd, t.get("timeout", 600), shell=True, cwd=root)
    except subprocess.TimeoutExpired:
        return False, f"verify 超时（{t.get('timeout', 600)}s）"
    return r.returncode == 0, (r.stdout + r.stderr)[-1500:]


def cmd_verify(p: Path, only: str | None, force: bool = False, accept_change: bool = False, full: bool = False) -> int:
    try:
        with goal_lock(p):
            return _verify(p, only, force, accept_change, full)
    except BlockingIOError:
        print(f"{p} 正在被另一个 verify 使用，稍后再跑。", file=sys.stderr)
        return 75


def verify_sha(t: dict) -> str:
    return hashlib.sha256(t["verify"].encode()).hexdigest()[:16]


def paths_sha(t: dict, root: Path) -> str | None:
    """对去重、排序后的相对路径及内容做哈希；空匹配不建立缓存。"""
    patterns = t.get("paths", [])
    if not isinstance(patterns, list) or any(
        not isinstance(pattern, str) or not pattern or Path(pattern).is_absolute()
        or ".." in Path(pattern).parts for pattern in patterns
    ):
        raise ValueError(f"[{t['id']}] paths 必须是相对 root 的 glob 字符串列表")
    files = set()
    for pattern in patterns:
        for match in root.glob(pattern):
            if match.is_dir():
                files.update(f for f in match.rglob("*") if f.is_file())
            elif match.is_file():
                files.add(match)
    if not files:
        return None
    digest = hashlib.sha256()
    for path in sorted(files, key=lambda f: f.relative_to(root).as_posix()):
        name = path.relative_to(root).as_posix().encode("utf-8")
        content = path.read_bytes()
        # 长度前缀避免文件名、内容及文件边界拼接歧义。
        digest.update(len(name).to_bytes(8, "big"))
        digest.update(name)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()[:16]


def fuse_reason(d: dict) -> str:
    rounds, fuse = d["rounds"], d.get("fuse", TEMPLATE["fuse"])
    if len(rounds) >= fuse["stall_rounds"] and all(not r["flipped"] for r in rounds[-fuse["stall_rounds"]:]):
        return f"连续 {fuse['stall_rounds']} 轮无进展"
    return "达到最大轮次" if len(rounds) >= fuse["max_rounds"] else ""


def _verify(p: Path, only: str | None, force: bool = False, accept_change: bool = False, full: bool = False) -> int:
    d = load(p)
    reason = fuse_reason(d)
    if reason and not force:
        print(f"⛔ 保险丝触发：{reason}；继续需 --force", file=sys.stderr)
        return 3
    changed = [t["id"] for t in d["tasks"] if t.get("verify_sha") and t["verify_sha"] != verify_sha(t)]
    if changed and not accept_change:
        print("verify 已变更：" + "、".join(changed) + "；确认需 --accept-verify-change", file=sys.stderr)
        return 65
    root = p.resolve().parent
    while root != root.parent and not (root / ".git").exists():
        root = root.parent
    if not (root / ".git").exists():
        root = p.resolve().parent  # 不在 git 仓库里就用清单所在目录，不能退到 /
    if only and not any(t["id"] == only for t in d["tasks"]):
        print(f"未知任务 {only}", file=sys.stderr)
        return 64
    for t in d["tasks"]:
        if t["id"] in changed:
            t["verify_sha"] = verify_sha(t)
            t.pop("paths_sha", None)
    flipped, failed, regressed, skipped = [], [], [], []
    failures = {}
    for t in d["tasks"]:
        if only and t["id"] != only:
            continue
        if t.get("owner") == "human" and not only:
            continue
        if "paths" not in t:
            t.pop("paths_sha", None)
        else:
            current_sha = paths_sha(t, root)
            if current_sha is None:
                print(f"[{t['id']}] paths 没有匹配到文件")
            elif not only and not full and t["passes"] and current_sha == t.get("paths_sha"):
                print(f"[{t['id']}] SKIP 相关文件未变")
                skipped.append(t["id"])
                continue
        t.setdefault("verify_sha", verify_sha(t))
        save(p, d)  # 执行前固定命令，异常退出也不能丢基线
        ok, tail = run_verify(t, root)
        print(f"[{t['id']}] {'PASS' if ok else 'FAIL'}  {t['title']}")
        if not ok:
            failed.append(t["id"])
            failures[t["id"]] = tail
            print("   " + tail.strip().replace("\n", "\n   ")[-600:])
        if not ok and t["passes"]:
            regressed.append(t["id"])
            t["passes"] = False  # 复验失败要撤销之前的通过，不能让旧 PASS 掩盖回归
            t["regressed_at"] = dt.datetime.now().astimezone().isoformat(timespec="seconds")
        if ok and "paths" in t:
            current_sha = paths_sha(t, root)
            if current_sha is None:
                t.pop("paths_sha", None)
            else:
                t["paths_sha"] = current_sha
        if ok and not t["passes"]:
            t["passes"] = True
            t["passed_at"] = dt.datetime.now().astimezone().isoformat(timespec="seconds")
            flipped.append(t["id"])
    round_record = {"ts": dt.datetime.now().astimezone().isoformat(timespec="seconds"), "flipped": flipped, "regressed": regressed, "verify_changed": changed, "skipped": skipped}
    if failures:
        round_record["failures"] = failures
    d["rounds"].append(round_record)
    save(p, d)
    rc = cmd_status(p)
    # 保险丝(3)优先；否则本轮有失败就返回 1，不能让失败的验收看起来像通过
    return rc if rc else (1 if failed else 0)


def cmd_status(p: Path) -> int:
    d = load(p)
    auto = [t for t in d["tasks"] if t.get("owner") != "human"]
    total = len(auto)
    done = sum(t["passes"] for t in auto)
    human_left = [t for t in d["tasks"] if t.get("owner") == "human" and not t["passes"]]
    rounds = d["rounds"]
    fuse = d.get("fuse", TEMPLATE["fuse"])
    print(f"目标：{d['goal']}\n自动任务通过 {done}/{total}，轮次 {len(rounds)}/{fuse['max_rounds']}，人工阻塞 {len(d.get('human_blockers', []))} 项")
    for t in d["tasks"]:
        print(f"  [{'x' if t['passes'] else ' '}] {t['id']} ({t.get('owner','main')}) {t['title']}" + (" ⚠ verify 已变更" if t.get("verify_sha") and t["verify_sha"] != verify_sha(t) else ""))
    regressed = rounds[-1].get("regressed", []) if rounds else []
    if regressed:
        print(f"本轮回归 {len(regressed)} 项：" + "、".join(regressed))
    if done != total and fuse_reason(d):
        print(f"\n⛔ 保险丝触发：{fuse_reason(d)}。停下来汇报，不要继续硬跑。")
        return 3
    if human_left:
        print("\n待你处理：" + "；".join(f"{t['id']} {t['title']}" for t in human_left))
    if done == total:
        print("\n✅ 自动任务全部通过。接下来做一次独立只读评审（aiw review <workdir> 900 <prompt文件>），不要直接宣告完成。宣告完成前先跑一次 aiw goal verify <文件> --full。")
    return 0


def main() -> int:
    if any(a in {"-h", "--help"} for a in sys.argv[1:]):
        print(__doc__)
        return 0
    if len(sys.argv) < 3:
        print(__doc__)
        return 64
    cmd, p = sys.argv[1], Path(sys.argv[2])
    if cmd == "init":
        if sys.argv[2].startswith("-"):
            print("init 目标路径不能以 - 开头", file=sys.stderr)
            return 64
        return cmd_init(p, " ".join(sys.argv[3:]))
    if cmd == "next":
        return cmd_next(p)
    if cmd == "verify":
        flags = {a for a in sys.argv[3:] if a.startswith("--")}
        ids = [a for a in sys.argv[3:] if not a.startswith("--")]
        if flags - {"--force", "--accept-verify-change", "--full"} or len(ids) > 1:
            print("verify 允许 [id] [--force] [--accept-verify-change] [--full]", file=sys.stderr)
            return 64
        return cmd_verify(p, ids[0] if ids else None, "--force" in flags, "--accept-verify-change" in flags, "--full" in flags)
    if cmd == "status":
        return cmd_status(p)
    print(__doc__)
    return 64


if __name__ == "__main__":
    sys.exit(main())
