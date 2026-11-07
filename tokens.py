#!/usr/bin/env python3
"""只读统计 Claude Code 转录与派单账本；不输出消息正文。"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import re
from env_compat import getenv
import sys
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median

FIELDS = ('input', 'cache_read', 'cache_creation', 'output')
USAGE = ('input_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens', 'output_tokens')
WEIGHTS = dict(input=1, cache_read=0.1, cache_creation=1.25, output=5)
STATE = Path(getenv('AIW_STATE', Path(__file__).resolve().parent / 'state'))
PHASES = ('发起派单', '主会话改代码', '跑测试/验收', '提交/PR', '等待派单',
          '写单/草稿', '调查/读取', '子代理', '文字回复', '其他')
DISPATCH = re.compile(r'\b(?:aiw|ai_worker\.py)\s+(?:codex-sol|codex|write|claude-write|goal\s+run|review|draft|panel|workbuddy-cn|workbuddy|qoduck|qoder|opencode|doubao|qianwen|claude|map)(?=\s|$|[;|&])')
TEST = re.compile(r'\b(?:pytest|npm\s+(?:run\s+)?test|go\s+test|cargo\s+test|node\s+--test|bun\s+test|swift\s+test|xcodebuild|make)\b|\S*check\.sh\b|tests/test_harness\.py\b|\baiw\s+goal\s+verify\b')
COMMIT = re.compile(r'\bgit\s+(?:-[^\s]+\s+(?:[^\s]+\s+)?)*?(?:commit|push|merge|rebase|tag)\b|\bgh\s+(?:pr|issue)\b')
WAIT = re.compile(r'\bsleep\s+\d+(?:\.\d+)?(?=\s|$|[;|&])|\buntil\s|\bps\s+aux\b|\bpgrep\b|/tasks/[^/\s]+\.output\b')
SKIP_TURN = ('<task-notification', '<system-reminder', '<local-command', 'Caveat:',
             '[Request interrupted', '<bash-', 'This session is being continued')


def phase(content):
    tags = set()
    for block in content if isinstance(content, list) else []:
        if not isinstance(block, dict) or block.get('type') != 'tool_use':
            continue
        name, args = block.get('name', ''), block.get('input') or {}
        command = args.get('command') or ''
        path = args.get('file_path') or args.get('notebook_path') or ''
        scratch = any(s in path for s in ('/private/tmp', '/tmp/', 'scratchpad'))
        if name == 'Bash':
            tags.add(next((label for pattern, label in (
                (DISPATCH, '发起派单'), (TEST, '跑测试/验收'),
                (COMMIT, '提交/PR'), (WAIT, '等待派单')) if pattern.search(command)), '调查/读取'))
        elif name in ('Edit', 'Write', 'MultiEdit', 'NotebookEdit') and not scratch:
            tags.add('主会话改代码')
        elif name in ('Monitor', 'TaskOutput'):
            tags.add('等待派单')
        elif name in ('Edit', 'Write') and scratch:
            tags.add('写单/草稿')
        elif name in ('Read', 'Grep', 'Glob') or 'codegraph' in name.lower():
            tags.add('调查/读取')
        elif name == 'Agent':
            tags.add('子代理')
        else:
            tags.add('其他')
    return next((label for label in PHASES if label in tags), '文字回复')


def user_turn(row):
    if row.get('type') != 'user' or row.get('isMeta'):
        return False
    content = row.get('message', {}).get('content')
    if isinstance(content, list):
        if any(isinstance(b, dict) and b.get('type') == 'tool_result' for b in content):
            return False
        content = '\n'.join(b.get('text', '') for b in content
                            if isinstance(b, dict) and b.get('type') == 'text')
    return isinstance(content, str) and bool(content.strip()) and not content.strip().startswith(SKIP_TURN)


def phase_summary(latest, labels, starts):
    counts, costs, events = Counter(), Counter(), defaultdict(list)
    for unique, (key, child, turn, values, _) in latest.items():
        if child or not any(values):
            continue
        label, first = labels[unique]
        cost = sum(value * WEIGHTS[field] for field, value in zip(FIELDS, values))
        counts[label] += 1
        costs[label] += cost
        events[key].append((first, 1, turn[1], cost))
    for key, entries in starts.items():
        events[key].extend((position, 0, 0, 0) for position in entries)
    turns = []
    for entries in events.values():
        current = None
        for _, kind, ctx, cost in sorted(entries):
            if kind == 0:
                current = [None, 0]
                turns.append(current)
            elif current is not None:
                if current[0] is None:
                    current[0] = ctx
                current[1] += cost
    turns = [t for t in turns if t[0] is not None]
    total = sum(costs.values())
    phases = {label: dict(rounds=counts[label], weighted=costs[label], share=ratio(costs[label], total))
              for label in PHASES}
    start = dict(turns=len(turns), ctx_median=median(t[0] for t in turns) if turns else 0)
    for n in (200000, 300000):
        start[f'over_{n // 1000}k'] = dict(
            turns_share=ratio(sum(t[0] > n for t in turns), len(turns)),
            weighted_share=ratio(sum(t[1] for t in turns if t[0] > n), sum(t[1] for t in turns)))
    return dict(phases=phases, wait_per_dispatch=ratio(counts['等待派单'], counts['发起派单'])
                if counts['发起派单'] else None, turn_start=start)


def timestamp(value):
    ts = dt.datetime.fromisoformat(value)
    return ts if ts.tzinfo else ts.astimezone()


def ratio(a, b):
    return a / b if b else 0


def summary(turns):
    contexts = sorted(t[1] for t in turns)
    return dict(rounds=len(turns), tokens=sum(t[2] for t in turns),
                ctx_median=median(contexts) if contexts else 0,
                ctx_p90=contexts[math.ceil(len(contexts) * .9) - 1] if contexts else 0,
                ctx_max=max(contexts, default=0))


def report(days, top, phases=False):
    end = dt.datetime.now().astimezone()
    since = end - dt.timedelta(days=days)
    root = Path(os.environ.get('AIW_CLAUDE_PROJECTS',
                str(Path(os.environ.get('CLAUDE_CONFIG_DIR', '~/.claude')).expanduser() / 'projects'))).expanduser()
    totals = Counter({f: 0 for f in FIELDS})
    main, children = defaultdict(list), defaultdict(list)
    projects, images, dispatch_counts, codex = {}, Counter(), Counter(), Counter()
    latest, bad = {}, 0
    labels, starts, sequence = {}, defaultdict(list), 0
    root_source = dict(name='transcripts', exists=root.exists(), rows=0, bad=0,
                       files=0, files_with_rows=0)
    sources = [root_source]
    # Only known transcript layouts; never load entire files into memory.
    for pattern, child in (('*/*.jsonl', False), ('*/*/subagents/agent-*.jsonl', True)):
        for path in root.glob(pattern):
            root_source['files'] += 1
            rows_before = root_source['rows']
            parent = path.parents[1].name if child else path.stem
            project = path.parents[2].name if child else path.parent.name
            with path.open(encoding='utf-8', errors='replace') as stream:
                for line in stream:
                    covered = '"assistant"' in line or '"tool_result"' in line
                    if not covered and not (phases and not child and '"user"' in line):
                        continue
                    try:
                        row = json.loads(line)
                        if not since <= timestamp(row['timestamp']) <= end:
                            continue
                        msg = row.get('message', {})
                        is_child = child or bool(row.get('isSidechain'))
                        sid = parent if is_child else row.get('sessionId') or parent
                        key = (project, sid)
                        sequence += 1
                        position = (timestamp(row['timestamp']), sequence)
                        if phases and not is_child and user_turn(row):
                            starts[key].append(position)
                        if row.get('type') == 'assistant':
                            usage = msg.get('usage') or {}
                            values = [usage.get(f, 0) for f in USAGE]
                            if any(not isinstance(v, int) or isinstance(v, bool) or v < 0 for v in values):
                                raise ValueError('invalid usage')
                            identity = msg.get('id') or row.get('uuid')
                            if not isinstance(identity, str) or not identity:
                                raise ValueError('missing message id and uuid')
                            unique = (key, path.name if child else is_child, identity)
                            if phases and unique in latest and not msg.get('usage'):
                                values = latest[unique][3]
                            turn = (timestamp(row['timestamp']), sum(values[:3]), sum(values))
                            cwd = row.get('cwd')
                            if phases and not is_child:
                                label = phase(msg.get('content'))
                            latest[unique] = (key, is_child, turn, values,
                                Path(cwd).name if isinstance(cwd, str) and cwd else project)
                            if phases and not is_child:
                                if unique in labels:
                                    previous, first = labels[unique]
                                    if label == '文字回复' or (previous != '文字回复' and PHASES.index(previous) < PHASES.index(label)):
                                        label = previous
                                    position = min(first, position)
                                labels[unique] = (label, position)
                        elif row.get('type') == 'user' and not is_child:
                            content = msg.get('content')
                            if isinstance(content, list):
                                for block in content:
                                    if isinstance(block, dict) and block.get('type') == 'tool_result' and isinstance(block.get('content'), list):
                                        images[key] += sum(isinstance(b, dict) and b.get('type') == 'image' for b in block['content'])
                        root_source['rows'] += covered
                    except (ValueError, KeyError, TypeError, AttributeError):
                        bad += covered
                        root_source['bad'] += covered
            root_source['files_with_rows'] += root_source['rows'] > rows_before
    for key, is_child, turn, values, project in latest.values():
        if not any(values):
            continue
        (children if is_child else main)[key].append(turn)
        if not is_child:
            totals.update(dict(zip(FIELDS, values)))
            projects[key] = project
    actual = Counter()
    rollout = {seat: dict(input=0, cached=0, output=0) for seat in ('codex', 'codex-sol')}
    linked, calls = 0, 0
    ledger = STATE / 'seat-ledger.jsonl'
    ledger_source = dict(name='seat-ledger.jsonl', exists=ledger.exists(), rows=0, bad=0)
    sources.append(ledger_source)
    if ledger.exists():
        with ledger.open(encoding='utf-8', errors='replace') as stream:
            for line in stream:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                    if not since <= timestamp(row['ts']) <= end or row.get('rc') is None:
                        continue
                    seat = row.get('actual') or 'unknown'
                    usage = row.get('tokens')
                    measured = isinstance(usage, dict) and usage.get('source') == 'rollout'
                    values = {f: usage.get(f) for f in ('input', 'cached', 'output')} if measured else {}
                    if any(not isinstance(v, int) or isinstance(v, bool) or v < 0 for v in values.values()):
                        raise ValueError('invalid ledger usage')
                    ledger_source['rows'] += 1
                    calls += 1
                    actual[seat] += 1
                    sid = row.get('session')
                    if sid:
                        linked += 1
                        dispatch_counts[sid] += 1
                    if measured:
                        rollout.setdefault(seat, dict(input=0, cached=0, output=0))
                        for f, value in values.items():
                            rollout[seat][f] += value
                        if sid:
                            codex[sid] += values['input']
                except (ValueError, KeyError, TypeError, AttributeError):
                    bad += 1
                    ledger_source['bad'] += 1
    reasons = []
    for source in sources:
        if not source['exists']:
            reasons.append(f"{source['name']} 不存在")
        if source['bad']:
            reasons.append(f"{source['name']} 有 {source['bad']} 行解析失败")
    valid_rows = sum(source['rows'] for source in sources)
    if not valid_rows:
        reasons.append('窗口内未读到有效行')
    coverage = dict(sources=sources, level='无数据' if not root_source['exists'] or not valid_rows
                    else '部分' if reasons else '完整', reasons=reasons)
    rollout = {seat: rollout[seat] for seat in ('codex', 'codex-sol',
               *sorted(set(rollout) - {'codex', 'codex-sol'}))}
    turns = [t for session in main.values() for t in session]
    context = summary(turns)
    ctx_total = sum(t[1] for t in turns)
    fixed = median([min(ts, key=lambda t: t[0])[1] for ts in main.values()]) if main else 0
    context.update(ctx_total=ctx_total, fixed_estimate=fixed,
                   fixed_share=ratio(sum(min(fixed, t[1]) for t in turns), ctx_total))
    context['growth_share'] = 1 - context['fixed_share'] if ctx_total else 0
    context['thresholds'] = {str(n): dict(rounds=sum(t[1] > n for t in turns),
        round_share=ratio(sum(t[1] > n for t in turns), len(turns)),
        token_share=ratio(sum(t[2] for t in turns if t[1] > n), context['tokens'])) for n in (200000, 400000)}
    sessions = [dict(session=sid[:8], project=projects[key], **summary(ts), screenshots=images[key],
                     dispatches=dispatch_counts[sid], codex_input=codex[sid]) for key, ts in main.items() for sid in [key[1]]]
    sessions.sort(key=lambda r: (-r['tokens'], r['session'], r['project']))
    total = sum(totals.values())
    totals = dict(totals, total=total, shares={f: ratio(totals[f], total) for f in FIELDS})
    weighted = {f: totals[f] * WEIGHTS[f] for f in FIELDS}
    weighted.update(total=sum(weighted.values()))
    weighted['shares'] = {f: ratio(weighted[f], weighted['total']) for f in FIELDS}
    sub = [dict(session=key[1][:8], project=projects.get(key, key[0]), **summary(ts)) for key, ts in sorted(children.items())]
    sub_tokens = sum(r['tokens'] for r in sub)
    result = dict(window_days=days, totals=totals, weighted=weighted, context=context,
        concentration={f'top{n}_share': ratio(sum(r['tokens'] for r in sessions[:n]), total) for n in (3, 10)},
        sessions=sessions[:top], subagents=dict(rounds=sum(r['rounds'] for r in sub), tokens=sub_tokens,
        share=ratio(sub_tokens, total + sub_tokens), parents=sub),
        dispatch=dict(count=calls, by_actual=dict(sorted(actual.items())), rollout=rollout,
                      with_session=linked, session_share=ratio(linked, calls)), malformed_lines=bad, coverage=coverage)
    if bad:
        print(f'⚠ 转录和账本有 {bad} 行解析不了，未计入统计', file=sys.stderr)
    if phases:
        result.update(phase_summary(latest, labels, starts))
    return result


def print_report(d):
    coverage = d['coverage']
    detail = '（' + '；'.join(coverage['reasons']) + '）' if coverage['reasons'] else ''
    print(f"数据覆盖：{coverage['level']}{detail}")
    print('覆盖只说明读到多少，不代表结论可信度')
    if not d['context']['rounds'] and not d['subagents']['rounds'] and not d['dispatch']['count']:
        print('无数据')
        if 'phases' in d:
            print_phases(d)
        return
    pct = lambda v: f'{v * 100:.2f}%'
    print(f"最近 {d['window_days']} 天 · Claude Code token 测量")
    print('\n构成（主会话，token = ctx + output）')
    for f in FIELDS:
        print(f"{f}: {d['totals'][f]} · {pct(d['totals']['shares'][f])} · 折算 {pct(d['weighted']['shares'][f])}")
    print('订阅额度的计量权重未公开，折算仅供参考（input 1、cache_creation 1.25、cache_read 0.1、output 5）')
    c = d['context']
    print(f"\n上下文：主会话 {c['rounds']} 轮 · ctx 中位 {c['ctx_median']:g} · p90 {c['ctx_p90']:g} · 最大 {c['ctx_max']}")
    for n, v in c['thresholds'].items():
        print(f"ctx > {n}: {v['rounds']} 轮 · 轮次 {pct(v['round_share'])} · token {pct(v['token_share'])}")
    print(f"固定开销估计 F {c['fixed_estimate']:g} · 固定 {pct(c['fixed_share'])} · 增长 {pct(c['growth_share'])}")
    print(f"\n会话集中度：前 3 {pct(d['concentration']['top3_share'])} · 前 10 {pct(d['concentration']['top10_share'])}")
    print('session 项目 轮数 ctx中位/最大 token 截图 派单 codex_input')
    for r in d['sessions']:
        # Strip control characters from metadata before terminal output.
        project = ''.join(ch for ch in r['project'] if ch.isprintable())
        print(f"{r['session']} {project} {r['rounds']} {r['ctx_median']:g}/{r['ctx_max']} {r['tokens']} {r['screenshots']} {r['dispatches']} {r['codex_input']}")
    s = d['subagents']
    print(f"\n子 agent：{s['rounds']} 轮 · token {s['tokens']} · 全局占比 {pct(s['share'])}")
    for r in s['parents']:
        print(f"{r['session']} · {r['rounds']} 轮 · token {r['tokens']}")
    a = d['dispatch']
    print(f"\n派单关联：{a['count']} 次 · 有 session {a['with_session']}（{pct(a['session_share'])}）")
    for seat, count in a['by_actual'].items():
        print(f'{seat}: {count} 次')
    for seat, usage in a['rollout'].items():
        print(f"{seat} rollout: input {usage['input']} · cached {usage['cached']} · output {usage['output']}")
    if 'phases' in d:
        print_phases(d)


def print_phases(d):
    print('\n环节（主会话，加权）')
    for label, value in sorted(d['phases'].items(), key=lambda item: -item[1]['share']):
        if value['rounds']:
            print(f"{label}: {value['rounds']} 轮 · {value['share']:.1%} · 每轮 {value['weighted'] / value['rounds'] / 1000:.1f}k")
    wait = d['wait_per_dispatch']
    print('每单等待轮数: ' + ('无派单' if wait is None else f'{wait:.2f}'))
    start = d['turn_start']
    thresholds = ' · '.join(f">{n}k 回合 {start[f'over_{n}k']['turns_share']:.1%} 花费 {start[f'over_{n}k']['weighted_share']:.1%}" for n in (200, 300))
    print(f"回合起点上下文：{start['turns']} 回合 · 中位 {start['ctx_median'] / 1000:g}k · {thresholds}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('days', nargs='?', type=int, default=7)
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--phases', action='store_true')
    parser.add_argument('--top', type=int, default=10)
    args = parser.parse_args()
    if args.days < 1 or args.top < 0:
        parser.error('天数须大于 0，--top 须非负')
    data = report(args.days, args.top, args.phases)
    if args.json:
        print(json.dumps(data, ensure_ascii=False))
    else:
        print_report(data)
    return 0


if __name__ == '__main__':
    sys.exit(main())
