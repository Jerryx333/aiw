#!/usr/bin/env python3
"""会诊：同一个 prompt 同时发给几家（只读、不顶替），收齐各自的独立意见。

    aiw panel <workdir> <timeout_s> <prompt_file|prompt文本> [seat ...] [--attach 文件 ...] [--prior 文件] [--free] [--full] [--text]
    --free 使用 panel_free 免费座位，同样去掉主会话自己；不能同时显式指定座位。
    --attach 预检读取一次，渲染进共用 prompt 快照，各家（含 CLI 座位）收到同一份全文；合计超过 30KB 提醒。
    --text 逐座位传给 worker，workspace 座位禁用工具；附件保留不能假设读取其它文件的提示。
    默认要求最多 4 条、1500 字的证据清单，并在 stdout 内联回答（每家最多显示 3000 字）。
    --full 不追加格式要求，stdout 不内联各家回答（全文在会诊目录的 <座位>.md）。
    --prior 主会话事先写好的判断，只归档并展示给主会话，不发给座位。
    默认座位见 seats.json 的 panel_default，去掉主会话自己
    （Claude Code / pi / omp 里自动识别；Codex 里请带 AIW_MAIN=codex）。

每家的输出在 state/panels/<时间>/<座位>.md，实际执行方在 <座位>.provider。
不顶替（--no-fallback）：会诊要的是不同模型家族的独立视角，一家挂了就记为缺席，
不能让另一家替它再答一遍冒充独立意见。
返回码：0 至少两家成功且有产出（rc == 0 且 chars > 0）；1 不足两家。
默认名单不足两家可用时不派单；显式单家仍展示结果，但不构成会诊，返回码为 1。
summary.json 的 sufficient 表示成功家数是否达到 required（固定为 2）。
"""
from __future__ import annotations

import concurrent.futures as cf
import datetime as dt
import hashlib
import json
import os
from env_compat import getenv
import subprocess
import sys
from pathlib import Path

from ai_worker import PANEL_FORMAT, atomic_write_bytes
from harness_ids import format_panel_id

HERE = Path(__file__).resolve().parent
STATE = Path(getenv("AIW_STATE", HERE / "state"))
ADJUDICATION = """## 主会话裁决（逐条填写）
| # | 意见要点（来源座位） | 裁决：接受 / 驳回 / 暂缓 | 理由或证据（path:line、命令输出） |
| --- | --- | --- | --- |
| 1 | | | |

收敛条件：每条关键意见都有裁决；驳回必须给理由；暂缓写明等什么。不以多数票收敛。
"""
from seats_config import ConfigError, load_seats, forward_command
try:
    SEATS = load_seats(HERE)
except ConfigError as error:
    print(f"配置错误：{error}", file=sys.stderr)
    raise SystemExit(1)


def ask(seat: str, workdir: str, timeout: str, prompt_file: str, out: Path, full: bool = False, text_mode: bool = False) -> dict:
    env = dict(os.environ, AIW_PANEL_DIR=str(out), AIW_PROVIDER_FILE=str(out / f"{seat}.provider"))
    env.update(AIW_PANEL_FORMAT="0" if full else "1", AIW_ATTACH_WARNING_SUPPRESS="1")
    argv = [sys.executable, str(HERE / "ai_worker.py"), seat, workdir, timeout, prompt_file, "--ro", "--no-fallback"]
    if text_mode:
        argv.append("--text")
    limit = int(timeout) + float(os.environ.get("AIW_PANEL_GRACE", "90"))
    p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    try:
        stdout, stderr = p.communicate(timeout=limit)
    except subprocess.TimeoutExpired:
        p.terminate()  # worker 负责清理供应商进程组
        try:
            p.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()
            p.communicate()
        return {"seat": seat, "rc": 124, "chars": 0, "err": [f"会诊外层超时（{limit}s）"]}
    (out / f"{seat}.md").write_text(stdout, encoding="utf-8")
    if stderr.strip():
        (out / f"{seat}.stderr.txt").write_text(stderr, encoding="utf-8")
    return {"seat": seat, "rc": p.returncode, "chars": len(stdout), "err": stderr.strip().splitlines()[-1:]}


def main() -> int:
    sys.path.insert(0, str(HERE))
    from ai_worker import split_argv, read_attachments, render_attachments, warn_large_attachments, AttachError, agent_use_workspace, main_agent, disabled_seat, print_disabled, errand_names, StateError
    try:
        values = {}
        args, flags, attach = split_argv(sys.argv[1:], {"--attach", "--free", "--prior", "--full", "--text"}, values=values)
    except AttachError as e:
        print(e, file=sys.stderr)
        return 64
    if len(args) < 3:
        print(__doc__, file=sys.stderr)
        return 64
    workdir, timeout, prompt_file, *seats = args
    prior = None
    if "prior" in values:
        try:
            prior = Path(values["prior"]).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError, ValueError) as e:
            print(f"读不了 prior 文件 {values['prior']}：{e}", file=sys.stderr)
            return 64
    else:
        print("未提供 --prior：建议先写下自己的判断再看意见", file=sys.stderr)
    free = "--free" in flags
    if free and seats:
        print("--free 与显式座位二选一，不能同时使用", file=sys.stderr)
        return 64
    try:
        forwarding = bool(forward_command(SEATS))
    except ConfigError as e:
        print(e, file=sys.stderr)
        return 64
    from ai_worker import seat_policy_unavailable, seat_unavailable
    unavailable = seat_policy_unavailable if forwarding else seat_unavailable
    explicit = bool(seats)
    try:
        errands = errand_names()
    except StateError as e:
        print(e, file=sys.stderr)
        return 64
    if errands.intersection(seats if explicit else (SEATS["panel_free"] if free else SEATS.get("panel_default", []))):
        print("跑腿供应商不能进入 panel，请改用 aiw errand", file=sys.stderr)
        return 64
    from ai_worker import main_excluded
    excluded = main_excluded()
    if explicit:
        for seat in seats:
            if seat in excluded:
                print(f"主会话 {main_agent()} 的 main_exclude 跳过座位 {seat}", file=sys.stderr)
        seats = [seat for seat in seats if seat not in excluded]
    if not explicit:
        me = main_agent()
        skip = set(SEATS.get("self_seats", {}).get(me, [])) | excluded
        defaults = SEATS["panel_free"] if free else SEATS.get("panel_default", [])
        seats = []
        for seat in defaults:
            if seat in skip:
                continue
            reason = unavailable(seat)
            if reason:
                print(f"跳过不可用座位 {seat}: {reason}", file=sys.stderr)
            else:
                seats.append(seat)
        if free:
            print(f"免费会诊：{', '.join(seats)}", file=sys.stderr)
        if me:
            print(f"主会话是 {me}，会诊不问它自己：{', '.join(seats)}", file=sys.stderr)
    if not explicit and len(seats) < 2:
        print("会诊需要至少两家可用座位；单家安装可接入 agent-use 文本座位", file=sys.stderr)
        return 1
    if len(set(seats)) != len(seats):
        print("座位重复：会诊要的是不同来源的独立意见", file=sys.stderr)
        return 64
    unknown = [s for s in seats if s not in SEATS["seats"]]
    if unknown:
        print(f"未知座位 {unknown}；可用：{list(SEATS['seats'])}", file=sys.stderr)
        return 64
    from ai_worker import selection_snapshot
    snapshot = selection_snapshot()
    active = []
    for seat in seats:
        reason = unavailable(seat, snapshot=snapshot)
        if reason:
            print(f"跳过不可用座位 {seat}: {reason}", file=sys.stderr)
        else:
            active.append(seat)
    seats = active
    if not seats:
        print("会诊没有可用座位", file=sys.stderr)
        return 1
    required_opinions = 2
    if len(seats) == 1:
        print("可用座位只有 1 家，单家意见不构成会诊，返回码为 1；在本地配置 panel_default 中加座位可获得多家意见", file=sys.stderr)
    try:
        attachment_files = read_attachments(str(Path(workdir).resolve()), attach)  # 先校验一遍，坏附件不发给任何一家
    except AttachError as e:
        print(e, file=sys.stderr)
        return 64
    if attachment_files:
        warn_large_attachments(attachment_files)
    if not Path(prompt_file).is_file():
        # 第三个参数不是文件就当 prompt 文本，落盘后再分发
        text, prompt_file = prompt_file, None
    out = STATE / "panels" / format_panel_id(dt.datetime.now(), os.getpid())
    out.mkdir(parents=True, exist_ok=True)
    prompt = Path(prompt_file).read_text(encoding="utf-8") if prompt_file else text
    workspace_context = "--text" not in flags and all(agent_use_workspace(SEATS["seats"][s]) for s in seats)
    (out / "prompt.md").write_text(prompt + render_attachments(attachment_files, "workspace" if workspace_context else "agent_use"), encoding="utf-8")
    if attachment_files:
        manifest = [{"path": rel, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                    for rel, data in attachment_files]
        atomic_write_bytes(out / "attachments.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
    prompt_file = str(out / "prompt.md")  # 各家共用这一份快照
    if prior is not None:
        (out / "prior.md").write_text(prior, encoding="utf-8")
        print("## 主会话先验判断\n" + prior, flush=True)
    (out / "adjudication.md").write_text(ADJUDICATION, encoding="utf-8")
    with cf.ThreadPoolExecutor(max_workers=len(seats)) as ex:
        results = list(ex.map(lambda s: ask(s, workdir, timeout, prompt_file, out, "--full" in flags, "--text" in flags), seats))
    ok = [r for r in results if r["rc"] == 0 and r["chars"] > 0]
    sufficient = len(ok) >= required_opinions
    summary = {"dir": str(out), "seats": results, "answered": [r["seat"] for r in ok],
               "sufficient": sufficient, "required": required_opinions}
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"会诊目录：{out}")
    for r in results:
        mark = "✓" if r in ok else "✗"
        print(f"  {mark} {r['seat']:11s} rc={r['rc']} {r['chars']} 字" + ("" if r in ok else f"  {r['err'][0] if r['err'] else ''}"))
    if "--full" not in flags:
        for r in ok:
            path = out / f"{r['seat']}.md"
            answer = path.read_text(encoding="utf-8")
            print(f"\n### {r['seat']}")
            print(answer[:3000])
            if len(answer) > 3000:
                print(f"…（超长已截断，全文：{path}）")
    print("各家意见是待审稿：主会话逐条对照证据，分歧保留，不以多数票收敛。")
    print(ADJUDICATION)
    if not sufficient:
        print(f"⚠ 有效意见不足 {required_opinions} 家，不能当作会诊结论。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
