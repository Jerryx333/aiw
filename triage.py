#!/usr/bin/env python3
"""Build local review batches and apply validated decisions locally."""
from __future__ import annotations
import collections
import datetime as dt
from platform_lock import acquire_lock
import json
import math
import os
import socket
import sys
from pathlib import Path
import findings as f
STATUSES = {'explained', 'unexplained', 'stale', 'wontfix', 'unverifiable'}
HYP_STATUSES = {'待补证', '支持', '反驳', '试修', '验证中', '已验证', '已放弃'}
EVIDENCE_KINDS = {'已见后果', '明文要求', '确定缺陷', '线索'}
LAYERS = {'机制', '项目规则', '代码'}
KEYWORDS = '找不到 不一致 被覆盖 冲突 超时 格式 猜 手动 重复 过期 权限 绕过'.split()
TEMPLATE = {
    'self_cause_considered': '本批是否有症状由主会话自己的规则或派单模板引起：……',
    'hypotheses': [{'id': 'H-YYYYMMDD-1', 'title': '', 'mechanism': '',
                    'explains': ['记录id'], 'predicts': ['若成立还应看到'],
                    'refuted_by': ['看到什么说明它错了'], 'competing': [],
                    'status': '待补证', 'layer': '机制', 'fix': {'commit': None},
                    'evidence': {'kind': '线索', 'refs': ['记录id']},
                    'baseline': {'metric': '每 100 单 timeout 观察数', 'direction': 'down',
                                 'window': '2026-10-01..2026-10-07', 'value': 10, 'evidence': ['基线输出文件路径']},
                    'comparison': {'window': '2026-10-08..2026-10-14', 'value': 5,
                                   'task_mix': '可比', 'evidence': ['复验输出文件路径']},
                    'probe': '', 'expect_down': [], 'expect_flat': [], 'window_days': 7}],
    'records': [{'id': '记录id', 'status': 'unexplained', 'hypothesis': '', 'note': ''}],
}
def now():
    return dt.datetime.now(dt.timezone.utc)
def host():
    return socket.gethostname().split('.')[0]
def folder():
    return f.STATE / 'findings'
def read(path, default=None):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default
def append(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as stream:
        acquire_lock(stream)
        f.complete_line(stream)
        for row in rows:
            stream.write((json.dumps(row, ensure_ascii=False) + '\n').encode('utf-8'))
        stream.flush()
        os.fsync(stream.fileno())
class LockConflict(RuntimeError):
    pass
def require_lock(lock):
    current = read(folder() / 'triage.lock', {})
    if any(current.get(k) != lock.get(k) for k in ('host', 'triage_id', 'started')):
        raise LockConflict(f"锁不属于本次 triage；持锁主机 {current.get('host')}，过期时间 {current.get('expires')}")
def latest():
    lock = read(folder() / 'triage.lock', {})
    path = folder() / 'triage' / str(lock.get('triage_id', '')) / 'lock.json'
    if not lock:
        print('请先运行 aiw triage start', file=sys.stderr)
        return None
    if not path.exists():
        raise LockConflict('锁不属于本次 triage')
    lock = read(path)
    if lock.get('host') != host():
        raise LockConflict('本地锁不属于本机')
    return path, lock
def collect():
    rows = {r['id']: r for r in f._rows(folder() / 'records.jsonl')}
    statuses = {r['id']: r for r in f._rows(folder() / 'status.jsonl')}
    return list(rows.values()), statuses
def overview(rows):
    result = {'hosts': dict(collections.Counter(r.get('host') for r in rows))}
    for dimension in ('seat', 'repo'):
        groups = {}
        for r in rows:
            key = r.get(dimension) or '(本机)'
            counts = groups.setdefault(key, {'answers': 0, 'observation': 0})
            if r.get('type') in counts:
                counts[r['type']] += 1
        for counts in groups.values():
            counts['per_100'] = 100 * counts['observation'] / counts['answers'] if counts['answers'] else None
        result[dimension] = groups
    answers = [r for r in rows if r.get('type') == 'answers']
    result['empty_answers'] = {key: {'empty': sum(str(r.get(key) or '').strip() in ('', '无') for r in answers),
                                   'total': len(answers)} for key in ('guess', 'deviation', 'abandoned', 'fixed')}
    latest_rows = {}
    for r in sorted(rows, key=lambda r: f._timestamp(r.get('ts')) or dt.datetime.min.replace(tzinfo=dt.timezone.utc)):
        latest_rows[r.get('host')] = r
    result['rules_hash'] = {h: r.get('rules_hash') for h, r in latest_rows.items()}
    result['rules_mismatch'] = len(set(result['rules_hash'].values())) > 1
    return result
def grouping(rows):
    data = {}
    for key in ('repo', 'repo_rel', 'seat', 'host', 'rules_hash'):
        data[key] = collections.Counter(r.get(key) or '(空)' for r in rows).most_common(15)
    data['keywords'] = {}
    for word in KEYWORDS:
        ids = [r['id'] for r in rows if word in ' '.join(str(r.get(k) or '') for k in ('observation', 'expected', 'evidence'))]
        data['keywords'][word] = {'count': len(ids), 'ids': ids[:5]}
    return data
def render(batch):
    def dump(value):
        return json.dumps(value, ensure_ascii=False, indent=2)
    def cell(value):
        return str(value or '').replace('\n', ' ').replace('\r', ' ').replace('|', '\\|')
    o = batch['overview']
    lines = [f"# Triage {batch['triage_id']}", '', '## 1. 概况', '各主机记录数：' + dump(o['hosts'])]
    for dim in ('seat', 'repo'):
        lines.append(f'### 按 {dim}（answers 为分母）')
        for key, c in o[dim].items():
            rate = f"{c['per_100']:.2f}" if c['per_100'] is not None else 'N/A（无分母）'
            lines.append(f"- {key}: answers={c['answers']}, observation={c['observation']}, 每 100 单观察数={rate}")
    for key, c in o['empty_answers'].items():
        rate = f"{100*c['empty']/c['total']:.2f}%" if c['total'] else 'N/A'
        lines.append(f"- {key} 无或空：{c['empty']}/{c['total']} ({rate})")
    lines.append('各主机最新 rules_hash：' + dump(o['rules_hash']))
    if o['rules_mismatch']:
        lines.append('⚠ 记录规则版本不一致')
    lines.append('## 2. 假设台账')
    for h in batch['hypotheses']:
        lines.append(h['id'])
        lines.extend(['```json', dump(h), '```'])
        if h['due']:
            lines.append('⏰ 到期：跑探针并复验基线')
            baseline = h.get('baseline')
            lines.append('基线：' + ' / '.join(f'{key}={cell(str(baseline.get(key)))}'
                         for key in ('metric', 'direction', 'window', 'value'))
                         if isinstance(baseline, dict) else '无基线')
    lines.append('## 3. 机械信号')
    for signal in batch['signals'].get('overlap', []):
        hosts = '、'.join(sorted({str(r.get('host') or '') for r in signal['records']}))
        lines.append(f"- {cell(signal.get('repo') or signal.get('workdir'))} {cell(signal['file'])} — {len(signal['records'])} 次（主机 {hosts}）")
    for signal in batch['signals'].get('rule_churn', []):
        lines.append(f"- {cell(signal['file'])} / {cell(signal['section'])} — {len(signal['commits'])} 次提交")
        for commit in signal['commits'][:3]:
            lines.append('  - ' + cell(commit['title']))
    for signal in batch['signals'].get('repeated_instruction', []):
        lines.append(f"- 出现 {signal['count']} 次：{cell(signal['line'])}")
    lines.append('## 4. 机械分组')
    for dimension, groups in batch['groups'].items():
        if dimension == 'keywords':
            groups = [(key, value['count']) for key, value in groups.items()]
        for key, count in groups:
            lines.append(f"- {dimension} / {cell(key)} — {count} 条")
    lines.append('## 5. 记录')
    for key, title in (('pool', '未解释池'), ('pending', '本批待处理')):
        lines.append('### ' + title)
        for r in batch[key]:
            lines.append(' | '.join(cell(r.get(k)) for k in ('id', 'source', 'seat')) + ' | ' +
                         cell(r.get('repo') or '(本机)') + ' | ' +
                         ' ／ '.join(cell(r.get(k)) for k in ('observation', 'expected', 'evidence', 'location')))
    lines.extend(['## decisions.json 模板', '待填写的骨架：替换占位符、选择状态，删除不需要的条目；comparison 可选，已验证时必填。',
                  '```json', dump(TEMPLATE), '```',
                  '写好后运行 aiw triage apply <decisions.json>'])
    return '\n'.join(lines) + '\n'
def start(days=60):
    stamp = now()
    tid = host() + '-' + stamp.strftime('%Y%m%d-%H%M%S')
    target = folder() / 'triage' / tid
    if target.exists():
        print('同秒批次已存在，请稍后 start', file=sys.stderr)
        return 75
    lock = {'host': host(), 'pid': os.getpid(), 'triage_id': tid, 'started': stamp.isoformat(),
            'expires': (stamp + dt.timedelta(hours=2)).isoformat()}
    folder().mkdir(parents=True, exist_ok=True)
    # Serialize lease checks and state mutations with the records append lock.
    with (folder() / 'records.lock').open('a') as guard:
        acquire_lock(guard)
        old = read(folder() / 'triage.lock', {})
        expires = f._timestamp(old.get('expires'))
        if old and (expires is None or expires > stamp):
            raise LockConflict(f"持锁主机 {old.get('host')}，过期时间 {old.get('expires')}")
        f._atomic_json(folder() / 'triage.lock', lock)
    f._atomic_json(target / 'lock.json', lock)
    if old:
        print('已接管过期锁')
    rows, statuses = collect()
    signals = f.extract(14)
    saved_hypotheses = read(folder() / 'hypotheses.json', {})
    pending = [r for r in rows if r.get('type') == 'observation' and r['id'] not in statuses]
    pool = [r for r in rows if r.get('type') == 'observation' and
            statuses.get(r['id'], {}).get('status') in ('unexplained', 'unverifiable') and
            (f._timestamp(r.get('ts')) or dt.datetime.min.replace(tzinfo=dt.timezone.utc)) >= stamp - dt.timedelta(days=days)]
    hypotheses = []
    for hid, value in saved_hypotheses.items():
        if value.get('status') in ('已验证', '已放弃'):
            continue
        h = dict(value, id=hid, due=False)
        updated = f._timestamp(h.get('updated'))
        window = h.get('window_days')
        if h.get('status') in ('试修', '验证中') and updated and type(window) is int and window > 0:
            h['due'] = updated + dt.timedelta(days=window) <= stamp
        hypotheses.append(h)
    batch = {'triage_id': tid, 'created': stamp.isoformat(), 'days': days, 'records': rows,
             'pending': pending, 'pool': pool, 'overview': overview(rows), 'hypotheses': hypotheses,
             'signals': signals, 'groups': grouping(pending + pool)}
    f._atomic_json(target / 'batch.json', batch)
    (target / 'batch.md').write_text(render(batch), encoding='utf-8')
    print(f"{target / 'batch.md'}\n本批待处理 {len(pending)}；未解释池 {len(pool)}")
    return 0
def nonempty(value):
    return isinstance(value, str) and bool(value.strip())
def string_refs(value, allow_empty=False):
    return isinstance(value, list) and (allow_empty or bool(value)) and all(nonempty(x) for x in value)
def valid_evidence(value, nonclue=False):
    return (isinstance(value, dict) and isinstance(value.get('kind'), str)
            and value['kind'] in EVIDENCE_KINDS
            and (not nonclue or value['kind'] != '线索')
            and string_refs(value.get('refs'), allow_empty=value['kind'] == '线索'))
def valid_measurement(value, baseline=False):
    if not isinstance(value, dict):
        return False
    number = value.get('value')
    if (type(number) not in (int, float) or (type(number) is float and not math.isfinite(number))
            or not nonempty(value.get('window')) or not string_refs(value.get('evidence'))):
        return False
    if baseline:
        return nonempty(value.get('metric')) and value.get('direction') in ('down', 'up')
    return value.get('task_mix') in ('可比', '未核实', '不可比')
def secret_paths(value, path, context):
    if isinstance(value, str):
        return [path] if f.has_secret(value, context) else []
    if isinstance(value, dict):
        result = []
        for key, item in value.items():
            # Keys can themselves contain user-provided secrets; never echo them.
            if f.has_secret(str(key), context):
                result.append(path + '.<key>')
            else:
                result.extend(secret_paths(item, path + '.' + str(key), context))
        return result
    if isinstance(value, list):
        return [hit for i, item in enumerate(value) for hit in secret_paths(item, f'{path}[{i}]', context)]
    return []
def validate(data, batch=None, existing=None, *, context=None):
    errors = []
    if not isinstance(data, dict):
        return ['decisions 必须为对象']
    keys = ('hypotheses', 'records')
    groups = {}
    for key in keys:
        value = data.get(key, [])
        if not isinstance(value, list) or any(not isinstance(x, dict) for x in value):
            errors.append(f'{key} 必须为对象列表')
            value = []
        groups[key] = value
    if batch is not None:
        if not nonempty(data.get('self_cause_considered')):
            errors.append('self_cause_considered 必须为非空字符串')
        ids = {r['id'] for r in batch['pending'] + batch['pool']}
        record_ids = {r['id'] for r in batch['records']}
        known = set(existing or {}) | {h['id'] for h in groups['hypotheses'] if nonempty(h.get('id'))}
        for h in groups['hypotheses']:
            for key in ('id', 'title', 'mechanism'):
                if not nonempty(h.get(key)):
                    errors.append(f'假设 {key} 必须为非空字符串')
            for key in ('explains', 'predicts', 'refuted_by'):
                if not isinstance(h.get(key), list) or not h[key] or not all(nonempty(x) for x in h[key]):
                    errors.append(f'假设 {key} 必须为非空字符串列表')
            if isinstance(h.get('explains'), list):
                for rid in h['explains']:
                    if not isinstance(rid, str) or rid not in record_ids:
                        errors.append('explains 引用不在本批')
            if not isinstance(h.get('status'), str) or h['status'] not in HYP_STATUSES:
                errors.append('假设 status 不合法')
            if not isinstance(h.get('layer'), str) or h['layer'] not in LAYERS:
                errors.append('假设 layer 不合法')
            needs_evidence = h.get('status') in ('支持', '试修', '验证中', '已验证')
            if needs_evidence or 'evidence' in h:
                if not valid_evidence(h.get('evidence'), nonclue=needs_evidence):
                    errors.append('假设 evidence 必须含合法 kind 和非空字符串 refs 列表；支持/试修/验证中/已验证不得为线索，仅线索允许空 refs')
            needs_baseline = h.get('status') in ('试修', '验证中', '已验证')
            baseline_ok = valid_measurement(h.get('baseline'), baseline=True)
            comparison_ok = valid_measurement(h.get('comparison'))
            if (needs_baseline or 'baseline' in h) and not baseline_ok:
                errors.append('假设 baseline 必须含非空 metric/window/evidence、down/up direction 和有限数字 value（不能为 bool）'
                              + ('；应保持「验证中」或改「已放弃」' if h.get('status') == '已验证' else ''))
            if (h.get('status') == '已验证' or 'comparison' in h) and not comparison_ok:
                errors.append('假设 comparison 必须含非空 window/evidence、合法 task_mix 和有限数字 value（不能为 bool）；应保持「验证中」或改「已放弃」')
            if h.get('status') == '已验证' and baseline_ok and comparison_ok:
                baseline, comparison = h['baseline'], h['comparison']
                improved = (comparison['value'] < baseline['value'] if baseline['direction'] == 'down'
                            else comparison['value'] > baseline['value'])
                if comparison['task_mix'] != '可比' or not improved:
                    errors.append('已验证必须 task_mix=可比且按 direction 有改进；应保持「验证中」或改「已放弃」')
            if h.get('status') in ('试修', '验证中'):
                if not nonempty(h.get('probe')):
                    errors.append('试修/验证中必须有非空 probe')
                if not isinstance(h.get('expect_down'), list) or not h['expect_down'] or not all(nonempty(x) for x in h['expect_down']):
                    errors.append('试修/验证中必须有非空 expect_down')
                if type(h.get('window_days')) is not int or h['window_days'] <= 0:
                    errors.append('window_days 必须为正整数')
        for r in groups['records']:
            if not isinstance(r.get('id'), str) or r['id'] not in ids:
                errors.append('记录 id 不在本批')
            if not isinstance(r.get('status'), str) or r['status'] not in STATUSES:
                errors.append('记录 status 不合法')
            if (r.get('status') == 'explained' or r.get('hypothesis') not in (None, '')) and (
                    not isinstance(r.get('hypothesis'), str) or r['hypothesis'] not in known):
                errors.append('记录必须引用存在的 hypothesis')
    if context is None:
        context = f.build_scan_context()
    for i, h in enumerate(groups['hypotheses']):
        scanned = {key: value for key, value in h.items() if key != 'explains'}
        errors.extend('密钥特征：' + hit for hit in secret_paths(scanned, f'hypotheses[{i}]', context))
    for i, r in enumerate(groups['records']):
        for key in ('note', 'hypothesis'):
            errors.extend('密钥特征：' + hit for hit in secret_paths(r.get(key, ''), f'records[{i}].{key}', context))
    return errors
def apply(data):
    current = latest()
    if current is None:
        return 64
    path, lock = current
    try:
        batch = read(path.parent / 'batch.json')
    except ValueError:
        batch = None
    if not isinstance(batch, dict) or any(
            not isinstance(batch.get(key), list) for key in ('pending', 'pool', 'records')):
        print('本批 batch.json 缺失或损坏，请先 aiw triage abort 再重新 start', file=sys.stderr)
        return 2
    with (folder() / 'records.lock').open('a') as guard:
        acquire_lock(guard)
        require_lock(lock)
        hypotheses = read(folder() / 'hypotheses.json', {})
        errors = validate(data, batch, hypotheses)
        if errors:
            print('\n'.join(errors), file=sys.stderr)
            return 2
        stamp = now().isoformat()
        for h in data.get('hypotheses', []):
            before = hypotheses.get(h['id'])
            after = dict(h, created=before['created'] if before else stamp, updated=stamp)
            append(folder() / 'hypotheses-log.jsonl', [dict(ts=stamp, triage_id=lock['triage_id'],
                                                          id=h['id'], before=before, after=after)])
            hypotheses[h['id']] = after
        f._atomic_json(folder() / 'hypotheses.json', hypotheses)
        statuses = [dict(id=r['id'], status=r['status'], hypothesis=r.get('hypothesis'), note=r.get('note', ''),
                         triage_id=lock['triage_id'], ts=stamp) for r in data.get('records', [])]
        append(folder() / 'status.jsonl', statuses)
        append(folder() / 'triages.jsonl', [dict(ts=stamp, triage_id=lock['triage_id'], host=host(),
                                               records=len(statuses), hypotheses=len(data.get('hypotheses', [])))])
        cache = f.recompute_count_cache()
        (folder() / 'triage.lock').unlink()
        path.unlink()
    print(f"已处理 {len(statuses)} 条；假设 {len(data.get('hypotheses', []))}；open={cache['open']}")
    return 0
def abort():
    current = latest()
    if current is None:
        return 64
    path, lock = current
    with (folder() / 'records.lock').open('a') as guard:
        acquire_lock(guard)
        require_lock(lock)
        (folder() / 'triage.lock').unlink()
        path.unlink()
    print('已释放 triage 锁')
    return 0
def main(argv=None):
    if os.environ.get('AIW_NESTED') == '1':
        print('执行方不得调用 aiw triage 命令', file=sys.stderr)
        return 64
    parser = f._Parser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('start')
    p.add_argument('--days', type=int, default=60)
    sub.add_parser('apply').add_argument('decisions', type=Path)
    sub.add_parser('abort')
    args = parser.parse_args(argv)
    try:
        if args.command == 'start':
            if args.days <= 0:
                parser.error('--days 必须为正整数')
            return start(args.days)
        if args.command == 'abort':
            return abort()
        try:
            data = json.loads(args.decisions.read_text(encoding='utf-8'))
        except (OSError, ValueError) as error:
            print(f'decisions: {error}', file=sys.stderr)
            return 2
        return apply(data)
    except LockConflict as error:
        print(str(error), file=sys.stderr)
        return 75
    except Exception as error:
        print(f'triage: {error}', file=sys.stderr)
        return 1
if __name__ == '__main__':
    sys.exit(main())
