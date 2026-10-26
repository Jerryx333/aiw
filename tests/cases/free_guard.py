"""free guard regression cases."""
from __future__ import annotations

from support import (
    Path,
    REPO,
    check,
    fake_agent_use,
    fake_bins,
    free_guard_fixture,
    json,
    latest_provider,
    legacy_config,
    os,
    permodel_run,
    permodel_state,
    inprocess_sh,
    sh,
    subprocess,
    sys,
    tempfile,
    wait_until,
    write_legacy_local,
)


def workbuddy_cn_argv():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); env = free_guard_fixture(t)
        prompt = t / 'prompt.md'; prompt.write_text('test')
        r = sh([str(REPO / 'bin/aiw'), 'workbuddy-cn', str(t), '30', str(prompt), '--ro'], env=env)
        check(r.returncode == 0, r.stderr)
        calls = [json.loads(line) for line in (t / 'calls').read_text().splitlines()]
        check(calls == [{'order': 'workbuddy-cn', 'model': ['workbuddy-cn=free-model-b'], 'options': ['workbuddy-cn.fallback_models='], 'no_tools': True}], str(calls))
        check(latest_provider(t / "state").read_text().strip() == 'workbuddy-cn', 'provider 错误')
    config = legacy_config()
    seat = config['seats']['workbuddy-cn']
    check(seat['kind'] == 'agent_use' and seat['free_guard'] and seat['free_only'], str(seat))
    check(config['fallback']['workbuddy-cn'] == ['workbuddy', 'qoder', 'opencode'], '国内版顶替链错误')
    check(all('workbuddy-cn' not in chain for chain in config['fallback'].values()), '其他座位不应顶替到国内版')
    check('workbuddy-cn' not in config['panel_default'] + config['panel_free'], '国内版不应加入会诊')



def workbuddy_cn_fallback_models():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); env = free_guard_fixture(t)
        local = t / 'local.json'
        write_legacy_local(local, {'seats': {'workbuddy-cn': {
            'models': ['free-model-b', 'free-model-c']}}})
        env['AIW_CONFIG'] = str(local)

        r = permodel_run(t, env, 'workbuddy-cn')
        call = json.loads((t / 'calls').read_text().splitlines()[-1])
        check(r.returncode == 0 and call == {
            'order': 'workbuddy-cn', 'model': ['workbuddy-cn=free-model-b'],
            'options': ['workbuddy-cn.fallback_models=free-model-b,free-model-c'],
            'no_tools': True}, repr(r) + str(call))

        r = sh([sys.executable, '-c',
                "import ai_worker as w, sys; "
                "sys.exit(w.run_agent_use('workbuddy-cn', 'test', sys.argv[1], 10, fixed_model='free-model-c')['rc'])",
                str(t)], env=env, cwd=REPO)
        call = json.loads((t / 'calls').read_text().splitlines()[-1])
        check(r.returncode == 0 and call['model'] == ['workbuddy-cn=free-model-c']
              and call['options'] == ['workbuddy-cn.fallback_models='], repr(r) + str(call))

        r = permodel_run(t, env, 'workbuddy-cn', FAKE_AU_CREDITS='0.5',
                         FAKE_ATTEMPT_FIELDS=json.dumps({'selected_model': 'free-model-c'}))
        check(r.returncode == 0 and set(permodel_state(t)) == {'workbuddy-cn:free-model-c'}, repr(r))
        r = permodel_run(t, env, 'workbuddy-cn')
        call = json.loads((t / 'calls').read_text().splitlines()[-1])
        check(r.returncode == 0 and call['model'] == ['workbuddy-cn=free-model-b']
              and call['options'] == ['workbuddy-cn.fallback_models='], repr(r) + str(call))



def workbuddy_cn_offhours_substitute():
    for reason, no_fallback, rc, breaker_kind in (
        ('outside_free_window', False, 0, None),
        ('outside_free_window', True, 1, None),
        ('free_window_invalid', True, 1, 'unavailable'),
        ('model_not_allowed', False, 2, None),
    ):
        with tempfile.TemporaryDirectory() as td:
            t = Path(td); env = free_guard_fixture(t)
            env['FAKE_AU_WORKBUDDY-CN'] = reason
            prompt = t / 'prompt.md'; prompt.write_text('test')
            cmd = [str(REPO / 'bin/aiw'), 'workbuddy-cn', str(t), '30', str(prompt), '--ro']
            r = sh(cmd + (['--no-fallback'] if no_fallback else []), env=env)
            check(r.returncode == rc, repr(r))
            br = t / 'state/breaker.json'
            breakers = json.loads(br.read_text()) if br.exists() else {}
            check(breakers.get('workbuddy-cn', {}).get('reason') == breaker_kind, str(breakers))
            calls = [json.loads(line)['order'] for line in (t / 'calls').read_text().splitlines()]
            check(calls == (['workbuddy-cn', 'workbuddy'] if rc == 0 else ['workbuddy-cn']), str(calls))
            hint = 'workbuddy-cn 不在免费时段，已顶替'
            if rc == 0:
                check(latest_provider(t / "state").read_text().strip() == 'workbuddy' and hint in r.stderr, r.stderr)
            else:
                check(not latest_provider(t / "state").exists() and hint not in r.stderr, r.stderr)
            check(not (t / 'state/disabled.json').exists() and not (t / 'discord').exists(), '闸门拒绝不应停用或通知')



def workbuddy_cn_promotion_expired():
    for no_fallback in (False, True):
        with tempfile.TemporaryDirectory() as td:
            t = Path(td); env = free_guard_fixture(t)
            env['FAKE_AU_WORKBUDDY-CN'] = 'promotion_expired'
            prompt = t / 'prompt.md'; prompt.write_text('test')
            cmd = [str(REPO / 'bin/aiw'), 'workbuddy-cn', str(t), '30', str(prompt), '--ro']
            r = sh(cmd + (['--no-fallback'] if no_fallback else []), env=env)
            check(r.returncode == (1 if no_fallback else 0), repr(r))
            br = t / 'state/breaker.json'
            check(not br.exists() or 'workbuddy-cn' not in json.loads(br.read_text()), '活动到期不应熔断')
            calls = [json.loads(line)['order'] for line in (t / 'calls').read_text().splitlines()]
            check(calls == (['workbuddy-cn'] if no_fallback else ['workbuddy-cn', 'workbuddy']), str(calls))
            hint = 'workbuddy-cn 免费活动已到期（free_until），已顶替；需复核适配器配置或停用该座位'
            if no_fallback:
                check(not latest_provider(t / "state").exists() and hint not in r.stderr, r.stderr)
            else:
                check(latest_provider(t / "state").read_text().strip() == 'workbuddy' and hint in r.stderr, r.stderr)
            rows = [json.loads(line) for line in (t / 'state/seat-ledger.jsonl').read_text().splitlines()]
            check(rows[0]['kind'] == 'transient', str(rows))
            check(not (t / 'state/disabled.json').exists() and not (t / 'discord').exists(), '活动到期不应停用或通知')



def workbuddy_cn_freecheck_promotion_expired():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); env = free_guard_fixture(t)
        env['FAKE_AU_WORKBUDDY-CN'] = 'promotion_expired'
        models = t / 'models.json'
        models.write_text(json.dumps({'data': [{'id': str(i)} for i in range(10)]}))
        env['AIW_ZEN_MODELS_URL'] = models.as_uri()
        r = sh([str(REPO / 'bin/aiw'), 'freecheck'], env=env)
        check(r.returncode == 0, repr(r))
        check('workbuddy-cn: 【需关注】免费活动已到期，需复核' in r.stdout, r.stdout)
        check('workbuddy-cn: 不在免费时段，跳过' not in r.stdout, r.stdout)
        rows = [json.loads(line) for line in (t / 'state/seat-ledger.jsonl').read_text().splitlines()]
        cn_rows = [row for row in rows if row['actual'] == 'workbuddy-cn']
        check(len(cn_rows) == 1 and cn_rows[0]['kind'] == 'transient' and cn_rows[0]['freecheck'], str(rows))
        br = t / 'state/breaker.json'
        check(not br.exists() or 'workbuddy-cn' not in json.loads(br.read_text()), '活动到期不应熔断')
        check(not (t / 'state/disabled.json').exists() and not (t / 'discord').exists(), '活动到期不应停用或通知')



def workbuddy_cn_freecheck_offhours():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); env = free_guard_fixture(t)
        env['FAKE_AU_WORKBUDDY-CN'] = 'outside_free_window'
        models = t / 'models.json'
        models.write_text(json.dumps({'data': [{'id': str(i)} for i in range(10)]}))
        env['AIW_ZEN_MODELS_URL'] = models.as_uri()
        r = sh([str(REPO / 'bin/aiw'), 'freecheck'], env=env)
        check(r.returncode == 0 and 'workbuddy-cn: 不在免费时段，跳过' in r.stdout, repr(r))
        rows = [json.loads(line) for line in (t / 'state/seat-ledger.jsonl').read_text().splitlines()]
        check(all(row['actual'] != 'workbuddy-cn' and row['kind'] == 'ok' for row in rows), str(rows))
        check(not (t / 'state/disabled.json').exists() and not (t / 'discord').exists(), '跳过不应停用或通知')



def workbuddy_hy4():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        env = dict(os.environ, PATH=f"{fake_bins(t)}:{os.environ['PATH']}",
                   AIW_STATE=str(t / "state"), AGENT_USE_HOME=str(fake_agent_use(t)),
                   FAKE_AU_LOG=str(t / "calls"))
        (t / "prompt.md").write_text("test")
        for seat, model in (("workbuddy", "free-model-a"), ("workbuddy-hy4", "free-model-b")):
            r = sh([str(REPO / "bin/aiw"), seat, str(t), "30", str(t / "prompt.md"), "--ro"], env=env)
            check(r.returncode == 0, r.stderr)
            call = json.loads((t / "calls").read_text().splitlines()[-1])
            check(call == {"order": "workbuddy", "model": [f"workbuddy={model}"], "options": ["workbuddy.fallback_models=" + ("free-model-a,free-model-b,free-model-c" if seat == "workbuddy" else "")], "no_tools": True}, str(call))
            check(latest_provider(t / "state").read_text().strip() == seat, "provider 应保留座位名")
    config = legacy_config()
    seat = config["seats"]["workbuddy-hy4"]
    check(seat["free_guard"] and seat["free_only"] and seat["kind"] == "agent_use", str(seat))
    check(config["fallback"]["workbuddy-hy4"] == ["workbuddy", "qoder", "opencode"], "Hy4 顶替链不对")
    check(all("workbuddy-hy4" not in chain for chain in config["fallback"].values()), "其他座位不应顶替到 Hy4")



def permodel_disable_rotates():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); env = free_guard_fixture(t)
        r = permodel_run(t, env, FAKE_MODEL_ATTEMPTS=json.dumps([
            {'reason': 'not_free', 'requested_model': 'free-model-a'},
            {'reason': 'ok', 'requested_model': 'free-model-b'}]))
        check(r.returncode == 0, repr(r))
        check(set(permodel_state(t)) == {'workbuddy:free-model-a'}, str(permodel_state(t)))
        r = permodel_run(t, env)
        call = json.loads((t / 'calls').read_text().splitlines()[-1])
        check(r.returncode == 0 and call['model'] == ['workbuddy=free-model-b']
              and call['options'] == ['workbuddy.fallback_models=free-model-b,free-model-c'], str(call))
        r = permodel_run(t, env, FAKE_AU_WORKBUDDY='payment_text')
        check('workbuddy:free-model-b' in permodel_state(t), repr(r))
        r = permodel_run(t, env)
        call = json.loads((t / 'calls').read_text().splitlines()[-1])
        check(r.returncode == 0 and call['model'] == ['workbuddy=free-model-c']
              and call['options'] == ['workbuddy.fallback_models='], str(call))



def permodel_all_disabled_skips_seat():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); env = free_guard_fixture(t)
        r = permodel_run(t, env, 'workbuddy-hy4', FAKE_AU_WORKBUDDY='credits')
        check(r.returncode == 0 and set(permodel_state(t)) == {'workbuddy:free-model-b'}, repr(r))
        r = permodel_run(t, env, 'workbuddy-hy4')
        rows = [json.loads(line) for line in (t / 'state/seat-ledger.jsonl').read_text().splitlines()]
        call = json.loads((t / 'calls').read_text().splitlines()[-1])
        check(r.returncode == 0 and rows[-2]['kind'] == 'skipped_disabled'
              and rows[-1]['actual'] == 'workbuddy' and 'workbuddy:free-model-b' in r.stderr, repr(r))
        check(call['model'] == ['workbuddy=free-model-a']
              and call['options'] == ['workbuddy.fallback_models=free-model-a,free-model-c'], str(call))



def permodel_credits_signal():
    for fields, expected in (({'selected_model': 'free-model-c', 'actual_model': 'free-model-b'}, 'free-model-c'),
                             ({'actual_model': 'free-model-b'}, 'free-model-b'),
                             ({}, 'free-model-a')):
        with tempfile.TemporaryDirectory() as td:
            t = Path(td); env = free_guard_fixture(t)
            for credits in ('0', '0.5'):
                r = permodel_run(t, env, FAKE_AU_CREDITS=credits, FAKE_ATTEMPT_FIELDS=json.dumps(fields))
                check(r.returncode == 0, repr(r))
                check(set(permodel_state(t)) == ({'workbuddy:' + expected} if credits == '0.5' else set()), str(permodel_state(t)))



def permodel_ledger_response_model():
    for fields, expected in (({'selected_model': 'free-model-c'}, 'free-model-c'),
                             ({'actual_model': 'free-model-b', 'selected_model': 'free-model-c'}, 'free-model-b'),
                             ({'requested_model': 'free-model-c'}, 'free-model-c'),
                             ({}, 'free-model-b'),
                             ({'requested_model': None}, 'free-model-b')):
        with tempfile.TemporaryDirectory() as td:
            t = Path(td); env = free_guard_fixture(t)
            r = permodel_run(t, env, 'workbuddy-cn', FAKE_ATTEMPT_FIELDS=json.dumps(fields))
            check(r.returncode == 0, repr(r))
            rows = [json.loads(line) for line in (t / 'state/seat-ledger.jsonl').read_text().splitlines()]
            check(len(rows) == 1 and rows[0]['actual'] == 'workbuddy-cn'
                  and rows[0]['kind'] == 'ok' and rows[0]['model'] == expected, str(rows))
            call = json.loads((t / 'calls').read_text().splitlines()[-1])
            check(call['model'] == ['workbuddy-cn=free-model-b'], str(call))



def permodel_enable():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); env = free_guard_fixture(t)
        permodel_run(t, env, 'workbuddy-hy4', FAKE_AU_WORKBUDDY='credits')
        state = permodel_state(t); state['workbuddy'] = dict(state['workbuddy:free-model-b'])
        (t / 'state/disabled.json').write_text(json.dumps(state))
        r = sh([str(REPO / 'bin/aiw'), 'enable', 'workbuddy'], env=env)
        check(r.returncode == 0 and set(permodel_state(t)) == {'workbuddy:free-model-b'}
              and '逐个 enable' in r.stdout and 'aiw enable workbuddy:free-model-b' in r.stdout, repr(r))
        r = sh([str(REPO / 'bin/aiw'), 'doctor'], env=env)
        check('模型 workbuddy:free-model-b' in r.stdout and 'credits=1.25' in r.stdout and 'since' in r.stdout, repr(r))
        r = sh([str(REPO / 'bin/aiw'), 'enable', 'workbuddy:free-model-b'], env=env)
        check(r.returncode == 0 and not permodel_state(t), repr(r))



def permodel_legacy_seat_entry():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); env = free_guard_fixture(t)
        (t / 'state').mkdir()
        (t / 'state/disabled.json').write_text(json.dumps({'workbuddy': {
            'since': '2026-10-09', 'evidence': 'legacy', 'notified': True}}))
        r = permodel_run(t, env)
        calls = [json.loads(line)['order'] for line in (t / 'calls').read_text().splitlines()]
        rows = [json.loads(line) for line in (t / 'state/seat-ledger.jsonl').read_text().splitlines()]
        check(r.returncode == 0 and calls == ['qoder'] and rows[0]['kind'] == 'skipped_disabled', repr(r))
        check(set(permodel_state(t)) == {'workbuddy'}, '旧条目不应迁移或清除')



def permodel_freecheck():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); env = free_guard_fixture(t)
        local = t / 'local.json'
        write_legacy_local(local, {'seats': {'antigravity': {'disabled_by_user': 'test-only disabled'}}})
        env['AIW_CONFIG'] = str(local)
        # Only the requested model hy4 reports a charge; other probes must continue.
        script = Path(env['AGENT_USE_HOME']) / 'scripts/run_agent.py'
        script.write_text(script.read_text().replace("if mode in ('credits', 'failedcredits'):",
            "if att['requested_model'] == 'free-model-b' and prov == 'workbuddy': att['credits'] = 0.5\nif mode in ('credits', 'failedcredits'):"))
        r = sh([str(REPO / 'bin/aiw'), 'freecheck'], env=env)
        check(r.returncode == 2 and set(permodel_state(t)) == {'workbuddy:free-model-b'}, repr(r))
        calls = [json.loads(line) for line in (t / 'calls').read_text().splitlines()]
        keys = [(c['order'], tuple(c['model'])) for c in calls]
        check(len(keys) == len(set(keys)) == 7, str(calls))
        wb = [c for c in calls if c['order'] == 'workbuddy']
        check([c['model'] for c in wb] == [['workbuddy=' + m] for m in ('free-model-a', 'free-model-b', 'free-model-c')], str(wb))
        check(all(c['options'] == ['workbuddy.fallback_models='] for c in wb), str(wb))
        cn = [c for c in calls if c['order'] == 'workbuddy-cn']
        check(all(c['options'] == ['workbuddy-cn.fallback_models='] for c in cn), str(cn))
        check(all(not c['options'] for c in calls if c['order'] not in ('workbuddy', 'workbuddy-cn')), str(calls))
        (t / 'calls').unlink()
        r = sh([str(REPO / 'bin/aiw'), 'freecheck'], env=env)
        calls = [json.loads(line) for line in (t / 'calls').read_text().splitlines()]
        check(r.returncode == 0 and not any(c['model'] == ['workbuddy=free-model-b'] for c in calls), repr(r))



def free_guard():
    script = r'''import ai_worker as w
for text in ('HTTP 402', 'status:402', 'code=402', 'error 402', '402 Payment', 'Payment Required', 'insufficient balance', 'insufficient funds', 'insufficient credit', 'insufficient credits', '余额不足', '请充值', '需要付费', '开通会员', '付费会员'):
    assert w.PAID_PAT.search(text), text
for text in ('elapsed 402ms', 'task #402', 'credit balance: 100 free credits', '1402', '4020', 'rate limit', 'quota exhausted', 'You exceeded your current quota, please check your plan and billing details'):
    assert not w.PAID_PAT.search(text), text
# 回答引用收费文本不触发，stderr 单独检测，不扫验证器拼入 err 的回答。
r = {'kind': 'format', 'err': 'Payment Required', 'stderr': ''}
assert w.guard_result('qoder', r) == r
'''
    r = inprocess_sh([sys.executable, '-c', script], cwd=REPO)
    check(r.returncode == 0, r.stderr)
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); env = free_guard_fixture(t)
        prompt = t / 'prompt'; prompt.write_text('test')
        def run(seat='qoder', **extra):
            return inprocess_sh([sys.executable, str(REPO / 'ai_worker.py'), seat, str(t), '10', str(prompt), '--ro'], env=dict(env, **extra))
        def disabled():
            p = t / 'state/disabled.json'
            return json.loads(p.read_text()) if p.exists() else {}
        def notices():
            return [json.loads(x) for x in (t / 'discord').read_text().splitlines()]
        r = run(FAKE_AU_QODER='credits')
        check(r.returncode == 0 and r.stdout == 'qoder ok\n' and '疑似收费' in r.stderr, repr(r))
        entry = disabled()['qoder:free-model-q']
        check(entry['reason'] == 'paid' and entry['evidence'] == 'credits=1.25' and len(entry['evidence']) <= 160, str(entry))
        check(len(notices()) == 1 and notices()[0]['has_token'] and notices()[0]['no_update'] == '1', str(notices()))
        check('test-only-token' not in str(notices()), 'token 进入 argv')
        check(notices()[0]['argv'][:4] == ['send', '--channel-id', 'test-channel', '--message'] and len(notices()[0]['argv'][4]) <= 400, str(notices()))
        r = run()
        calls = [json.loads(x)['order'] for x in (t / 'calls').read_text().splitlines()]
        check(calls == ['qoder', 'workbuddy'], '停用座位仍被派单')
        check(r.returncode == 0 and '已停用' in r.stderr and len(notices()) == 1, repr(r))
        rows = [json.loads(x) for x in (t / 'state/seat-ledger.jsonl').read_text().splitlines()]
        check(rows[-2]['kind'] == 'skipped_disabled', str(rows))
        r = inprocess_sh([sys.executable, str(REPO / 'ai_worker.py'), 'qoder', str(t), '10', str(prompt), '--ro', '--no-fallback'], env=env)
        check(r.returncode == 1, '停用点名未拒绝')
        r = sh([str(REPO / 'bin/aiw'), 'panel', str(t), '10', 'test', 'qoder', 'workbuddy', 'opencode'], env=env)
        check(r.returncode == 0 and '已停用' in r.stderr, repr(r))
        r = inprocess_sh([sys.executable, str(REPO / 'ai_worker.py'), 'doctor'], env=env)
        check('⛔ qoder 已停用（疑似收费）' in r.stdout and 'test-channel' in r.stdout and '待发通知：0' in r.stdout, repr(r))
        for seat in ('qoder:free-model-q', 'qoder:free-model-q', 'unknown'):
            r = inprocess_sh([sys.executable, str(REPO / 'ai_worker.py'), 'enable', seat], env=env)
            check(r.returncode == 0, repr(r))
        check(not disabled(), 'enable 未恢复')
        r = run()
        check(r.returncode == 0 and r.stdout == 'qoder ok\n', repr(r))
        for mode in ('paid', 'exhausted', 'not_free', 'payment_text', 'failedcredits'):
            r = run(FAKE_AU_QODER=mode)
            check(r.returncode == 0 and r.stdout == 'workbuddy ok\n' and 'qoder:free-model-q' in disabled(), f'{mode}: {r}')
            inprocess_sh([sys.executable, str(REPO / 'ai_worker.py'), 'enable', 'qoder:free-model-q'], env=env)
        for mode, kind in (('quota_text', 'quota'), ('rate_text', 'ratelimit')):
            r = run(FAKE_AU_QODER=mode)
            check(r.returncode == 0 and not disabled(), f'普通额度限流误停用: {r}')
            check(json.loads((t / 'state/breaker.json').read_text())['qoder']['reason'] == kind, repr(r))
            (t / 'state/breaker.json').unlink()
        for value in ('0', '"3"', 'true', '-1'):
            r = run(FAKE_AU_CREDITS=value)
            check(r.returncode == 0 and not disabled(), '非正数 credits 误停用')
        r = run(FAKE_AU_QODER='credits', FAKE_DISCORD_FAIL='1')
        check(r.returncode == 0 and 'auth' in r.stderr and 'test-only-token' not in r.stderr, repr(r))
        pending = t / 'state/notify-pending.jsonl'
        check(disabled()['qoder:free-model-q']['notified'] is False, '通知失败未持久化')
        r = inprocess_sh([sys.executable, str(REPO / 'freecheck.py')], env=env)
        check(r.returncode == 0 and disabled()['qoder:free-model-q']['notified'] is True, 'freecheck 未补发')
        # 所有座位停用不触发 agent-use，退出 1。
        state = disabled()
        for seat in ('workbuddy', 'antigravity', 'opencode'):
            state[seat] = state['qoder:free-model-q']
        (t / 'state/disabled.json').write_text(json.dumps(state))
        check(run().returncode == 1, '全链停用应 rc=1')



def agent_use_registry():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        env = dict(os.environ, HOME=str(t), AGENT_USE_CONFIG='',
                   AGENT_USE_HOME=str(t / 'agent-use'), AIW_STATE=str(t / 'state'))
        fallback = t / 'agent-use/config/dispatcher.json'
        default = t / '.config/agent-use/dispatcher.json'
        explicit = t / 'explicit.json'
        for path in (fallback, explicit):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('{}')
        probe = [sys.executable, '-c', 'import ai_worker as w; print(w.agent_use_registry())']
        def expect(path):
            r = sh(probe, cwd=REPO, env=env)
            check(r.returncode == 0 and r.stdout.strip() == str(path), repr(r))
        expect(fallback)
        default.parent.mkdir(parents=True)
        default.write_text('{}')
        expect(default)
        for value in ('', 'relative/config'):
            env['XDG_CONFIG_HOME'] = value
            expect(default)
        xdg = t / 'xdg/agent-use/dispatcher.json'
        xdg.parent.mkdir(parents=True)
        xdg.write_text('{}')
        env['XDG_CONFIG_HOME'] = str(xdg.parent.parent)
        expect(xdg)
        xdg.unlink()
        xdg.symlink_to(t / 'absent')
        r = sh(probe, cwd=REPO, env=env)
        check(r.returncode != 0 and 'RegistryError' in r.stderr, repr(r))
        env['XDG_CONFIG_HOME'] = ''
        env['AGENT_USE_CONFIG'] = '~/explicit.json'
        expect(explicit)
        default.unlink()
        default.symlink_to(explicit)
        env['AGENT_USE_CONFIG'] = ''
        expect(default)
        # 两种无效高优先级配置均不能回退到存在的示例注册表。
        script = """import freecheck as f
f.w.disable_seat = lambda *a, **kw: (_ for _ in ()).throw(AssertionError('误停用座位'))
f.urllib.request.urlopen = lambda *a, **kw: (_ for _ in ()).throw(AssertionError('不应请求目录'))
print(f.zen_check())
assert not f.w.disabled_seat('opencode')
"""
        env['AGENT_USE_CONFIG'] = str(t / 'missing.json')
        for broken in ('explicit', 'default'):
            if broken == 'default':
                env.pop('AGENT_USE_CONFIG')
                explicit.unlink()
            r = sh([sys.executable, '-c', script], cwd=REPO, env=env)
            expected_path = env.get('AGENT_USE_CONFIG', str(default))
            check(r.returncode == 0 and 'agent-use 注册表不可用' in r.stdout
                  and expected_path in r.stdout, repr(r))
            check(not (t / 'state/disabled.json').exists(), '注册表不可用误停用')



def freecheck():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); env = free_guard_fixture(t)
        local = t / 'local.json'
        write_legacy_local(local, {'seats': {'antigravity': {'disabled_by_user': 'test-only disabled'}}})
        env['AIW_CONFIG'] = str(local)
        config = t / 'registry/dispatcher.json'
        env['AGENT_USE_CONFIG'] = str(config)
        config.parent.mkdir()
        config.write_text(json.dumps({'providers': {'opencode': {'default_model': 'a-free', 'models': ['b-free'], 'options': {'fallback_models': 'c-free'}}}}))
        models = t / 'models.json'
        env['AIW_ZEN_MODELS_URL'] = models.as_uri()
        def run(**extra):
            return sh([str(REPO / 'bin/aiw'), 'freecheck'], env=dict(env, **extra))
        models.write_text(json.dumps({'data': [{'id': 'a-free'}] + [{'id': str(i)} for i in range(9)]}))
        r = run()
        check(r.returncode == 0 and '部分免费模型下架' in r.stdout, repr(r))
        check(not (t / 'state/disabled.json').exists(), '部分下架不应停用')
        check(len((t / 'discord').read_text().splitlines()) == 1, '部分下架未提醒')
        check(run().returncode == 0 and len((t / 'discord').read_text().splitlines()) == 1, '重复下架提醒')
        models.write_text(json.dumps({'data': [{'id': str(i)} for i in range(10)]}))
        (t / 'calls').unlink()
        r = run()
        check(r.returncode == 2 and '全部下架' in r.stdout, repr(r))
        calls = [json.loads(x)['order'] for x in (t / 'calls').read_text().splitlines()]
        check('opencode' not in calls and 'antigravity' not in calls and len(calls) == 6 and 'workbuddy-cn' in calls and calls.count('workbuddy') == 3, str(calls))
        check(run().returncode == 0, '旧停用不应 rc=2')
        r = run(FAKE_AU_QODER='credits', FAKE_AU_DOUBAO='weird')
        check(r.returncode == 2 and 'doubao: 探测失败（other），未停用' in r.stdout, repr(r))
        check(set(json.loads((t / 'state/disabled.json').read_text())) == {'qoder:free-model-q', 'opencode'}, '非收费失败误停用')
        sh([str(REPO / 'bin/aiw'), 'enable', 'opencode'], env=env)
        models.write_text('bad json')
        r = run()
        check(r.returncode == 0 and '列表检查失败' in r.stderr and 'opencode: ' in r.stdout, repr(r))
        check('opencode' not in json.loads((t / 'state/disabled.json').read_text()), '无效目录误停用')
        # freecheck 也会补发。
        (t / 'state/notify-pending.jsonl').write_text(json.dumps({'id': 'retry-id', 'message': 'retry'}) + '\n')
        check(run().returncode == 0 and (t / 'state/notify-pending.jsonl').read_text() == '', 'freecheck 未补发')



def freecheck_failure_streak():
    with tempfile.TemporaryDirectory() as td:
        env = free_guard_fixture(Path(td))
        script = """import contextlib, io, json
from unittest.mock import patch
import freecheck as f
w = f.w
cfg = {'provider': 'test-provider', 'free_guard': True}
w.SEATS = {'seats': {'test-seat': cfg, 'alias': cfg}}
result = {'kind': 'transient', 'reason': 'test_failure', 'rc': 1, 'secs': 0}
path = w.STATE / 'freecheck-failures.json'
pending = w.STATE / 'notify-pending.jsonl'
locked = False
real_lock = w.state_lock
real_queue = w.queue_notification
real_load = w.load_json
real_save = w.save_json
@contextlib.contextmanager
def lock():
    global locked
    with real_lock():
        locked = True
        try:
            yield
        finally:
            locked = False
def queue(message):
    assert locked
    real_queue(message)
def load(p, *args, **kwargs):
    if p == path:
        assert locked
    return real_load(p, *args, **kwargs)
def save(p, *args, **kwargs):
    if p == path:
        assert locked
    return real_save(p, *args, **kwargs)
def rows():
    return [json.loads(line) for line in pending.read_text().splitlines()] if pending.exists() else []
def probe(**changes):
    result.clear()
    result.update(kind='transient', reason='test_failure', rc=1, secs=0)
    result.update(changes)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert f.check() == 0
    return out.getvalue()
with patch.object(w, 'state_lock', lock), patch.object(w, 'queue_notification', queue), \\
     patch.object(w, 'load_json', load), patch.object(w, 'save_json', save), \\
     patch.object(w, 'retry_notifications'), patch.object(w, 'errand_names', return_value=[]), \\
     patch.object(w, 'disabled_seat', return_value=False), patch.object(w, 'ledger'), \\
     patch.object(w, 'available_models', return_value=['test-model']), \\
     patch.object(w, 'run_seat', side_effect=lambda *a, **kw: result), \\
     patch.object(f.socket, 'gethostname', return_value='test-host'), \\
     patch.object(w, 'now', return_value='test-time'):
    assert probe().strip() == 'test-seat: 探测失败（transient），未停用（test-model），连续 1 次'
    assert not rows()
    probe(reason='latest_failure')
    assert len(rows()) == 1
    assert rows()[0]['message'] == ('[aiw] 座位 test-seat（test-model）连续 2 次自检失败：'
        'transient/latest_failure。主机 test-host，test-time。修好前派单会被顶替。')
    assert probe().strip().endswith('连续 3 次') and len(rows()) == 1
    entry = json.loads(path.read_text())['test-provider:test-model']
    assert entry == {'count': 3, 'kind': 'transient', 'reason': 'test_failure', 'notified': True}
    probe(kind='ok', reason='ok', rc=0)
    assert json.loads(path.read_text()) == {}
    probe()
    before = path.read_bytes()
    for skipped in ({'reason': 'outside_free_window'}, {'reason': 'promotion_expired'},
                    {'disabled_key': 'test-provider:test-model'}):
        probe(**skipped)
        assert path.read_bytes() == before and len(rows()) == 1
    probe()
    assert len(rows()) == 2
    # Invalid JSON, invalid top-level shape and malformed entries all recover.
    for broken in ('{', '[]', '{"test-provider:test-model": {"count": "bad"}}',
                   '{"test-provider:test-model": null}'):
        path.write_text(broken)
        assert probe().strip().endswith('连续 1 次') and len(rows()) == 2
    # No model: key is the provider, with no trailing colon.
    with patch.object(w, 'available_models', return_value=[None]):
        probe()
    assert json.loads(path.read_text())['test-provider']['count'] == 1
"""
        r = sh([sys.executable, '-c', script], cwd=REPO, env=env)
        check(r.returncode == 0, r.stdout + r.stderr)


def freecheck_runner_hosts():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); env = free_guard_fixture(t)
        env['AIW_LAUNCH_AGENTS_DIR'] = str(t / 'agents')
        script = """import contextlib, io, os
from unittest.mock import patch
import freecheck as f
w = f.w
cfg = {'provider': 'test-provider', 'free_guard': True}
result = {'kind': 'ok', 'rc': 0, 'secs': 0}
ledger = w.STATE / 'seat-ledger.jsonl'
failures = w.STATE / 'freecheck-failures.json'
w.STATE.mkdir(parents=True, exist_ok=True)
failures.write_text('{"preserve": true}')
cases = [({}, {}, True), ({'runner_hosts': []}, {}, True),
         ({'runner_hosts': ['other-host']}, {}, False),
         ({'runner_hosts': [' RuNnEr-HoSt.example.invalid ']}, {}, True),
         ({'runner_hosts': ['other-host']}, {'AIW_NO_FORWARD': '1'}, True),
         ({'command': [], 'runner_hosts': ['other-host']}, {}, True),
         ({'runner_hosts': ['other-host']}, {'AIW_FORWARD': '[]'}, True),
         ({'command': [], 'runner_hosts': ['other-host']}, {'AIW_FORWARD': '["forward"]'}, False)]
with patch('socket.gethostname', return_value='runner-host.local'), \\
     patch.object(w, 'errand_names', return_value=[]), \\
     patch.object(w, 'disabled_seat', return_value=False), \\
     patch.object(w, 'available_models', return_value=['test-model']):
    for forward, environ, runs in cases:
        w.SEATS = {'seats': {'test-seat': cfg}, 'forward': {'command': ['forward'], **forward}}
        ledger.unlink(missing_ok=True)
        before = failures.read_bytes()
        out = io.StringIO()
        with patch.dict(os.environ, environ), patch.object(w, 'retry_notifications') as retry, \\
             patch.object(w, 'run_seat', return_value=result) as run, \\
             patch.object(f, 'record_probe', return_value=0) as record, \\
             contextlib.redirect_stdout(out):
            run.side_effect = lambda *a, **kw: (retry.assert_called_once(), result)[1]
            assert f.check() == 0
            retry.assert_called_once()
            assert run.call_count == record.call_count == int(runs)
        assert ledger.exists() == runs
        if runs:
            assert len(ledger.read_text().splitlines()) == 1
        else:
            assert out.getvalue().strip() == '本机派单转发到执行端（forward.runner_hosts 不含本机），跳过探测'
        assert failures.read_bytes() == before
    w.SEATS['forward'] = {'command': ['forward'], 'runner_hosts': ['other-host']}
    out = io.StringIO()
    with patch.object(f.subprocess, 'run') as launch, contextlib.redirect_stdout(out):
        assert f.install() == 0
        launch.assert_not_called()
        directory = f.Path(os.environ['AIW_LAUNCH_AGENTS_DIR'])
        assert not directory.exists()
        assert '不安装每日检查；已安装的可用 aiw freecheck --uninstall 卸载' in out.getvalue()
        directory.mkdir()
        path = directory / (f.LABEL + '.plist')
        path.write_bytes(b'existing plist')
        assert f.install() == 0 and path.read_bytes() == b'existing plist'
        assert f.install(True) == 0 and not path.exists()
        assert launch.call_count == 1 and launch.call_args.args[0][1] == 'bootout'
"""
        r = sh([sys.executable, '-c', script], cwd=REPO, env=env)
        check(r.returncode == 0, r.stdout + r.stderr)


def freecheck_install():
    import plistlib
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); env = free_guard_fixture(t)
        b = t / 'bin'; b.mkdir()
        launchctl = b / 'launchctl'
        launchctl.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$LAUNCH_LOG"\n[ "$1" = bootout ] && exit 1\nexit 0\n')
        launchctl.chmod(0o755)
        env.update(AIW_PYTHON=sys.executable, PATH=str(b) + ':' + env['PATH'], AIW_LAUNCH_AGENTS_DIR=str(t / 'agents'), LAUNCH_LOG=str(t / 'launch'))
        env.update(AIW_ROUTER_RULES=str(t / 'rules.json'), AGENT_USE_ERRAND_HOME=str(t / 'errands'),
                   AGENT_USE_CONFIG=str(t / 'registry.json'), XDG_CONFIG_HOME=str(t / 'xdg'))
        cmd = [str(REPO / 'bin/aiw'), 'freecheck']
        r = sh(cmd + ['--install'], env=env)
        check(r.returncode == 0, repr(r))
        path = t / 'agents/com.aiw.freecheck.plist'
        data = plistlib.loads(path.read_bytes())
        check(data['Label'] == 'com.aiw.freecheck' and data['ProgramArguments'] == [str(REPO / 'bin/aiw'), 'freecheck'], str(data))
        check(data['StartCalendarInterval'] == {'Hour': 9, 'Minute': 30}, str(data))
        check(data['StandardOutPath'] == data['StandardErrorPath'] == str(t / 'state/freecheck.log'), str(data))
        check(data['EnvironmentVariables']['PATH'].split(':') == [str(Path(env['HOME']) / '.local/bin'), str(Path(env['HOME']) / '.opencode/bin'), '/opt/homebrew/bin', '/usr/local/bin', '/usr/bin', '/bin'], str(data))
        for key in ('AIW_STATE', 'AGENT_USE_HOME', 'AGENT_USE_CONFIG', 'XDG_CONFIG_HOME', 'AIW_PYTHON', 'AIW_CONFIG', 'AIW_ROUTER_RULES', 'AGENT_USE_ERRAND_HOME'):
            check(data['EnvironmentVariables'][key] == env[key], key)
        empty_env = dict(env, AIW_CONFIG='', AIW_ROUTER_RULES='', AGENT_USE_CONFIG='', XDG_CONFIG_HOME='')
        empty_env.pop('AGENT_USE_ERRAND_HOME', None)
        check(sh(cmd + ['--install'], env=empty_env).returncode == 0, '重复安装失败')
        installed_env = plistlib.loads(path.read_bytes())['EnvironmentVariables']
        check(not any(key in installed_env for key in ('AIW_CONFIG', 'AIW_ROUTER_RULES', 'AGENT_USE_ERRAND_HOME', 'AGENT_USE_CONFIG', 'XDG_CONFIG_HOME')), str(installed_env))
        check(sh(cmd + ['--uninstall'], env=env).returncode == 0 and not path.exists(), '卸载失败')
        check(sh(cmd + ['--uninstall'], env=env).returncode == 0, '重复卸载失败')
        commands = (t / 'launch').read_text().splitlines()
        check([x.split()[0] for x in commands] == ['bootout', 'bootstrap', 'bootout', 'bootstrap', 'bootout', 'bootout'], str(commands))
        check(all(f'gui/{os.getuid()}' in x for x in commands), str(commands))



def free_guard_model_attempts():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); env = free_guard_fixture(t)
        prompt = t / 'prompt'; prompt.write_text('test')
        for reason in ('payment_required', 'credits_exhausted', 'not_free'):
            for mode in ('ok', 'weird'):
                attempts = [{'reason': reason, 'requested_model': 'charged-free'},
                            {'reason': 'ok', 'requested_model': 'other-free'}]
                r = sh([str(REPO / 'bin/aiw'), 'opencode', str(t), '10', str(prompt), '--ro', '--no-fallback'],
                       env=dict(env, FAKE_MODEL_ATTEMPTS=json.dumps(attempts), FAKE_AU_OPENCODE=mode))
                entry = json.loads((t / 'state/disabled.json').read_text())['opencode:charged-free']
                check(entry['evidence'] == f'model_attempts:{reason}@charged-free', str(entry))
                check(r.returncode == (0 if mode == 'ok' else 1), repr(r))
                if mode == 'ok':
                    check(r.stdout == 'opencode ok\n', repr(r))
                sh([str(REPO / 'bin/aiw'), 'enable', 'opencode:charged-free'], env=env)



def free_guard_outer_timeout():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); env = free_guard_fixture(t)
        script = r'''import json, subprocess
from pathlib import Path
import ai_worker as w
for source in ('none', 'stderr', 'reason', 'credits', 'models'):
    def timeout(argv, *args, **kwargs):
        out = Path(argv[4]); out.mkdir()
        att = {'reason': 'payment_required'} if source == 'reason' else {}
        if source == 'credits': att['credits'] = 2
        if source == 'models': att['model_attempts'] = [{'reason': 'not_free', 'requested_model': 'x-free'}]
        (out / 'status.json').write_text(json.dumps({'attempts': [att]}))
        raise subprocess.TimeoutExpired(argv, 31, stderr=b'HTTP 402' if source == 'stderr' else b'ordinary error')
    w.run_group = timeout
    r = w.run_agent_use('opencode', 'test', '.', 1)
    assert (r['kind'], r['rc'], r['err'], r['out']) == ('timeout', 124, 'timeout (outer)', ''), r
    assert Path(r['run_dir'], 'stderr.txt').read_text() == ('HTTP 402' if source == 'stderr' else 'ordinary error')
    key = 'opencode:x-free' if source == 'models' else 'opencode'
    assert (key in w.load_json(w.STATE / 'disabled.json', {})) == (source != 'none'), r
    w.enable_seat(key)
    import shutil
    shutil.rmtree(r['run_dir'])
'''
        r = sh([sys.executable, '-c', script], cwd=REPO, env=env)
        check(r.returncode == 0, r.stderr)



def free_guard_missing_token():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); env = free_guard_fixture(t); env.pop('DISCORD_TOKEN')
        b = t / 'bin'; b.mkdir()
        security = b / 'security'; security.write_text('#!/bin/sh\nexit 1\n'); security.chmod(0o755)
        env['PATH'] = str(b) + ':' + env['PATH']
        script = '''import ai_worker as w
assert w.send_discord('test') == 'missing_token'
w.disable_seat('qoder', 'payment required')
with w.state_lock(): w.queue_notification('partial')
w.retry_notifications()
assert w.disabled_seat('qoder')['notified'] is False
assert w.pending_count() == 2
'''
        r = sh([sys.executable, '-c', script], cwd=REPO, env=env)
        check(r.returncode == 0 and not (t / 'discord').exists(), r.stderr)



def free_guard_notification_diagnostics():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); env = free_guard_fixture(t)
        token = 'fake-discord-secret-abcdef123456'
        env['DISCORD_TOKEN'] = token
        binary = Path(env['AIW_DISCORD_USE_BIN'])
        payload = ('prefix' * 200 + '\ncredential=' + token +
                   '\nAuthorization: Bot header-secret\nBearer bearer-secret\n' +
                   'https://discord.com/api/webhooks/123456/webhook-secret' +
                   '\nlast\tline\x1b\x7f\x85')
        binary.write_text('#!' + sys.executable + '\nimport sys\n'
                          + 'sys.stderr.write(' + repr(payload) + ')\nsys.exit(37)\n')
        script = "import ai_worker as w; assert w.send_discord('test') == 'send_failed'"
        r = sh([sys.executable, '-c', script], cwd=REPO, env=env)
        check(r.returncode == 0 and 'discord-use rc=37 stderr=' in r.stderr, r.stderr)
        diagnostic = r.stderr.split('stderr=', 1)[1].rstrip('\n')
        check(len(diagnostic) == 512 and len(r.stderr.splitlines()) == 1, repr(r.stderr))
        for secret in (token, 'header-secret', 'bearer-secret', 'webhook-secret'):
            check(secret not in r.stderr, '凭据泄露')
        check(r'last\tline\u001b\u007f\u0085' in diagnostic, repr(diagnostic))
        # 分类仍读取原输出；认证和凭据失败同样留下脱敏诊断。
        for response, category in (('401 unauthorized', 'auth'), ('invalid token', 'credential')):
            binary.write_text('#!' + sys.executable + '\nimport sys\n'
                              + 'sys.stderr.write(' + repr(response) + ')\nsys.exit(38)\n')
            r = sh([sys.executable, '-c',
                    f"import ai_worker as w; assert w.send_discord('test') == {category!r}"], cwd=REPO, env=env)
            check(r.returncode == 0 and 'rc=38' in r.stderr, r.stderr)


def free_guard_notifications_unlocked():
    import time
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); env = free_guard_fixture(t)
        script = '''import ai_worker as w
with w.state_lock():
    w.queue_notification('first')
    w.queue_notification('second')
'''
        check(sh([sys.executable, '-c', script], cwd=REPO, env=env).returncode == 0, 'seed')
        binary = Path(env['AIW_DISCORD_USE_BIN'])
        binary.write_text('''#!/usr/bin/env python3
import os, sys, time
from pathlib import Path
p = Path(os.environ['FAKE_DISCORD_LOG'])
with p.open('a') as f: f.write(sys.argv[-1] + '\\n')
if sys.argv[-1] == 'second':
    release = Path(os.environ['FAKE_DISCORD_LOG']).with_suffix('.release')
    while not release.exists(): time.sleep(.02)
    sys.exit(0)
''')
        p = subprocess.Popen([sys.executable, '-c', 'import ai_worker as w; w.retry_notifications()'], cwd=REPO, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            wait_until(lambda: (t / 'discord').exists() and 'second' in (t / 'discord').read_text(),
                       'sender did not start', timeout=120)
            rows = [json.loads(x) for x in (t / 'state/notify-pending.jsonl').read_text().splitlines()]
            check([x['message'] for x in rows] == ['second'], '成功项未立即删除')
            start = time.monotonic()
            r = subprocess.run([str(REPO / 'bin/aiw'), 'enable', 'qoder'], env=env, capture_output=True, timeout=2)
            check(r.returncode == 0 and time.monotonic() - start < 2, '发送持全局锁')
            r = sh([sys.executable, '-c', "import ai_worker as w\nwith w.state_lock(): w.queue_notification('second')"], cwd=REPO, env=env)
            check(r.returncode == 0, r.stderr)
            (t / 'discord.release').touch()
            p.communicate(timeout=6)
            rows = [json.loads(x) for x in (t / 'state/notify-pending.jsonl').read_text().splitlines()]
            check(len(rows) == 1 and rows[0]['message'] == 'second', '按 id 删除误删新入队消息')
        finally:
            if p.poll() is None:
                p.kill(); p.communicate()



def freecheck_disabled_retry():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); env = free_guard_fixture(t)
        # 模拟停用已落盘、发送前崩溃；不依赖另一通知队列。
        state = t / 'state'; state.mkdir()
        entry = {'id': 'crash-event', 'since': 'now', 'host': 'test', 'reason': 'paid', 'evidence': 'HTTP 402', 'notified': False}
        (state / 'disabled.json').write_text(json.dumps({'qoder': entry}))
        r = sh([str(REPO / 'bin/aiw'), 'freecheck'], env=env)
        check(r.returncode == 0, repr(r))
        check(json.loads((state / 'disabled.json').read_text())['qoder']['notified'] is True, 'freecheck 未确认通知')
        check(len((t / 'discord').read_text().splitlines()) == 1, '停用通知未补发')



def freecheck_catalog_safety():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); env = free_guard_fixture(t)
        config = t / 'registry/dispatcher.json'
        env['AGENT_USE_CONFIG'] = str(config); config.parent.mkdir()
        config.write_text(json.dumps({'providers': {'opencode': {'default_model': 'a-free'}}}))
        script = '''import io, json
import freecheck as f
for rows in ([], [{'id': str(i)} for i in range(9)], [{'bad': 'row'}]):
    def fetch(request, timeout):
        assert request.get_header('User-agent') == 'aiw-freecheck/1'
        return io.StringIO(json.dumps({'data': rows}))
    f.urllib.request.urlopen = fetch
    try: f.zen_check()
    except ValueError: assert rows == [{'bad': 'row'}]
    assert not f.w.disabled_seat('opencode')
    assert f.w.pending_count() == 0
'''
        r = sh([sys.executable, '-c', script], cwd=REPO, env=env)
        check(r.returncode == 0 and not (t / 'discord').exists(), r.stderr)



def antigravity_disabled_direct():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); env = free_guard_fixture(t)
        local = t / 'local.json'
        write_legacy_local(local, {'seats': {'antigravity': {'disabled_by_user': '测试停用原因'}}})
        env['AIW_CONFIG'] = str(local)
        prompt = t / 'prompt'; prompt.write_text('test')
        cmd = [str(REPO / 'bin/aiw')]
        r = sh(cmd + ['antigravity', str(t), '10', str(prompt), '--ro'], env=env)
        check(r.returncode == 1 and '已被用户停用' in r.stderr and '测试停用原因' in r.stderr, repr(r))
        check(not (t / 'calls').exists(), '停用座位调用了 agent-use')
        for name in ('breaker.json', 'disabled.json', 'seat-ledger.jsonl'):
            check(not (t / 'state' / name).exists(), '停用点名产生状态：' + name)
        r = sh(cmd + ['panel', str(t), '10', 'test', 'antigravity', 'qoder', 'workbuddy'], env=env)
        check(r.returncode == 0 and '已被用户停用' in r.stderr, repr(r))
        check({json.loads(x)['order'] for x in (t / 'calls').read_text().splitlines()} == {'qoder', 'workbuddy'}, '显式 panel 未跳过')
        r = sh(cmd + ['doctor'], env=env)
        check(r.returncode == 0 and '⛔ antigravity 已被用户停用：' in r.stdout, repr(r))
        sh(cmd + ['enable', 'antigravity'], env=env)
        r = sh(cmd + ['antigravity', str(t), '10', str(prompt), '--ro'], env=env)
        check(r.returncode == 1 and '已被用户停用' in r.stderr, 'enable 不应解除用户停用')



def antigravity_not_in_chains():
    config = legacy_config()
    check('antigravity' not in config['panel_default'] + config['panel_free'], '会诊名单残留')
    check(all('antigravity' not in v for v in config['fallback'].values()), '顶替链残留')
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); env = free_guard_fixture(t)
        local = t / 'local.json'
        write_legacy_local(local, {'seats': {'antigravity': {'disabled_by_user': '测试停用原因'}}})
        env['AIW_CONFIG'] = str(local)
        prompt = t / 'prompt'; prompt.write_text('test')
        # 注入旧顶替链，验证配置之外的运行时防线。
        code = """import ai_worker as w, sys
w.SEATS['fallback']['workbuddy'] = ['antigravity', 'qoder', 'opencode']
sys.argv = ['aiw', 'workbuddy', sys.argv[1], '10', sys.argv[2], '--ro']
raise SystemExit(w.main())
"""
        r = sh([sys.executable, '-B', '-c', code, str(t), str(prompt)], cwd=REPO,
               env=dict(env, FAKE_AU_WORKBUDDY='quota', FAKE_AU_QODER='quota'))
        calls = [json.loads(x)['order'] for x in (t / 'calls').read_text().splitlines()]
        check(r.returncode == 0 and calls == ['workbuddy', 'qoder', 'opencode'], repr(r) + str(calls))
        check('antigravity' not in json.loads((t / 'state/breaker.json').read_text()), '跳过仍熔断')
        r = sh([str(REPO / 'bin/aiw'), 'freecheck'], env=env)
        check(r.returncode == 0 and 'antigravity: 已被用户停用，跳过' in r.stdout, repr(r))
        check(all(json.loads(x)['order'] != 'antigravity' for x in (t / 'calls').read_text().splitlines()), 'freecheck 探测停用座位')



CASES = {case.__name__: case for case in (
    workbuddy_cn_argv,
    workbuddy_cn_fallback_models,
    workbuddy_cn_offhours_substitute,
    workbuddy_cn_promotion_expired,
    workbuddy_cn_freecheck_promotion_expired,
    workbuddy_cn_freecheck_offhours,
    workbuddy_hy4,
    permodel_disable_rotates,
    permodel_all_disabled_skips_seat,
    permodel_credits_signal,
    permodel_ledger_response_model,
    permodel_enable,
    permodel_legacy_seat_entry,
    permodel_freecheck,
    free_guard,
    agent_use_registry,
    freecheck,
    freecheck_failure_streak,
    freecheck_install,
    freecheck_runner_hosts,
    free_guard_model_attempts,
    free_guard_outer_timeout,
    free_guard_missing_token,
    free_guard_notification_diagnostics,
    free_guard_notifications_unlocked,
    freecheck_disabled_retry,
    freecheck_catalog_safety,
    antigravity_disabled_direct,
    antigravity_not_in_chains,
)}
