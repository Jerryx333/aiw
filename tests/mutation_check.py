#!/usr/bin/env python3
# 用途：在临时仓库副本中破坏红线，验证守护测试能拒绝。
# 运行：python3 tests/mutation_check.py [id ...]；依赖：Python 标准库及自测所需 Git/Bash。
from __future__ import annotations

import py_compile
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MUTATIONS = [{'id': 'transient-fallback',
  'expect': 'transient/unavailable 应按链顶替',
  'invariant': 'agent-use transient 失败顶替且不熔断；未知 reason 不自动改派',
  'file': 'ai_worker.py',
  'old': 'if r["kind"] == "transient":',
  'new': 'if False:  # transient 落入 other，停止顶替',
  'tests': ['agent_use_transient'],
  'location': 'dispatch'}, {'id': 'disabled-dispatch', 'expect': '停用座位仍被派单', 'invariant': '停用座位不能派单', 'file': 'ai_worker.py', 'old': '                if disabled:\n                    ledger(', 'new': '                if False:\n                    ledger(', 'tests': ['free_guard'], 'location': 'dispatch'}, {'id': 'deferred-exception',
  'expect': '延迟信号遮蔽真实异常',
  'invariant': '延迟信号不遮蔽关键区真实异常',
  'file': 'ai_worker.py',
  'old': 'if completed and deliver and DEFERRED_DEPTH == 0 and PENDING_SIGNAL is not None:',
  'new': 'if DEFERRED_DEPTH == 0 and PENDING_SIGNAL is not None:',
  'tests': ['signal_windows'],
  'location': 'deferred_signals'},
 {'id': 'startup-deferred', 'expect': '启动窗口未延迟信号', 'invariant': '子进程创建登记完成后才交付中断', 'file': 'ai_worker.py', 'old': '            with deferred_signals():\n                p = subprocess.Popen', 'new': '            with interruptible_signals():\n                p = subprocess.Popen', 'tests': ['signal_startup_window'], 'location': 'run_group'},
 {'id': 'inflight-lock',
  'expect': '重建锁未被持有',
  'invariant': 'worker 持锁表明在途且锁不被子进程继承',
  'file': 'ai_worker.py',
  'old': 'fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)\n            return lock',
  'new': 'pass  # 省略在途锁\n            return lock',
  'tests': ['inflight_liveness_lock'],
  'location': 'acquire_liveness_lock'},
 {'id': 'inflight-lock-cleanup',
  'expect': '只读残留锁未重建',
  'invariant': '拿锁失败不留下假锁文件',
  'file': 'ai_worker.py',
  'old': '                path.unlink(missing_ok=True)',
  'new': '                pass  # 省略失败锁清理',
  'tests': ['inflight_liveness_lock'],
  'location': 'acquire_liveness_lock'},
 {'id': 'credentials',
  'expect': "'.env ' 应被拒（凭据）",
  'invariant': '凭据类附件拒发',
  'file': 'ai_worker.py',
  'old': 'return any(SENSITIVE_SEG.match(x) for x in segs) or bool(SENSITIVE_PATH.search("/".join(segs)))',
  'new': 'return False',
  'tests': ['attach'],
  'location': 'sensitive'},
 {'id': 'workdir',
  'expect': '../outside.py 应被拒（不在工作目录）',
  'invariant': '附件不出 workdir',
  'file': 'ai_worker.py',
  'old': 'if not f.is_relative_to(root):',
  'new': 'if False:',
  'tests': ['attach'],
  'location': 'build_attachments'},
 {'id': 'nested',
  'expect': '嵌套未拒绝',
  'invariant': 'AIW_NESTED 不再派单',
  'file': 'ai_worker.py',
  'old': 'if os.environ.get("AIW_NESTED") == "1" and "--allow-nested" not in flags:',
  'new': 'if False:',
  'tests': ['nested_dispatch'],
  'location': 'dispatch'},
 {'id': 'write-fallback',
  'expect': '写任务不能由文本座位伪成功',
  'invariant': '写任务不顶替给只出文本的座位',
  'file': 'ai_worker.py',
  'old': 'if not ro and text_only_seat(s):',
  'new': 'if False:',
  'tests': ['write_fallback_text_seat'],
  'location': 'dispatch'},
 {'id': 'text-write',
  'expect': '文本座位写任务应拒绝',
  'invariant': '只出文本的座位不接写任务',
  'file': 'ai_worker.py',
  'old': 'if mode == \'rw\' and role is None and text_only_seat(seat) and explicit_chain is None:\n        print(f"[{seat}] 这个座位只生成文本',
  'new': 'if False:\n        print(f"[{seat}] 这个座位只生成文本',
  'tests': ['worker_regression'],
  'location': 'dispatch'},
 {'id': 'sol-fallback',
  'expect': 'codex-sol 认证失败应 rc=1',
  'invariant': 'codex-sol 不顶替',
  'file': 'seats.json',
  'old': '"codex-sol": [],',
  'new': '"codex-sol": ["codex"],',
  'tests': ['codex_sol_no_fallback'],
  'location': 'fallback'},
 {'id': 'timeout',
  'expect': 'agent-use 报 timeout 应 rc=124',
  'invariant': '超时不换座位',
  'file': 'ai_worker.py',
  'old': 'if r["kind"] == "timeout":',
  'new': 'if False:',
  'tests': ['worker_regression'],
  'location': 'dispatch'},
 {'id': 'success-first',
  'expect': '成功回答含 quota 字样不应熔断',
  'invariant': 'rc=0 且有输出优先判 ok，回答谈 quota/401 不熔断',
  'file': 'ai_worker.py',
  'old': 'if rc == 0 and out.strip():',
  'new': 'if rc == 0 and out.strip() and "quota" not in err.lower():',
  'tests': ['worker_regression'],
  'location': 'classify'},
 {'id': 'tool-calls',
  'expect': '工具调用残骸漏判',
  'invariant': '工具调用伪答复必须识别',
  'file': 'ai_worker.py',
  'old': 'return residue < 200 if has_opening else residue <= 10',
  'new': 'return False',
  'tests': ['tool_call_detection'],
  'location': 'looks_like_tool_calls'},
 {'id': 'verify-sha',
  'expect': '变更应阻止所有执行',
  'invariant': 'goal verify 命令防篡改',
  'file': 'goal.py',
  'old': 'if changed and not accept_change:',
  'new': 'if False:',
  'tests': ['goal_verify_integrity'],
  'location': '_verify'},
 {'id': 'goal-fuse',
  'expect': '保险丝未阻止执行',
  'invariant': 'goal 保险丝在执行前判定',
  'file': 'goal.py',
  'old': 'if reason and not force:',
  'new': 'if False:',
  'tests': ['goal_fuse_preflight'],
  'location': '_verify'},
 {'id': 'panel-fallback',
  'expect': '一家缺席不应被顶替',
  'invariant': '会诊不顶替',
  'file': 'panel.py',
  'old': '"--ro", "--no-fallback"]',
  'new': '"--ro"]',
  'tests': ['panel'],
  'location': 'ask'},
 {'id': 'write-zero',
  'expect': '写任务零变化未提示',
  'invariant': '写任务零变化必须提示',
  'file': 'ai_worker.py',
  'old': 'if self.ok and n == 0:',
  'new': 'if False:',
  'tests': ['write_changes_report'],
  'location': 'WorkChanges.__exit__'},
 {'id': 'output-file',
  'expect': 'CLI output.md 未落盘',
  'invariant': 'CLI output.md 必须落盘',
  'file': 'ai_worker.py',
  'old': 'output_saved = write_run_file(run_dir / "output.md", out)',
  'new': 'pass  # omit output archive',
  'tests': ['run_dir_persist'],
  'location': 'run_seat'},
 {'id': 'codex-trust',
  'expect': '缺少 ⚠ Codex 信任',
  'invariant': 'Codex 信任检查不能恒为通过',
  'file': 'doctor_config.py',
  'old': 'def trust(home, hp, mine):',
  'new': 'def trust(home, hp, mine):\n    for g, i, _ in mine:\n        print(f"  ✓ Codex 信任 [{g}:{i}]（只能确认有信任记录，无法确认是否过期）")\n    return',
  'tests': ['doctor_config'],
  'location': 'trust'},
 {'id': 'locked',
  'expect': '库锁账本分类或座位错误',
  'invariant': '运行中库锁识别为 locked，不熔断、不顶替',
  'file': 'ai_worker.py',
  'old': 'if LOCKED_PAT.search(text):',
  'new': 'if False:',
  'tests': ['codex_locked_runtime'],
  'location': 'classify'},
 {'id': 'inflight-contract',
  'expect': 'inflight 字段缺失',
  'invariant': 'inflight 字段符合状态文件约定',
  'file': 'ai_worker.py',
  'old': '                            "nested": os.environ.get("AIW_NESTED") == "1",\n',
  'new': '',
  'tests': ['test_state_contract'],
  'location': 'dispatch'}]


MUTATIONS.extend([
    {'id': 'write-fallback-ro-only', 'expect': '写单调用了 ro_only workspace 顶替',
     'invariant': '写单跳过只读 workspace 顶替', 'file': 'ai_worker.py',
     'old': 'if not ro and (SEATS["seats"][s].get("ro_only") or SEATS["seats"][s].get("force_mode") in ("ro", "text")):', 'new': 'if False:',
     'tests': ['workspace_write_fallback_skips_ro_only'], 'location': 'dispatch'},
    {'id': 'panel-mixed-workspace-scope', 'expect': 'panel 附件范围提示错误',
     'invariant': '混合名单保留附件范围提示', 'file': 'panel.py',
     'old': 'all(agent_use_workspace(SEATS["seats"][s]) for s in seats)',
     'new': 'any(agent_use_workspace(SEATS["seats"][s]) for s in seats)',
     'tests': ['workspace_panel_mixed_attachment_scope'], 'location': 'main'},
    {'id': 'panel-all-workspace-scope', 'expect': 'panel 附件范围提示错误',
     'invariant': '全 workspace 名单省略附件范围提示', 'file': 'panel.py',
     'old': '"workspace" if workspace_context else "agent_use"', 'new': '"agent_use"',
     'tests': ['workspace_panel_all_workspace_attachment_scope'], 'location': 'main'},
    {'id': 'panel-text-workspace-scope', 'expect': 'panel 附件范围提示错误',
     'invariant': 'text 场景保留附件范围提示', 'file': 'panel.py',
     'old': '"--text" not in flags and all(', 'new': 'all(',
     'tests': ['workspace_panel_text_attachment_scope'], 'location': 'main'},
    {'id': 'cleanup-deferred-exception', 'expect': '清理区延迟信号遮蔽 Popen 真实异常',
     'invariant': '延迟信号不遮蔽清理区正在传播的真实异常', 'file': 'ai_worker.py',
     'old': 'with deferred_signals(deliver=sys.exc_info()[0] is None):',
     'new': 'with deferred_signals():',
     'tests': ['signal_windows'], 'location': 'run_group'},
    {'id': 'executor-watchdog', 'expect': 'aiw SIGKILL 后执行方仍存活',
     'invariant': 'aiw 强杀后看门狗清理独立执行方进程组', 'file': 'ai_worker.py',
     'old': '        os.killpg(pgid, sig)', 'new': '        pass  # EOF 后不清理执行方',
     'tests': ['executor_watchdog'], 'location': 'WATCHDOG_CODE'},
    {'id': 'codex-adapter-name', 'expect': '别名写单判定失效',
     'invariant': 'Codex 专属行为按 adapter 而非座位名启用', 'file': 'ai_worker.py',
     'old': 'return (seat_adapter(seat) == "codex" and cfg.get("kind") != "agent_use"',
     'new': 'return (seat in ("codex", "codex-sol") and cfg.get("kind") != "agent_use"',
     'tests': ['codex_adapter_alias'], 'location': 'codex_workspace_write'},
    {'id': 'panel-opinion-threshold', 'expect': '会诊门槛应固定两家',
     'invariant': '会诊至少两家成功且有产出，单家不能降低门槛', 'file': 'panel.py',
     'old': 'required_opinions = 2', 'new': 'required_opinions = min(2, len(seats))',
     'tests': ['panel_opinion_threshold'], 'location': 'main'},
    {'id': 'errand-registry', 'expect': '损坏 errand 名单未拒绝',
     'invariant': '已存在的 errand 名单必须严格校验', 'file': 'ai_worker.py',
     'old': '    if not isinstance(data, dict) or type(data.get("version")) is not int or data["version"] != 1:',
     'new': '    if not isinstance(data, dict):',
     'tests': ['errand_registry_corrupt_rejected'], 'location': 'errand_names'},
    {'id': 'errand-panel', 'expect': 'errand panel 未拒绝',
     'invariant': 'errand 不进入 panel', 'file': 'panel.py',
     'old': 'if errands.intersection(seats if explicit else (SEATS["panel_free"] if free else SEATS.get("panel_default", []))):', 'new': 'if False:',
     'tests': ['errand_panel_rejects'], 'location': 'main'},
    {'id': 'errand-route', 'expect': 'errand 路由未拒绝',
     'invariant': 'errand 不进入运行时 route', 'file': 'route.py',
     'old': '            check_errand_rule(r)\n', 'new': '',
     'tests': ['errand_route_runtime_rejects'], 'location': 'route'},
    {'id': 'errand-freecheck', 'expect': 'errand freecheck 未跳过',
     'invariant': 'freecheck 不探测 errand', 'file': 'freecheck.py',
     'old': 'if seat in errands:', 'new': 'if False:',
     'tests': ['errand_freecheck_skips'], 'location': 'check'},
])


MUTATIONS.extend([
    {'id': 'triage-evidence', 'expect': 'evidence gate bypass',
     'invariant': '支持及试修验证必须有非线索证据', 'file': 'triage.py',
     'old': "valid_evidence(h.get('evidence'), nonclue=needs_evidence)",
     'new': "valid_evidence(h.get('evidence'), nonclue=False)",
     'tests': ['triage_validate_core'], 'location': 'validate'},
    {'id': 'triage-baseline', 'expect': 'no improvement accepted',
     'invariant': '已验证必须有可比且改进的基线复验', 'file': 'triage.py',
     'old': "comparison['value'] < baseline['value']",
     'new': "comparison['value'] <= baseline['value']",
     'tests': ['triage_baseline_verified'], 'location': 'validate'},
    {'id': 'findings-open', 'expect': 'status count semantics',
     'invariant': 'unexplained 观察仍计入 open', 'file': 'findings.py',
     'old': 'in (None, "unexplained")', 'new': 'in (None,)',
     'tests': ['findings_local_cache_hint'], 'location': 'recompute_count_cache'},
    {'id': 'schema-retry-once', 'expect': '重交后应成功',
     'invariant': '结构化结果不合格先同座位重交一次，不直接报错',
     'file': 'structured.py', 'old': 'if attempts >= 2:', 'new': 'if attempts >= 1:',
     'tests': ['structured_retry_then_ok'], 'location': 'run'},
    {'id': 'schema-format-ledger', 'expect': '账本：',
     'invariant': '结构化结果不合格的尝试在账本记 kind=format（不记成功）',
     'file': 'ai_worker.py', 'old': '                            if schema_errors:\n',
     'new': '                            if False:\n',
     'tests': ['structured_retry_then_ok'], 'location': 'dispatch'},
    {'id': 'flow-reuse', 'expect': '已完成的单不能重派',
     'invariant': '流水线续跑复用钥匙相同且已完成的单，不重派',
     'file': 'flow.py', 'old': '        cached = self.store.reusable(job)', 'new': '        cached = None',
     'tests': ['flow_resume'], 'location': 'Scheduler.add'},
    {'id': 'flow-parent-key', 'expect': '改评审说明应重跑评审及下游',
     'invariant': '上游单钥匙进下游钥匙：上游说明改了，下游即使输入相同也重跑',
     'file': 'flow.py', 'old': '[p.key for p in parents]', 'new': '[]',
     'tests': ['flow_resume'], 'location': 'Scheduler.add'},
])


MUTATIONS.extend([
    {'id': 'forward-resolved-chain', 'expect': '转发链不符合本机解析结果',
     'invariant': '转发必须携带本机解析的完整链', 'file': 'ai_worker.py',
     'old': "argv += ['--chain', ','.join(chain)]",
     'new': "argv += ['--chain', chain[0]]",
     'tests': ['forward_resolved_chain'], 'location': 'forward_dispatch'},
    {'id': 'receiver-chain-boundary', 'expect': '显式链执行越界或顺序错误',
     'invariant': '接收端不得展开自己的 fallback', 'file': 'ai_worker.py',
     'old': '        chain = explicit',
     'new': "        chain = [explicit[0]] + SEATS['fallback'].get(explicit[0], [])",
     'tests': ['receiver_explicit_chain'], 'location': 'dispatch_chain'},
])


def run_tests(repo, cases):
    return subprocess.run([sys.executable, "tests/test_harness.py", *cases],
                          cwd=repo, capture_output=True, text=True)


def main(args):
    by_id = {m["id"]: m for m in MUTATIONS}
    if len(by_id) != len(MUTATIONS):
        print("错误：重复变异 id", file=sys.stderr)
        return 1
    unknown = set(args) - by_id.keys()
    if unknown:
        print("错误：未知变异 id：" + ", ".join(sorted(unknown)), file=sys.stderr)
        return 1
    selected = [by_id[k] for k in dict.fromkeys(args)] if args else MUTATIONS
    cases = list(dict.fromkeys(c for m in selected for c in m["tests"]))
    with tempfile.TemporaryDirectory(prefix="aiw-mutation-") as td:
        clean = Path(td) / "clean"
        shutil.copytree(REPO, clean, ignore=shutil.ignore_patterns(
            ".git", "state", "snapshots", "__pycache__"))
        baseline = run_tests(clean, cases)
        if baseline.returncode != 0 or any(
                not re.search(r"^PASS " + re.escape(c) + r"$", baseline.stdout, re.M) for c in cases):
            print("基线失败：停止变异自检\n" + baseline.stdout + baseline.stderr)
            return 1
        print(f"基线通过：{len(cases)} 个用例", flush=True)
        counts = Counter()
        for mutation in selected:
            work = Path(td) / "mutant"
            shutil.copytree(clean, work, ignore=shutil.ignore_patterns("__pycache__"))
            try:
                target = work / mutation["file"]
                source = target.read_text(encoding="utf-8")
                detail = ""
                if source.count(mutation["old"]) != 1:
                    result = "补丁失效"
                else:
                    target.write_text(source.replace(mutation["old"], mutation["new"], 1), encoding="utf-8")
                    try:
                        if target.suffix == ".py":
                            py_compile.compile(str(target), doraise=True)
                    except py_compile.PyCompileError as exc:
                        result, detail = "补丁导致语法错误", str(exc)
                    else:
                        run = run_tests(work, mutation["tests"])
                        output = run.stdout + run.stderr
                        failures = re.findall(r"^FAIL ([^:\n]+): ([^\n]*)$", output, re.M)
                        expected_failure = any(
                            case in mutation["tests"] and mutation["expect"] in message
                            for case, message in failures)
                        if run.returncode != 0 and expected_failure:
                            result = "已杀死"
                        elif failures:
                            result = "被无关失败杀死"
                        else:
                            result = "存活"
                        detail = "\n".join(f"FAIL {case}: {message}" for case, message in failures) if failures else output
                counts[result] += 1
                print(f"{mutation['id']}: {result} — {mutation['invariant']}", flush=True)
                if detail:
                    print(detail, flush=True)
            finally:
                shutil.rmtree(work)
        print("汇总：" + "，".join(f"{key} {value}" for key, value in counts.items()))
        return 0 if counts["已杀死"] == len(selected) else 1


if __name__ == "__main__":
    sys.path.insert(0, str(REPO))
    import slot  # 每个变异体都复制仓库并重跑用例，整次自检占一个全机名额
    with slot.reservation([sys.executable, __file__, *sys.argv[1:]], label="aiw mutation_check"):
        slot.lower_to(10)
        sys.exit(main(sys.argv[1:]))
