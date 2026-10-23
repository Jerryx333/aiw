"""Local findings regression cases."""
from support import (inprocess_sh, git_history, git_template, FINDINGS_BLOCK, Path, REPO, check,
    complete_config_json, fake_home, findings_append, findings_fixture, findings_worker_fixture,
    json, os, sh, shutil, subprocess, sys, tempfile, wait_until, write_legacy_local)

def findings_parse_strip():
    with tempfile.TemporaryDirectory() as td:
        f = findings_fixture(Path(td))
        block = ('<<<AIW-FINDINGS\nguess: 无\ndeviation: 改了步骤\nabandoned: 放弃\nfixed: 修复\n'
                 '- observation: 无\n- observation: None。\n'
                 '- observation: 现象 | expected: 预期 | evidence: f.py:12 | location: f.py:main | other: 忽略\n'
                 '- observation:  | expected: 丢弃\nAIW-FINDINGS>>>')
        body, result, error = f.parse('正文\n' + block + '\n \t')
        check(body == '正文' and error is None, '正常块未剥离')
        check(result == {'guess': '无', 'deviation': '改了步骤', 'abandoned': '放弃', 'fixed': '修复',
                         'observations': [{'observation': '现象', 'expected': '预期', 'evidence': 'f.py:12', 'location': 'f.py:main'}]}, str(result))
        answers = {key: '无' for key in ('guess', 'deviation', 'abandoned', 'fixed')}
        for value in ('', ' \t ', '无', '没有', 'none', 'n/a', 'None。', ' NONE. ', 'N/A。', '没有。', '无.'):
            empty_block = ('<<<AIW-FINDINGS\n' + ''.join(f'{key}: {val}\n' for key, val in answers.items()) +
                           f'- observation: {value} | expected: 不收录\nAIW-FINDINGS>>>')
            body, result, error = f.parse('正文\n' + empty_block)
            check(body == '正文' and error is None and result == {**answers, 'observations': []},
                  f'空观察未过滤或回答被修改: {value!r}: {result}')
        check(f.parse('<<<AIW-FINDINGS\n- observation: 无\nAIW-FINDINGS>>>') ==
              ('', {**dict.fromkeys(answers, ''), 'observations': []}, None), '只有无观察的块未被识别')
        for bad in (block + '\n正文\n' * 4, '<<<AIW-FINDINGS\nguess: 无',
                    'inline ' + block, block.replace('\nAIW-FINDINGS>>>', ' AIW-FINDINGS>>>')):
            stripped, parsed, error = f.parse(bad)
            check(stripped == bad and parsed is None and error, '坏块被吞掉')
        tolerant = ('<<<AIW-FINDINGS\n忽略前言\nguess：第一行\n第二行\n'
                    '- observation：现象 | expected：预期 | 补充 | evidence：f.py:12\n'
                    '证据续行\nAIW-FINDINGS>>>')
        _, result, error = f.parse(tolerant)
        check(error is None and result['guess'] == '第一行 第二行', str(result))
        check(result['observations'][0]['expected'] == '预期 补充' and
              result['observations'][0]['evidence'] == 'f.py:12 证据续行', str(result))
        bad = '<<<AIW-FINDINGS\n无归属\n- observation 缺冒号\nAIW-FINDINGS>>>'
        check(f.parse(bad)[0] == bad and f.parse(bad)[1] is None and f.parse(bad)[2], '无协议块被剥离')
        check(f.parse(block + '\n中间\n' + block)[0] == block + '\n中间', '未只剥离最后一个')
        many = '<<<AIW-FINDINGS\n' + '- observation: 多条\n' * 5 + 'AIW-FINDINGS>>>'
        check(len(f.parse(many)[1]['observations']) == 2, '未限制 max_items')
        check(f.parse(many)[1]['guess'] == '', '缺省字段不为空')
        check(f.parse('原文 \n') == ('原文 \n', None, None), '无块被修改')
        check('<<<AIW-FINDINGS' in f.SUFFIX and 'AIW-FINDINGS>>>' in f.SUFFIX, 'suffix markers')
        (f.HERE / 'seats.json').write_text(complete_config_json('{}'))
        check(all(f.config()[key] is False for key in ('collect', 'hint')), '缺省开关未关闭')


def findings_parse_trailing():
    with tempfile.TemporaryDirectory() as td:
        f = findings_fixture(Path(td))
        _, expected, _ = f.parse(FINDINGS_BLOCK)
        for trailing in ('派单记录', '一\n\n二\n \t\n三', '字' * 300, 'a' * 98 + '\n' + 'b' * 99 + '\n' + 'c' * 101):
            text = '正文\n' + FINDINGS_BLOCK + '\n' + trailing + '\n'
            answer, parsed, notice = f.parse(text)
            check(answer == '正文\n' + trailing and parsed == expected and notice,
                  f'短尾文丢失或记录被拒: {answer!r}, {notice!r}')
        answer, parsed, notice = f.parse(FINDINGS_BLOCK + '\n派单记录')
        check(answer == '派单记录' and parsed == expected and notice, '只有尾文时丢失正文')
        for trailing in ('正文', ' 正文\n尾文', '\t正文\r\n尾文', '字' * 301):
            text = '正文\n' + FINDINGS_BLOCK + trailing
            check(f.parse(text) == (text, None, '记录块标记格式错误'),
                  '结束标记同一行的尾文未拒绝或原输出被修改')
        for trailing in ('一\n二\n三\n四', '字' * 301):
            text = '正文\n' + FINDINGS_BLOCK + '\n' + trailing
            check(f.parse(text) == (text, None, '记录块后还有正文'), '超限尾文未拒绝')
        for trailing in ('<<<AIW-FINDINGS', '<<<AIW-FINDINGS\nguess: 无\nAIW-FINDINGS>>>\n' + '字' * 301):
            text = FINDINGS_BLOCK + '\n' + trailing
            answer, parsed, error = f.parse(text)
            check(answer == text and parsed is None and error, '尾文中的协议标记被忽略')


def findings_secret_segments():
    import re
    with tempfile.TemporaryDirectory() as td:
        f = findings_fixture(Path(td))

        original_load_seats = f.load_seats
        try:
            f.load_seats = lambda *args, **kwargs: {'seats': dict.fromkeys(
                ('codex', 'codex-sol', 'workbuddy', 'workbuddy-cn'), {})}
            context = f.build_scan_context()
            context = f.ScanContext(context.hosts | {"Example-MacBook-Pro"}, context.seats, f.compile_id_pattern(context.hosts | {"Example-MacBook-Pro"}, context.seats))
            random = 'aB3dE5gH7jK9mN0pQ2sT4vW6yZ8cF1iL5oR7uX9n'
            check(len(random) == 40, '随机样本长度错误')
            segmented = 'Xk9fQ2mZ_p7Lr4tW-a8Nc3vB6yH1-eJ5gU0sD2qR'
            check(len(segmented) == 40 and all(len(part) < 20 for part in re.split(r'[-_]', segmented)),
                  '分段随机样本长度错误')
            letters = 'QwErTyUiOpAsDfGhJkLzXcVbNmQaZwSxEdCrFvTg'
            # 夹在凭据中间的标识符被剥离后，凭据仍按整串判定。
            check(f.has_secret(random[:20] + '/20261011-174531-81790/' + random[20:], context), '标识符切断凭据')
            for value in (random, segmented, letters,
                          'state/runs/' + random,
                          'aB3d/E5gH7jK9/mN0pQ2sT4/vW6yZ8cF1iL5oR7uX9n+=',
                          'Q7m2Z9v4R8x1K6p3-N5t0W2y8C4s7H9d1-20261011144503-53420-1',
                          '20261010-162916-abcdefghijklmnop-qrstuvwx-yz012345-73190-6b28e1ea',
                          'Z8cF1iL5oR7uX9nB-20261011144503-53420-1',
                          'aZ7q-B2mX-c9Wp-D4rN-e8Ks-F3vH-g6Jt-L5uY',
                          letters[:20] + '-' + letters[20:], 'AIza' + 'A' * 34 + '-',
                          'AIza' + 'SyA1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q',
                          'task-run-prefix-' + random[:20], 'task_run_prefix_' + random[:20],
                          'task-run-prefix-' + random[:19], 'task_run_prefix_' + random[:19]):
                check(f.has_secret(value, context), '随机段未命中: ' + value)
            for value in ('state/runs/20261023-013216-review-53315-07ca533b',
                          'state/runs/20261023-090647-codex-7351-',
                          '20261010-162916-codex-73190-6b28e1ea',
                          '20261010-162916-codex-sol-73190-6b28e1ea',
                          'runs/20261010-171024-workbuddy-22968-6351683f',
                          '20261011-174531-workbuddy-cn-81790-ee6ddbb2',
                          '~/.claude/aiw/state/runs/20261011-174531-workbuddy-cn-81790-ee6ddbb2',
                          'Example-MacBook-Pro-20261011144503-53420-1',
                          'state/panels/20261010-231558-50917/workbuddy',
                          'short-words_joined_together_into_a_long_string'):
                check(not f.has_secret(value, context), '误报: ' + value)
            # 紧邻标识符的高熵串不能随标识符一起被剥离。
            check(f.has_secret(letters + '-Example-MacBook-Pro-20261011144503-53420-1', context), '主机名吞入凭据')
            check(f.has_secret('20261010-162916-' + random.lower() + '-73190-6b28e1ea', context), '座位名吞入凭据')
            check(not f.has_secret('20261011-174531-workbuddy-cn-81790-ee6ddbb2', context), '多段座位名误报')
            check(f.has_secret(random[:20] + 'Example-MacBook-Pro-20261011144503-53420-1' + random[20:], context),
                  '嵌在随机串中的记录 id 不应剥离')

        finally:
            f.load_seats = original_load_seats


def findings_sanitize_quarantine():
    import contextlib
    import io
    with tempfile.TemporaryDirectory() as td:
        f = findings_fixture(Path(td))
        check(f.sanitize('```python\n  中文   hello\n```\n world', 7) == '中文 hell', '清洗或截断错误')
        secrets = [prefix + 'a' * 20 for prefix in ('ghp_', 'gho_', 'ghu_', 'ghs_', 'ghr_', 'github_pat_', 'sk-')] + ['AKIA' + 'A' * 16,
                   '-----BEGIN PRIVATE KEY-----', 'xoxb-' + 'a' * 10, 'xoxa-' + 'a' * 10, 'xoxp-' + 'a' * 10,
                   'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.signature',
                   'aB3dE5gH7jK9mN0pQ2sT4vW6yZ8cF1iL']
        context = f.build_scan_context()
        for value in secrets:
            check(f.has_secret(value, context), '未检测到密钥特征: ' + value[:8])
        for value in ('task-runner', 'risk-free', 'disk-usage', 'AKIAxyz 普通词',
                      '0123456789abcdef0123456789abcdef01234567ab',
                      'src/findings.py', '/Users/developer/projects/harness/tests/test_findings.py',
                      'src/components/Observations/FindingsBuffer.py',
                      '普通中文句子，不是密钥', 'configuration documentation implementation'):
            check(not f.has_secret(value, context), '误报: ' + value)
        with contextlib.redirect_stderr(io.StringIO()) as warning:
            result = f.record([{'type': 'answers', 'evidence': secrets[0]}])
        folder = f.STATE / 'findings'
        check(result == {'written': 0, 'quarantined': 1, 'capped': 0}, str(result))
        check(not (folder / 'records.jsonl').exists(), '密钥写入普通日志')
        check(json.loads((folder / 'quarantine.jsonl').read_text())['evidence'] == secrets[0], '隔离丢数据')
        check('警告' in warning.getvalue(), '隔离未警告')
        import uuid
        repo = Path(td) / 'real-repo'; repo.mkdir()
        check(sh(['git', 'init', '-q', str(repo)]).returncode == 0, 'git init')
        row = f.common_fields(str(repo), session=str(uuid.uuid4()), seat='codex', actual='codex',
                              via='direct', run_dir=str(Path(td) / 'state/runs/20261010-124501-codex-12345'))
        row.update(guess='无', deviation='无', abandoned='无', fixed='正常记录')
        check(f.record([row])['written'] == 1, '真实元数据被隔离')
        check(json.loads((folder / 'records.jsonl').read_text())['id'] == row['id'], '正常记录丢失')
        row['guess'] = secrets[0]
        check(f.record([row])['quarantined'] == 1, '自由文本令牌未隔离')
        f.STATE = Path(td) / 'file'
        f.STATE.write_text('block')
        with contextlib.redirect_stderr(io.StringIO()) as warning:
            check(f.record([{}]) == {'written': 0, 'quarantined': 0, 'capped': 0}, '写失败计数不对')
        check('警告' in warning.getvalue(), '写失败未警告')


def findings_daily_cap():
    import concurrent.futures
    with tempfile.TemporaryDirectory() as td:
        f = findings_fixture(Path(td))
        # 并发调用与跨次调用都不能重置限额；answers 始终不占观察名额。
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda i: f.record([{'id': str(i), 'type': 'observation', 'observation': str(i)}]), range(8)))
        check(sum(r['written'] for r in results) == 2 and sum(r['capped'] for r in results) == 6, str(results))
        check(f.record([{'type': 'answers'}, {'type': 'changes'}]) == {'written': 2, 'quarantined': 0, 'capped': 0}, '非观察受限')
        folder = f.STATE / 'findings'
        check(next(folder.glob('daily-*.count')).read_text() == '8', '超限未累计')
        rows = [json.loads(line) for line in (folder / 'records.jsonl').read_text().splitlines()]
        check(len(rows) == 4 and sum(row['type'] == 'observation' for row in rows) == 2, '落盘条数不符')


def findings_worker_collect():
    import hashlib
    with tempfile.TemporaryDirectory() as td:
        root = Path(td); run, work, prompt, output, harness = findings_worker_fixture(root)
        r, row, records = run()
        check(r.returncode == 0 and r.stdout == '正文\n', r.stderr + repr(r.stdout))
        check([x['type'] for x in records] == ['answers', 'observation', 'observation'], str(records))
        check(len({x['id'] for x in records}) == 3, '重复 id')
        for x in records:
            check(x['seat'] == x['actual'] == 'codex' and x['via'] == 'direct' and
                  x['prompt_id'] == hashlib.sha1(prompt.read_bytes()).hexdigest()[:10] == row['prompt'] and
                  x['run_dir'] == row['run_dir'] and not x['partial_context'], str(x))
        check(row['findings_n'] == 2 and '观察记录：2 条' in r.stderr and '范围内修改：修复了测试' in r.stderr, r.stderr)
        check((Path(row['run_dir']) / 'output.md').read_text() == output.read_text(), '原文丢失')
        import findings
        task = (Path(row['run_dir']) / 'task.txt').read_text()
        check(task.startswith('今天是 ') and task.split('\n\n', 1)[1].startswith(prompt.read_text() + '\n\n')
              and '转述以原文为准' in task and task.endswith('\n\n' + findings.SUFFIX)
              and task.index('转述以原文为准') < task.index(findings.SUFFIX), 'prompt 前言或后缀错误')
        # 写入失败只警告，仍交付正文。
        shutil.rmtree(root / 'state/findings'); (root / 'state/findings').write_text('blocked')
        r, _, records = run()
        check(r.returncode == 0 and r.stdout == output.read_text() and '收集失败' in r.stderr, r.stderr)
        (root / 'state/findings').unlink()

def findings_worker_skip_rules():
    with tempfile.TemporaryDirectory() as td:
        run, work, _, _, _ = findings_worker_fixture(Path(td))
        for seat, flags, overrides in (
            ('codex', ('--ro',), {'AIW_PANEL_DIR': 'panel'}),
            ('codex', ('--ro',), {'AIW_MAP_DIR': 'map'}),
            ('codex', ('--ro', '--no-findings'), {}),
            ('workbuddy', ('--ro',), {})):
            r, row, records = run(seat, flags, **overrides)
            check(r.returncode == 0 and not records and 'findings_n' not in row, r.stderr)
            check('【aiw 观察记录】' not in (Path(row['run_dir']) / 'task.txt').read_text(), '不应追加')
        (work / 'code.py').write_text('print(1)')
        r, row, records = run('workbuddy', ('--ro', '--attach', 'code.py'))
        check(r.returncode == 0 and len(records) == 3 and all(x['partial_context'] for x in records), r.stderr)
        check('【aiw 观察记录】' in (Path(row['run_dir']) / 'task.txt').read_text(), '附件单未追加')
        # direct CLI 的收集决定沿顶替链保留，实际 agent_use 仍追加。
        # fixture 默认 no-fallback；显式开启顶替。
        r, row, records = run(flags=('--ro',), FAKE_CODEX='quota')
        check(r.returncode == 0 and row['actual'] != 'codex' and records[-1]['partial_context'], r.stderr)
        check('【aiw 观察记录】' in (Path(row['run_dir']) / 'task.txt').read_text(), '顶替丢失后缀')


def findings_worker_empty_body_rc2():
    with tempfile.TemporaryDirectory() as td:
        run, _, _, output, _ = findings_worker_fixture(Path(td))
        output.write_text(FINDINGS_BLOCK)
        r, row, records = run()
        check(r.returncode == 2 and row['kind'] == 'empty' and row['rc'] == 2 and
              row['findings_n'] == 2 and not records and '只有记录块，没有正文' in r.stderr, r.stderr)


def findings_worker_malformed():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td); run, _, _, output, _ = findings_worker_fixture(root)
        for text in ('正文\n' + FINDINGS_BLOCK + '\n后文\n' * 4, '正文\n<<<AIW-FINDINGS\nguess: 无'):
            output.write_text(text)
            r, row, records = run()
            check(r.returncode == 0 and r.stdout == (text if text.endswith('\n') else text + '\n') and not records and '格式不对' in r.stderr and
                  'findings_n' not in row, r.stderr)
            breaker = root / 'state/breaker.json'
            check(not breaker.exists() or 'codex' not in json.loads(breaker.read_text()), '坏块触发熔断')


def findings_worker_trailing():
    with tempfile.TemporaryDirectory() as td:
        run, _, _, output, _ = findings_worker_fixture(Path(td))
        output.write_text('正文\n' + FINDINGS_BLOCK + '\n派单记录')
        r, row, records = run()
        check(r.returncode == 0 and r.stdout == '正文\n派单记录\n' and row['kind'] == 'ok' and
              row['findings_n'] == 2 and len(records) == 3 and '格式不对' not in r.stderr,
              r.stderr + repr(r.stdout))
        check((Path(row['run_dir']) / 'output.md').read_text() == output.read_text(), '原文未保留')


def findings_worker_disabled():
    with tempfile.TemporaryDirectory() as td:
        run, _, prompt, output, harness = findings_worker_fixture(Path(td), False)
        keysets = []
        for missing in (False, True):
            if missing:
                config = json.loads((harness / 'seats.json').read_text()); config.pop('findings')
                (harness / 'seats.json').write_text(json.dumps(config))
            r, row, records = run()
            keysets.append(set(row))
            check(r.returncode == 0 and r.stdout == output.read_text() and not records and
                  'findings_n' not in row and '观察记录' not in r.stderr, r.stderr)
            task = (Path(row['run_dir']) / 'task.txt').read_text().split('\n\n', 1)[1]
            check(task.startswith(prompt.read_text() + '\n\n') and '转述以原文为准' in task
                  and '<<<AIW-FINDINGS' not in task and '沙箱说明' not in task, '关闭 findings 后任务内容被修改')
        check(keysets[0] == keysets[1], '关闭时账本键变化')


def findings_changes_record():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td); run, work, _, _, _ = findings_worker_fixture(root)
        check(sh(['git', 'init', '-q', str(root)]).returncode == 0, 'git init')
        (work / 'tracked').write_text('before')
        check(sh(['git', 'add', 'work/tracked'], cwd=root).returncode == 0, 'git add')
        check(sh(['git', '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'init'], cwd=root).returncode == 0, 'git commit')
        r, row, records = run(flags=('--no-fallback',), FAKE_CODEX='write')
        check(r.returncode == 0 and len(records) == 1 and records[0]['type'] == 'changes' and
              records[0]['files'] == ['work/created', 'work/tracked'], r.stderr + str(records))
    with tempfile.TemporaryDirectory() as td:
        run, _, _, _, _ = findings_worker_fixture(Path(td))
        r, _, records = run(flags=('--no-fallback',), FAKE_CODEX='write')
        check(r.returncode == 0 and not records and '无法统计' in r.stderr, r.stderr)


def findings_note():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td); home, env = fake_home(root)
        harness = home / '.claude/aiw'
        work = root / 'work'; work.mkdir()
        check(sh(['git', 'init', '-q', str(work)]).returncode == 0, 'git init')
        check(sh(['git', '-C', str(work), 'remote', 'add', 'origin', 'git@github.com:Owner/Name.git']).returncode == 0, 'remote')
        def run(*args, **overrides):
            return inprocess_sh([sys.executable, str(harness / 'findings.py'), 'note', str(work), *args], env=dict(env, **overrides))
        r = run('  现象   描述 ', '--expected', '```\n应当成功\n```', '--location', 'test.py:1')
        folder = root / 'state/findings'
        row = json.loads((folder / 'records.jsonl').read_text())
        check(r.returncode == 0 and r.stdout.strip() == row['id'], r.stderr)
        check(row['source'] == 'main' and row['type'] == 'observation' and
              row['seat'] == row['actual'] == 'main' and row['via'] == 'note' and
              row['repo'] == 'github.com/owner/name' and row['repo_rel'] == '.' and
              row['observation'] == '现象 描述' and row['expected'] == '应当成功', str(row))
        check(all(row[key] is None for key in ('ro', 'run_dir', 'prompt_id')), str(row))
        check(run('   ').returncode == 64, '空现象')
        check(run('正常', AIW_NESTED='1').returncode == 64, 'nested')
        missing = inprocess_sh([sys.executable, str(harness / 'findings.py'), 'note', str(root / 'missing'), '现象'], env=env)
        check(missing.returncode == 64, missing.stderr)
        r = run('ghp_' + 'a' * 25)
        check(r.returncode == 0 and len((folder / 'records.jsonl').read_text().splitlines()) == 1 and
              json.loads((folder / 'quarantine.jsonl').read_text())['id'] == r.stdout.strip(), r.stderr)






def scanner_context_no_io():
    import contextlib
    from unittest.mock import patch
    with tempfile.TemporaryDirectory() as td:
        f = findings_fixture(Path(td))
        c = f.build_scan_context()
        with contextlib.ExitStack() as stack:
            spies = [stack.enter_context(patch.object(obj, name, side_effect=AssertionError('scan I/O')))
                     for obj, name in ((f, 'load_seats'), (f.socket, 'gethostname'),
                                       (Path, 'iterdir'), (Path, 'is_dir'), (Path, 'read_text'),
                                       (Path, 'open'), (os, 'scandir'))]
            spies.append(stack.enter_context(patch('builtins.open', side_effect=AssertionError('scan open'))))
            for s, expected in [('ghp_' + 'a' * 20, True), ('ordinary text', False),
                                ('20261011-174531-text-81790-ee6ddbb2', False),
                                ('aB3dE5gH7jK9mN0pQ2sT4vW6yZ8cF1iL5oR7uX9n', True)]:
                check(f.has_secret(s, c) == expected, 'scan result')
            check(all(s.call_count == 0 for s in spies), 'scan touched environment')
        try:
            f.has_secret('ordinary text')
        except TypeError:
            pass
        else:
            raise AssertionError('implicit context allowed')


def scanner_context_once():
    import importlib.util
    from unittest.mock import patch
    with tempfile.TemporaryDirectory() as td:
        f = findings_fixture(Path(td))
        spec = importlib.util.spec_from_file_location('triage_gate', REPO / 'triage.py')
        t = importlib.util.module_from_spec(spec); spec.loader.exec_module(t); t.f = f
        c = f.build_scan_context()
        with patch.object(f, 'build_scan_context', return_value=c) as build, \
                patch.object(f, 'has_secret', wraps=f.has_secret) as scan:
            f.record([{'guess': 'safe', 'evidence': 'safe'}, {'fixed': 'safe', 'location': 'safe'}], strict=True)
            check(build.call_count == 1 and len(scan.call_args_list) == 4, 'record snapshot count')
            check(all(call.args[1] is c for call in scan.call_args_list), 'record context identity')
            row = {'id': 'R'}
            batch = {'pending': [row], 'pool': [], 'records': [row]}
            for data, kwargs in [({'self_cause_considered': 'yes',
                    'hypotheses': [{'id': 'H', 'explains': ['R'], 'mechanism': {'nested': ['safe']}}],
                    'records': [{'id': 'R', 'status': 'explained', 'hypothesis': 'H', 'note': ['safe']}]},
                    {'batch': batch, 'existing': {'H': {}}})]:
                build.reset_mock(); scan.reset_mock()
                t.validate(data, **kwargs)
                check(build.call_count == 1, 'validate snapshot count')
                check(any(call.args[0] == 'nested' for call in scan.call_args_list), 'dictionary key not scanned')
                check(all(call.args[1] is c for call in scan.call_args_list), 'validate context identity')
                build.reset_mock()
                t.validate(data, context=c, **kwargs)
                check(build.call_count == 0, 'injected context rebuilt')


def scanner_context_bypass():
    from unittest.mock import patch
    import findings
    findings.build_scan_context()
    findings_secret_segments()
    scanner_context_no_io()
    with tempfile.TemporaryDirectory() as td:
        f = findings_fixture(Path(td))
        credential_name = 'sk-' + 'a' * 20
        with patch.object(
                f.socket, 'gethostname', return_value=credential_name):
            c = f.build_scan_context()
        check(f.has_secret(credential_name + '-20261012112233-1-1', c),
              'credential prefix stripped as trusted ID')


def scanner_id_rules():
    import datetime as dt
    import harness_ids as ids
    stamp = dt.datetime(2026, 10, 12, 11, 22, 33)
    check(ids.format_run_id(stamp, 'workbuddy-cn', 12345, 'abcdef01') ==
          '20261012-112233-workbuddy-cn-12345-abcdef01', 'run format')
    check(ids.format_record_id('host', stamp, 12345, 9999) == 'host-20261012112233-12345-9999', 'record format')
    check(ids.format_panel_id(stamp, 12345) == '20261012-112233-12345', 'panel/map format')
    pattern = ids.compile_id_pattern({'host', 'host-long', 'h.st'}, {'seat', 'seat-long', 's.eat'})
    check(pattern.pattern.index('host\\-long') < pattern.pattern.index('|host'), 'host longest first')
    check(pattern.pattern.index('seat\\-long') < pattern.pattern.index('|seat'), 'seat longest first')
    for value in ('host-long-20261012112233-1234567-9999', 'h.st-20261012112233-1-9999',
                  '20261012-112233-seat-long-1234567-abcdef01', '20261012-112233-s.eat-1-abcdef01',
                  '20261012-112233-1234567'):
        check(pattern.fullmatch(value) is not None, 'known ID rejected: ' + value)
        for edge in ('A', '0', '_', '-'):
            check(pattern.search(edge + value) is None and pattern.search(value + edge) is None, 'ID boundary')
    for value in ('unknown-20261012112233-1-1', 'hxst-20261012112233-1-1',
                  'host-20261012112233-12345678-1', 'host-20261012112233-1-10000',
                  '20261012-112233-seat-12345678-abcdef01', '20261012-112233-seat-1-ABCDEF01',
                  '20261012-112233-12345678'):
        check(pattern.search(value) is None, 'ID exemption expanded: ' + value)


def findings_record_invalid_cache():
    for content in (None, '{broken', '[]', 'null', '{"x":1}', '{"open":"a"}',
                    '{"open":true}', '{"open":1.5}'):
        with tempfile.TemporaryDirectory() as td:
            f = findings_fixture(Path(td))
            folder = f.STATE / 'findings'; folder.mkdir(parents=True)
            findings_append(folder, *[dict(id=rid, type='observation')
                                     for rid in ('pending', 'unexplained', 'done')])
            (folder / 'status.jsonl').write_text(
                '{"id":"unexplained","status":"unexplained"}\n'
                '{"id":"done","status":"explained"}\n')
            cache_path = folder / 'count-cache.json'
            if content is not None:
                cache_path.write_text(content)
            rows = [dict(id=rid, type='observation', observation='现象') for rid in ('one', 'two')]
            check(f.record(rows) == dict(written=2, quarantined=0, capped=0),
                  f'invalid cache interrupted batch: {content}')
            records = list(f._rows(folder / 'records.jsonl'))
            check([r['id'] for r in records] == ['pending', 'unexplained', 'done', 'one', 'two'],
                  f'batch records missing: {content}')
            statuses = {r['id']: r['status'] for r in f._rows(folder / 'status.jsonl')}
            actual = sum(r['type'] == 'observation' and statuses.get(r['id']) in (None, 'unexplained')
                         for r in records)
            check(json.loads(cache_path.read_text())['open'] == actual == 4,
                  f'cache did not recount existing records exactly once: {content}')


CASES = {name: value for name, value in globals().copy().items() if name.startswith(("findings_", "scanner_")) and callable(value) and value.__module__ == __name__}


def findings_local_cache_hint():
    import datetime as dt
    from unittest.mock import patch
    with tempfile.TemporaryDirectory() as td:
        f = findings_fixture(Path(td))
        cfg = dict(f.config(), daily_cap=10, hint=True, trigger_count=2, trigger_days=3)
        now = dt.datetime.now(dt.timezone.utc)
        with patch.object(f, 'config', return_value=cfg):
            check(f.record([dict(id='one', type='observation', ts=now.isoformat(), observation='现象')])['written'] == 1, 'write')
            folder = f.STATE / 'findings'
            cache = json.loads((folder / 'count-cache.json').read_text())
            check(cache == dict(open=1, last_triage=None, oldest_open=now.astimezone().isoformat()), 'increment cache')
            with patch.object(f, '_rows', side_effect=AssertionError('hook scanned records')):
                check(f.hint_text(now) is None, 'early hint')
                check('[缓冲区]' in f.hint_text(now + dt.timedelta(days=4)), 'age hint missing')
                check(f.hint_text(now + dt.timedelta(days=4, hours=3)) is None, 'throttle')
                check(f.hint_text(now + dt.timedelta(days=4, hours=4)), 'throttle never expires')
                (folder / 'hint.json').unlink()
                cache['oldest_open'] = None
                f._atomic_json(folder / 'count-cache.json', cache)
                check(f.hint_text(now) is None, 'invalid age scanned records')
            check(f.record([dict(id='two', type='observation', ts=now.isoformat(), observation='现象'),
                            dict(id='answer', type='answers'), dict(id='secret', type='observation', observation='sk-'+'a'*24)]) ==
                  dict(written=2, quarantined=1, capped=0), 'counts')
            check(json.loads((folder / 'count-cache.json').read_text())['open'] == 2, 'non-observation increment')
            (folder / 'status.jsonl').write_text('{"id":"one","status":"unexplained"}\n{"id":"two","status":"unverifiable"}\n')
            check(f.recompute_count_cache()['open'] == 1, 'status count semantics')
        check(len(f.SUFFIX.encode()) <= 450, 'suffix too large')


def findings_extract_local():
    import datetime as dt
    from unittest.mock import patch
    with tempfile.TemporaryDirectory() as td:
        f = findings_fixture(Path(td))
        now = dt.datetime.now().astimezone()
        folder = f.STATE / 'findings'; folder.mkdir(parents=True)
        records = [dict(id=str(i), type='changes', ts=(now-dt.timedelta(minutes=i+1)).isoformat(),
                        repo='example/owner/project', run_dir=str(i), files=['a.py']) for i in range(2)]
        findings_append(folder, *records)
        with (folder / 'records.jsonl').open('a') as stream: stream.write('{broken\n')
        runs = f.STATE / 'runs'; runs.mkdir()
        for i in range(5):
            run = runs / ((now-dt.timedelta(minutes=i)).strftime('%Y%m%d-%H%M%S')+'-'+str(i))
            run.mkdir(); (run / 'task.txt').write_text('每次必须检查这条足够长的重复手写规则\n不同任务'+str(i)+'\n'+f.SUFFIX)
        with patch.object(f, '_git', return_value=None):
            data = f.extract()
        check(len(data['overlap']) == 1 and len(data['repeated_instruction']) == 1, 'signals')
        source = next(s for s in data['coverage']['sources'] if s['name']=='findings/records.jsonl')
        check(source['rows'] == 2 and source['bad'] == 1, 'coverage counting')
        check(data['coverage']['level'] == '部分' and any('解析失败' in r for r in data['coverage']['reasons']), 'coverage')
        check(json.loads((folder / 'signals.json').read_text()) == data, 'signals persistence')


def findings_config_local():
    with tempfile.TemporaryDirectory() as td:
        f = findings_fixture(Path(td))
        from unittest.mock import patch
        with patch.object(f, '_settings', return_value={'findings': dict(sync='bad', hub=[], owners=1, sync_interval=None)}):
            check(set(f.config()) == {'collect','hint','max_items','daily_cap','field_max','trigger_count','trigger_days'}, 'removed config retained')
        with patch.object(f, '_settings', return_value={'findings': dict(daily_cap=-1, field_max=False)}):
            check(f.config()['daily_cap']==300 and f.config()['field_max']==300, 'invalid config')
        check(f._repo('git@example.com:Owner/Project.git') == 'example.com/owner/project', 'repo identity')


CASES.update({case.__name__: case for case in (findings_local_cache_hint, findings_extract_local, findings_config_local)})


def findings_worker_prompt_filters():
    import importlib.util
    from unittest.mock import patch
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        run, work, prompt, _, harness = findings_worker_fixture(root)
        result, row, _ = run()
        check(result.returncode == 0, result.stderr)
        task = (Path(row['run_dir']) / 'task.txt').read_text()
        # 日期取自真实派单，worker 改动日期文字时不能悄悄漏过。
        prefix = task.split('\n\n', 1)[0] + '\n\n'
        f = findings_fixture(root)
        spec = importlib.util.spec_from_file_location('worker_prompt_filters', harness / 'ai_worker.py')
        worker = importlib.util.module_from_spec(spec); spec.loader.exec_module(worker)
        body = prompt.read_text()
        check(f._handwritten_prompt(prefix + body) == body, '日期前缀未剥离')
        check(f._handwritten_prompt(body + worker.PANEL_FORMAT) == body, '会诊格式未剥离')
        check(f._handwritten_prompt(body + '\n\n' + f.SUFFIX) == body, '观察记录未剥离')
        for reminder in f._TASK_REMINDERS:
            check(f._handwritten_prompt(prefix + body + '\n\n' + reminder) == body,
                  '历史或当前提醒未剥离')
        for ro in (False, True):
            for net in (False, True):
                for linked in (False, True):
                    with patch.object(worker, 'worktree_writable_roots', return_value=['git-dir'] if linked else []):
                        reminders = worker.dispatch_reminders('codex', ro, str(work), net)
                    # 单独验证每段，避免第一段截断掩盖后续文字变动。
                    for segment in reminders.strip().splitlines():
                        check(f._handwritten_prompt(body + '\n\n' + segment) == body,
                              f'自动提醒未剥离: ro={ro}, net={net}, linked={linked}: {segment}')
                    for kind in ('cli', 'agent_use'):
                        attachments = worker.render_attachments([('data.txt', b'attachment noise')], kind)
                        for attachment in ('', attachments):
                            wrapped = prefix + body + attachment + reminders + '\n\n' + f.SUFFIX + worker.PANEL_FORMAT
                            check(f._handwritten_prompt(wrapped) == body, '组合包装未剥离')
        # 正文中的相似句子和中间段落应保留，不能按行前缀全局删除。
        handwritten = '今天是手写的日期说明\n\n' + f._TASK_REMINDERS[-1] + '\n后面仍然是正文'
        check(f._handwritten_prompt(handwritten) == handwritten, '误删正文')


def findings_worker_repeated_instruction():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        run, _, prompt, _, harness = findings_worker_fixture(root)
        repeated = '每次修改结束以后必须运行相关的离线回归测试'
        for i in range(6):
            prompt.write_text(f'任务 {i}\n{repeated}')
            flags = ('--ro', '--no-fallback') + (('--attach', 'data.txt') if i == 5 else ())
            if i == 5:
                (root / 'work/data.txt').write_text('附件中的重复说明不应混入手写指令统计')
            overrides = dict(AIW_PANEL_DIR=str(root / 'panel'), AIW_PANEL_FORMAT='1') if i == 4 else {}
            result, _, _ = run(flags=flags, **overrides)
            check(result.returncode == 0, result.stderr)
        check(len(list((root / 'state/runs').glob('*/task.txt'))) == 6, '未生成六个真实任务文件')
        f = findings_fixture(root)
        data = f.extract(harness=root / 'no-history')
        check(len(data['repeated_instruction']) == 1 and
              data['repeated_instruction'][0]['line'] == repeated and
              data['repeated_instruction'][0]['count'] == 6, str(data['repeated_instruction']))


CASES.update({case.__name__: case for case in (
    findings_worker_prompt_filters, findings_worker_repeated_instruction)})


def findings_extract():
    import datetime as dt
    with tempfile.TemporaryDirectory() as td:
        root = Path(td); f = findings_fixture(root)
        now = dt.datetime.now().astimezone()
        folder = f.STATE / 'findings'; folder.mkdir(parents=True)
        def row(id, hours, host, file):
            return {'id': id, 'ts': (now - dt.timedelta(hours=hours)).isoformat(),
                    'host': host, 'repo': 'github.com/owner/repo', 'type': 'changes',
                    'files': [file], 'run_dir': '/runs/' + id}
        local = [row('a', 1, 'host-a', 'same.py'), row('c', 1, 'host-a', 'far.py')]
        remote = [row('b', 11, 'host-b', 'same.py'), row('d', 31, 'host-b', 'far.py'), local[0]]
        findings_append(folder, *(local + remote))
        harness = root / 'history'; harness.mkdir()
        def git(*args, **kwargs):
            r = sh(['git', '-C', str(harness), *args], **kwargs)
            check(r.returncode == 0, r.stderr)
        old = (now - dt.timedelta(days=30)).isoformat()
        initial = '\n## frequent\ninitial\n## rare\ninitial\n'
        history = [('base', {'CLAUDE.md': initial}, old)]
        history.extend((f'frequent {i}', {'CLAUDE.md': f'\n## frequent\nversion {i}\n## rare\ninitial\n'}, now.isoformat()) for i in range(3))
        history.append(('rare', {'CLAUDE.md': '\n## frequent\nversion 2\n## rare\nchanged\n'}, now.isoformat()))
        git_template(harness, empty=True)
        git_history(harness, history)
        repeated = '每次修改结束以后必须运行相关的离线回归测试'
        noise = '这一行只在附件或观察记录说明里出现不应该计入'
        for i in range(6):
            run = f.STATE / 'runs' / ((now - dt.timedelta(minutes=1)).strftime('%Y%m%d-%H%M%S') + f'-seat-{i}')
            run.mkdir(parents=True)
            unique = min(i, 4)
            marker = '\n\n---\n以下是调用方附上的文件' if i % 2 else '\n\n---\n【aiw 观察记录】'
            (run / 'task.txt').write_text(f'任务 {unique}\n{repeated}\n{repeated}{marker}\n{noise}')
        # A separate old run must not increase the count.
        oldrun = f.STATE / 'runs' / ((now - dt.timedelta(days=30)).strftime('%Y%m%d-%H%M%S') + '-old')
        oldrun.mkdir(); (oldrun / 'task.txt').write_text('old\n' + repeated)
        data = f.extract(harness=harness)
        check(len(data['overlap']) == 1, str(data))
        check({r['host'] for r in data['overlap'][0]['records']} == {'host-a', 'host-b'} and
              len(data['overlap'][0]['records']) == 2, str(data['overlap']))
        check(len(data['rule_churn']) == 1 and data['rule_churn'][0]['section'] == 'frequent' and
              len(data['rule_churn'][0]['commits']) == 3, str(data['rule_churn']))
        check(len(data['repeated_instruction']) == 1 and data['repeated_instruction'][0]['line'] == repeated and
              data['repeated_instruction'][0]['count'] == 5 and len(data['repeated_instruction'][0]['examples']) == 3,
              str(data['repeated_instruction']))
        check(json.loads((folder / 'signals.json').read_text()) == data, 'signals snapshot')
        # No remote: identical repo_rel values in distinct workdirs must not collide.
        isolated_rows = []
        for id, wd in (('e', '/workspace/one'), ('f', '/workspace/two')):
            item = row(id, 2, 'host-a', 'local.py')
            item.update(repo=None, repo_rel='.', workdir=wd)
            isolated_rows.append(item)
        with (folder / 'records.jsonl').open('a') as stream:
            for item in isolated_rows:
                stream.write(json.dumps(item) + '\n')
        check(len(f._overlap(now - dt.timedelta(days=14), now)) == 1, '无 remote 仓库误合并')
        # Re-extraction replaces the whole snapshot and supports a non-git harness.
        data = f.extract(days=1, harness=f.HERE)
        check(data['rule_churn'] == [] and json.loads((folder / 'signals.json').read_text())['days'] == 1, 'replace')
        for days in ('0', '-1', 'bad'):
            try:
                f.main(['extract', '--days', days])
            except SystemExit as error:
                check(error.code == 64, days)
            else:
                raise AssertionError('invalid days accepted')



def findings_record_lock_timeout():
    from unittest.mock import patch
    with tempfile.TemporaryDirectory() as td:
        f = findings_fixture(Path(td))
        with patch.object(f, '_acquire_lock', return_value=False):
            check(f.record([dict(type='observation')])['written'] == 0, 'locked record written')
            try:
                f.record([dict(type='observation')], strict=True)
            except TimeoutError:
                pass
            else:
                raise AssertionError('strict lock failure ignored')


CASES.update({case.__name__: case for case in (findings_extract, findings_record_lock_timeout)})


def findings_local_reporting():
    import contextlib
    import datetime as dt
    import io
    import importlib.util
    from unittest.mock import patch
    with tempfile.TemporaryDirectory() as td:
        f = findings_fixture(Path(td))
        stamp = dt.datetime.now(dt.timezone.utc).isoformat()
        folder = f.STATE / 'findings'; folder.mkdir(parents=True)
        (folder / 'records.jsonl').write_text('{"interrupted":')
        check(f.record([dict(id='a', type='answers', ts=stamp, guess='无'),
                        dict(id='o', type='observation', ts=stamp, observation='现象', owned=True)])['written'] == 2, 'half-line recovery')
        rows = list(f._rows(folder / 'records.jsonl'))
        check(len(rows) == 2 and all('owned' not in r for r in rows), 'old field retained')
        (folder / 'status.jsonl').write_text(json.dumps(dict(id='o', status='unexplained', ts=stamp))+'\n')
        f._atomic_json(folder / 'hypotheses.json', {'H': dict(status='验证中')})
        spec = importlib.util.spec_from_file_location('weekly_test', REPO / 'weekly.py')
        w = importlib.util.module_from_spec(spec); spec.loader.exec_module(w); w.STATE = f.STATE
        with contextlib.redirect_stdout(io.StringIO()) as out:
            w.findings_section(dt.datetime.now(dt.timezone.utc)-dt.timedelta(days=1))
        text = out.getvalue()
        check('answers: 1' in text and 'observation: 1' in text and 'unexplained: 1' in text and '验证中: 1' in text, 'local weekly reporting')
        with contextlib.redirect_stdout(io.StringIO()) as out:
            f.status()
        check('answers: 1' in out.getvalue() and 'daily: 1' in out.getvalue(), 'status reporting')
        with patch.object(f, '_acquire_lock', return_value=False):
            check(f.record([dict(type='observation')])['written'] == 0, 'lock timeout ignored')


def findings_route_hint():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td); home, env = fake_home(root)
        harness = home / '.claude/aiw'
        settings = json.loads((harness / 'seats.json').read_text())
        settings['findings'] = dict(hint=True)
        (harness / 'seats.json').write_text(json.dumps(settings))
        folder = Path(env['AIW_STATE']) / 'findings'; folder.mkdir(parents=True)
        (folder / 'count-cache.json').write_text(json.dumps(dict(open=120, last_triage=None, oldest_open=None)))
        result = inprocess_sh([sys.executable, str(harness / 'route.py'), '--hook'], env=env,
                              input=json.dumps(dict(prompt='你好', cwd=str(root))))
        check(result.returncode == 0 and '[缓冲区]' in result.stdout, 'hook hint missing')
        result = inprocess_sh([sys.executable, str(harness / 'route.py'), '--hook'], env=env,
                              input=json.dumps(dict(prompt='你好', cwd=str(root))))
        check(result.returncode == 0 and '[缓冲区]' not in result.stdout, 'hook hint not throttled')


CASES.update({case.__name__: case for case in (findings_local_reporting, findings_route_hint)})
