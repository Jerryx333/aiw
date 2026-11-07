#!/usr/bin/env python3
"""每周快照：从账本和派单日志算指标。同一个脚本出，不手写。

    python3 ops/aiw/weekly.py [days=7] [--json]

--json 只输出派单统计；token 和改进缓冲区小节仅在文本模式输出。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import socket
import sys
from collections import Counter
from statistics import median
from pathlib import Path

import os
from env_compat import getenv
STATE = Path(getenv("AIW_STATE", Path(__file__).resolve().parent / "state"))


def coverage_summary(sources, reasons=()):
    reasons = list(dict.fromkeys(reasons))
    for source in sources:
        if not source['exists']:
            reasons.append(f"{source['name']} 不存在")
        if source['bad']:
            reasons.append(f"{source['name']} 有 {source['bad']} 行解析失败")
    total = sum(source['rows'] for source in sources)
    if not total:
        reasons.append('窗口内未读到有效行')
    return dict(sources=sources, level='无数据' if not total else '部分' if reasons else '完整',
                reasons=list(dict.fromkeys(reasons)))


def rows(name: str, since: dt.datetime, sources=None):
    p = STATE / name
    source = dict(name=name, exists=p.exists(), rows=0, bad=0)
    if sources is not None:
        sources.append(source)
    if not source['exists']:
        return []
    out, bad = [], 0
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
            ts = dt.datetime.fromisoformat(r["ts"])
            if ts.tzinfo is None:
                ts = ts.astimezone()  # 老格式没时区，按本机时区算，不丢行
            if ts >= since:
                out.append(r)
        except (ValueError, KeyError, TypeError, AttributeError):
            bad += 1
    source.update(rows=len(out), bad=bad)
    if bad:
        print(f"⚠ {name} 有 {bad} 行解析不了，未计入统计", file=sys.stderr)
    return out


def findings_section(since: dt.datetime, sources=None, reasons=None):
    """Windowed raw counts and current local snapshots; never sync or mutate state."""
    folder = STATE / "findings"
    print("\n## 改进缓冲区")
    records = rows("findings/records.jsonl", since, sources)
    print("### 记录类型（窗口内）")
    if records or (folder / "records.jsonl").exists():
        counts = Counter(r.get("type", "未知") for r in records)
        for kind in sorted(set(counts) | {"answers", "observation", "changes"}):
            print(f"- {kind}: {counts[kind]}")
    else:
        print("无数据")
    observations = [r for r in records if r.get("type") == "observation"]
    answers = [r for r in records if r.get("type") == "answers"]
    print("### observation 来源")
    for source, count in sorted(Counter(r.get("source") or "未记录" for r in observations).items()):
        print(f"- {source}: {count}")
    if not observations:
        print("无数据")
    print("### 按座位（每 100 单以 answers 为分母）")
    ac = Counter(r.get("seat") or "未记录" for r in answers)
    oc = Counter(r.get("seat") or "未记录" for r in observations)
    for seat in sorted(ac.keys() | oc.keys()):
        rate = f"{100 * oc[seat] / ac[seat]:.1f}" if ac[seat] else "无数据"
        print(f"- {seat}: answers {ac[seat]} · observation {oc[seat]} · 每 100 单观察数 {rate}")
    if not ac and not oc:
        print("无数据")
    print("### 回答「无」或空的比例")
    for key in ("guess", "deviation", "abandoned", "fixed"):
        empty = sum(str(r.get(key) or "").strip() in ("", "无") for r in answers)
        ratio = f"{empty}/{len(answers)}（{100 * empty / len(answers):.1f}%）" if answers else "无数据"
        print(f"- {key}: {ratio}")
    quarantine = rows("findings/quarantine.jsonl", since, sources)
    count = len(quarantine) if (folder / "quarantine.jsonl").exists() else "无数据"
    print(f"- quarantine（窗口内）: {count}")

    def snapshot(name):
        path = folder / name
        source = dict(name='findings/' + name, exists=path.exists(), rows=0, bad=0)
        if sources is not None:
            sources.append(source)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ValueError('invalid snapshot')
            # Current snapshots have no windowed rows.
            return value
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            source['bad'] = 1
            return {}

    cache = snapshot("count-cache.json")
    print(f"- count-cache 待处理数: {cache.get('open', '无数据')}")
    print(f"- count-cache 上次评审: {cache.get('last_triage') or '无数据'}")
    processed = rows("findings/status.jsonl", since, sources)
    print("### 记录处理状态（窗口内）")
    for status, count in sorted(Counter(r.get("status") or "未记录" for r in processed).items()):
        print(f"- {status}: {count}")
    print("### 假设状态（当前）")
    hypotheses = snapshot("hypotheses.json")
    statuses = Counter(h.get("status") or "未记录" for h in hypotheses.values() if isinstance(h, dict))
    for status, count in sorted(statuses.items()):
        print(f"- {status}: {count}")
    if not statuses:
        print("无数据")


def tokens_section(ledger):
    print("\n## token（codex 类座位）")
    extra = {r.get("actual") for r in ledger if "tokens" in r
             and isinstance(r.get("actual"), str) and r.get("actual")} - {"codex", "codex-sol"}
    calls = [r for r in ledger if r.get("rc") is not None]
    for actual in ("codex", "codex-sol", *sorted(extra)):
        attempts = [r for r in calls if r.get("actual") == actual]
        if not attempts:
            print(f"- {actual}: 无数据")
            continue
        rollout, bad = [], 0
        for r in attempts:
            tokens = r.get("tokens")
            if tokens is not None and not isinstance(tokens, dict):
                bad += 1
            elif isinstance(tokens, dict) and tokens.get("source") == "rollout":
                if all(type(tokens.get(key)) is int and tokens[key] >= 0
                       for key in ("input", "cached", "output", "calls")):
                    rollout.append(tokens)
                else:
                    bad += 1
        fallback = sum(isinstance(r.get("tokens"), dict) and r["tokens"].get("source") == "stderr" for r in attempts)
        nulls = Counter(r.get("tokens_null_reason") or "未记录" for r in attempts if "tokens" in r and r["tokens"] is None)
        reasons = "、".join(f"{reason} {count}" for reason, count in sorted(nulls.items())) or "无"
        old = sum("tokens" not in r for r in attempts)
        line = (f"- {actual}: 总尝试 {len(attempts)} · rollout {len(rollout)}（覆盖率 {100 * len(rollout) / len(attempts):.1f}%）"
                f" · stderr 兜底 {fallback} · tokens=null {sum(nulls.values())}（{reasons}） · 旧记录 {old} · 坏记录 {bad}")
        if rollout:
            medians = " / ".join(f"{median(r[key] for r in rollout):g}" for key in ("input", "cached", "output", "calls"))
            inputs, cached, outputs = (sum(r[key] for r in rollout) for key in ("input", "cached", "output"))
            ratio = f"{100 * cached / inputs:.1f}%" if inputs else "无数据"
            line += (f" · rollout 每单中位 input / cached / output / calls: {medians}"
                     f" · 合计 input {inputs}、cached {cached}（缓存占比 {ratio}）、output {outputs}")
        else:
            line += " · rollout 用量无数据"
        print(line)
    print("Claude 主会话用量见 aiw tokens")


def group_stats(attempts):
    total = len(attempts)
    ok_rows = [r for r in attempts if r["kind"] == "ok"]
    timeouts = sum(r["kind"] == "timeout" for r in attempts)
    return {"attempts": total, "ok": len(ok_rows),
            "success_rate": len(ok_rows) / total, "timeouts": timeouts,
            "timeout_rate": timeouts / total,
            "median_ok_secs": median(r["secs"] for r in ok_rows) if ok_rows else None}


def repo_key(row):
    if row.get("repo"):
        return row["repo"], "repo"
    if row.get("workdir"):
        return row["workdir"], "workdir"
    return None, "unknown"


def survival(row, generated):
    """缺证据不计零；每单按记录的去重行求比例。"""
    try:
        stamp = dt.datetime.fromisoformat(row['ts']).astimezone()
        if generated - stamp < dt.timedelta(hours=48):
            return None
        evidence = json.loads((Path(row['run_dir']) / 'changes.json').read_text(encoding='utf-8'))
        root = Path(evidence['workdir'])
        if not root.is_dir():
            return None
        survived, total = 0, 0
        for name, lines in evidence['files'].items():
            path = root / name
            # 证据只允许仓库内路径。
            if not path.resolve().is_relative_to(root.resolve()):
                return None
            try:
                current = {line.strip() for line in path.read_text(encoding='utf-8').splitlines()}
            except FileNotFoundError:
                current = set()
            total += len(lines)
            survived += sum(line in current for line in lines)
        return survived / total if total else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def quality_stats(attempts, generated):
    from rating import latest_ratings, run_id, task_class, RATINGS
    ratings = latest_ratings(STATE)
    groups = {}
    for row in attempts:
        if row.get('kind') == 'ok':
            groups.setdefault((row['actual'], task_class(row)), []).append(row)
    result = []
    for (actual, category), group in sorted(groups.items()):
        counts = Counter(ratings[run_id(row['run_dir'])]['rating'] for row in group
                         if row.get('run_dir') and run_id(row['run_dir']) in ratings)
        rated = sum(counts.values())
        verifies = [r['verify'] for r in group if isinstance(r.get('verify'), dict)
                    and not r['verify'].get('skipped')
                    and type(r['verify'].get('passed')) is int
                    and type(r['verify'].get('total')) is int and r['verify']['total'] > 0]
        verify_total = sum(v['total'] for v in verifies)
        verify_passed = sum(v['passed'] for v in verifies)
        lives = [value for row in group if category == 'write'
                 and (value := survival(row, generated)) is not None]
        adoption = (counts['ok'] + counts['fixed']) / rated if rated else None
        rework = (counts['rework'] + counts['discard']) / rated if rated else None
        live = sum(lives) / len(lives) if lives else None
        suggestions = []
        if rated >= 10:
            if rework >= .3:
                suggestions.append(f'建议该类任务少派 {actual}')
            if adoption >= .9 and (len(lives) < 10 or live >= .8):
                suggestions.append(f'{actual} 做 {category} 质量稳定，可多派')
        result.append(dict(actual=actual, task_class=category, successful=len(group), rated=rated,
                           coverage=rated / len(group), ratings={key: counts[key] for key in RATINGS},
                           adoption_rate=adoption, rework_rate=rework,
                           verify_passed=verify_passed, verify_total=verify_total,
                           verify_rate=verify_passed / verify_total if verify_total else None,
                           survival_rate=live, survival_samples=len(lives), suggestions=suggestions))
    return result


def print_quality(quality):
    def ratio(value, samples):
        label = f'{value * 100:.1f}%' if value is not None else '无数据'
        return label + ('（样本不足）' if samples < 10 else '')
    print('\n## 按座位 × 任务类别的质量')
    for row in quality:
        parts = [f"- {row['actual']} × {row['task_class']}: 成功单 {row['successful']} · 已评分 {row['rated']}"
                 f"（评分覆盖率 {row['coverage'] * 100:.1f}%）"]
        if row['rated']:
            parts.extend([' / '.join(f'{key} {value}' for key, value in row['ratings'].items()),
                          f"采纳率 {ratio(row['adoption_rate'], row['rated'])}",
                          f"返工率 {ratio(row['rework_rate'], row['rated'])}"])
        else:
            parts.append('未评分')
        if row['verify_total']:
            parts.append(f"verify 通过率 {ratio(row['verify_rate'], row['verify_total'])}（{row['verify_passed']}/{row['verify_total']}）")
        if row['survival_samples']:
            parts.append(f"改动存活率 {ratio(row['survival_rate'], row['survival_samples'])}（样本 {row['survival_samples']}）")
        print(' · '.join(parts))
    suggestions = [suggestion for row in quality for suggestion in row['suggestions']]
    for suggestion in suggestions or ['暂无足够数据给建议']:
        print(f'- {suggestion}')


def report(days):
    generated = dt.datetime.now().astimezone()
    since = generated - dt.timedelta(days=days)
    from ai_worker import substitute_limits
    coverage_sources = []
    led = rows("seat-ledger.jsonl", since, coverage_sources)
    rl = rows("router-log.jsonl", since, coverage_sources)
    calls = [r for r in led if r.get("rc") is not None]
    attempts = [r for r in calls if not r.get("freecheck") and not r["kind"].startswith("skipped_")]
    seat_groups, repo_groups = {}, {}
    for row in attempts:
        seat_groups.setdefault(row["actual"], []).append(row)
        repo_groups.setdefault(repo_key(row), []).append(row)
    seats = []
    for actual, group in seat_groups.items():
        sources = Counter(r.get("via") for r in group)
        seats.append({"actual": actual, **group_stats(group),
                      "failures": dict(Counter(r["kind"] for r in group if r["kind"] != "ok").most_common()),
                      "via": {**{via: sources[via] for via in ("direct", "panel", "map", "flow")},
                              "unknown": sources[None]}})
    seats.sort(key=lambda r: (-r["attempts"], r["actual"]))
    repos = []
    for (repo, key_type), group in repo_groups.items():
        changed = [r["changed_files"] for r in group if type(r.get("changed_files")) is int]
        repos.append({"repo": repo, "key_type": key_type, **group_stats(group),
                      "median_changed_files": median(changed) if changed else None})
    repos.sort(key=lambda r: (-r["attempts"], r["repo"] or "未记录"))
    data = {"host": socket.gethostname(), "days": days, "since": since.isoformat(),
            "generated_at": generated.isoformat(), "calls": len(calls),
            "router": {"total": len(rl), "hit": sum(bool(r["rule"]) for r in rl),
                       "escape": sum(bool(r["escape"]) for r in rl)},
            "kinds": dict(Counter(r["kind"] for r in calls).most_common()),
            "efforts": [{"seat": seat, "effort": effort, "n": n} for (seat, effort), n in
                        sorted(Counter((r["seat"], r["effort"]) for r in calls if r.get("effort") is not None).items())],
            "seats": seats, "repos": repos, "quality": quality_stats(attempts, generated),
            "substituted_ok": sum(bool(r.get("substituted")) and r["kind"] == "ok" for r in calls),
            "substitute_limits": substitute_limits(), "coverage": coverage_summary(coverage_sources)}
    text_data = {"calls": calls, "ledger": led,
                 "seat_counts": sorted(Counter((r.get("host"), r["seat"], r["actual"]) for r in calls).items()),
                 "ok_secs": [r["secs"] for r in calls if r["kind"] == "ok"],
                 "rule_counts": Counter((r.get("project"), r["rule"]) for r in rl if r["rule"]).most_common()}
    return data, text_data


def print_report(data, text_data):
    import contextlib
    import io
    sources = list(data['coverage']['sources'])
    reasons = []
    with contextlib.redirect_stdout(io.StringIO()) as section:
        findings_section(dt.datetime.fromisoformat(data['since']), sources, reasons)
    data['coverage'] = coverage_summary(sources, reasons)
    print(f"顶替上限（每日，仅成功顶替计数）：{data['substitute_limits']}")
    generated = dt.datetime.fromisoformat(data["generated_at"])
    print(f"# aiw 周报  {data['host']}  最近 {data['days']} 天  ({generated.date()})\n")
    coverage = data['coverage']
    detail = '（' + '；'.join(coverage['reasons']) + '）' if coverage['reasons'] else ''
    print(f"数据覆盖：{coverage['level']}{detail}")
    print('覆盖只说明读到多少，不代表结论可信度')
    router = data["router"]
    print(f"外包调用 {data['calls']} 次；派单判定 {router['total']} 条，命中 {router['hit']}，逃生口 {router['escape']}\n")
    print("## 按座位（请求座位 → 实际执行方）")
    for (h, s, a), n in text_data["seat_counts"]:
        print(f"- [{h}] {s} → {a}: {n}")
    print("\n## 推理档位")
    for r in data["efforts"]:
        print(f"- {r['seat']} / {r['effort']}: {r['n']}")
    print("\n## 结果分类")
    for k, n in data["kinds"].items():
        print(f"- {k}: {n}")
    print("\n## 按座位成功率")
    print("口径：按实际执行座位；不计 freecheck=true 和 skipped_*；成功与超时按 kind 判断；失败分布取前三项。")
    for r in data["seats"]:
        elapsed = f"{r['median_ok_secs']:g}s" if r['median_ok_secs'] is not None else "无数据"
        distribution = "、".join(f"{kind} {count}" for kind, count in list(r["failures"].items())[:3]) or "无"
        sources = r["via"]
        print(f"- {r['actual']}: 尝试 {r['attempts']} · 成功 {r['ok']} · 成功率 {r['success_rate'] * 100:.1f}%"
              f" · 超时 {r['timeouts']}（{r['timeout_rate'] * 100:.1f}%）"
              f" · 成功单中位耗时 {elapsed} · 失败分布 {distribution}"
              f" · direct {sources['direct']} · panel {sources['panel']} · "
              f"map {sources['map']} · flow {sources.get('flow', 0)} · 未记录 {sources['unknown']}")
    print_quality(data["quality"])
    print("\n## 按仓库")
    for r in data["repos"]:
        elapsed = f"{r['median_ok_secs']:g}s" if r['median_ok_secs'] is not None else "无数据"
        changed = f"{r['median_changed_files']:g}" if r['median_changed_files'] is not None else "无数据"
        print(f"- {r['repo'] or '未记录'}: 尝试 {r['attempts']} · 成功 {r['ok']} · 成功率 {r['success_rate'] * 100:.1f}%"
              f" · 超时 {r['timeouts']}（{r['timeout_rate'] * 100:.1f}%）"
              f" · 成功单中位耗时 {elapsed} · 写任务改动文件中位数 {changed}")
    print(f"\n被顶替但记成功：{data['substituted_ok']} 次（这个数不为 0 时，普通派单看对应 run 目录的 provider 文件，panel/map 看各自目录的 <座位>.provider）")
    secs = text_data["ok_secs"]
    if secs:
        print(f"成功单平均耗时 {sum(secs)/len(secs):.0f}s，最长 {max(secs):.0f}s")
    tokens_section(text_data["ledger"])
    print("\n## 派单规则命中")
    for k, n in text_data["rule_counts"]:
        print(f"- {k}: {n}")
    print(section.getvalue(), end="")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("days", nargs="?", type=int, default=7)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.days < 1:
        parser.error("天数须大于 0")
    data, text_data = report(args.days)
    if args.json:
        print(json.dumps(data, ensure_ascii=False))
    else:
        print_report(data, text_data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
