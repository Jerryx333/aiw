"""结构化结果与流水线回归用例。"""
from support import REPO, Path, check, json, os, sh, sys, write_legacy_local

# 按规则出答的假 codex：FAKE_PLAN 是规则列表 {match, model?, outputs, delay?}，取第一条命中的；
# 每条规则各自计数，第 n 次调用取 outputs[min(n, 末项)]。每次调用在 FAKE_LOG 追加开始/结束时间。
FAKE_CODEX = r'''#!/usr/bin/env python3
import json, os, sys, time, fcntl
if sys.argv[1:2] == ["--version"]:
    print("codex-cli fake"); sys.exit(0)
args = sys.argv[1:]
model = args[args.index("-m") + 1] if "-m" in args else ""
prompt = args[-1]
plan = json.load(open(os.environ["FAKE_PLAN"]))
for index, rule in enumerate(plan):
    if rule["match"] in prompt and rule.get("model", model) == model:
        break
else:
    print("没有匹配的规则"); sys.exit(0)
counter = os.environ["FAKE_PLAN"] + f".{index}.count"
with open(counter + ".lock", "w") as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    n = int(open(counter).read()) if os.path.exists(counter) else 0
    open(counter, "w").write(str(n + 1))
started = time.time()
if rule.get("gate"):
    # 等到某个文件出现才交卷，用来确定地控制先后（不靠猜 sleep）
    deadline = time.time() + 60
    while not os.path.exists(rule["gate"]) and time.time() < deadline:
        time.sleep(0.05)
time.sleep(rule.get("delay", 0))
outputs = rule["outputs"]
out = outputs[min(n, len(outputs) - 1)]
print(out)
if rule.get("touch"):
    open(rule["touch"], "a").close()
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write(json.dumps({"rule": index, "model": model, "n": n, "start": started, "end": time.time(),
                          "prompt": prompt}, ensure_ascii=False) + "\n")
'''

ISSUES = {"type": "object", "required": ["issues"], "additionalProperties": False,
          "properties": {"issues": {"type": "array", "items": {
              "type": "object", "required": ["claim"], "additionalProperties": False,
              "properties": {"claim": {"type": "string", "minLength": 1}}}}}}


def block(value) -> str:
    return "正文\n<<<AIW-RESULT\n" + json.dumps(value, ensure_ascii=False) + "\nAIW-RESULT>>>"


def setup(t: Path, plan: list, models: dict | None = None) -> dict:
    fake = t / "fakecodex"
    fake.write_text(FAKE_CODEX)
    fake.chmod(0o755)
    (t / "plan.json").write_text(json.dumps(plan, ensure_ascii=False))
    (t / "issues.json").write_text(json.dumps(ISSUES))
    env = dict(os.environ, CODEX_BIN=str(fake), FAKE_PLAN=str(t / "plan.json"), FAKE_LOG=str(t / "log.jsonl"),
               AIW_NO_FORWARD="1")
    if models:
        local = t / "seats.local.json"
        write_legacy_local(local, {"seats": {seat: {"model": model} for seat, model in models.items()}})
        env["AIW_CONFIG"] = str(local)
    return env


def calls(t: Path) -> list:
    path = t / "log.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def worker(t: Path, env: dict, *extra, seat="codex-sol"):
    (t / "p.md").write_text("评审这段代码")
    return sh([sys.executable, str(REPO / "ai_worker.py"), seat, str(t), "30", str(t / "p.md"), "--ro",
               "--schema", str(t / "issues.json"), *extra], env=env)


def ledger(env: dict) -> list:
    path = Path(env["AIW_STATE"]) / "seat-ledger.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def structured_schema_units():
    import structured as st
    check(st.validate({"issues": [{"claim": 1}], "x": 1}, ISSUES) ==
          ["$.issues[0].claim: 应为 string，实际是 int", "$: 不允许的字段 x"], "校验错误不对")
    check(st.validate(True, {"type": "integer"}) and st.validate(1, {"enum": [True]}), "布尔不能当数字")
    check(st.validate([], {"type": "array", "minItems": 1}) == ["$: 至少 1 项"], "minItems")
    for bad in ({"oneOf": []}, {"type": "date"}, {"properties": {"a": {"pattern": "x"}}}, {"enum": []}, []):
        try:
            st.check_schema(bad)
        except st.SchemaError:
            continue
        raise AssertionError(f"不支持的 schema 没被拒绝：{bad}")
    body, value, error = st.extract("前文\n<<<AIW-RESULT\n```json\n{\"a\": 1}\n```\nAIW-RESULT>>>\n---\n【aiw 观察记录】\n- x\n")
    check(error is None and value == {"a": 1} and body == "前文\n---\n【aiw 观察记录】\n- x", f"提取不对：{body!r} {value}")
    for bad in ("前文 <<<AIW-RESULT\n{}\nAIW-RESULT>>>", "<<<AIW-RESULT\n{}", "<<<AIW-RESULT\n{bad}\nAIW-RESULT>>>",
                "<<<AIW-RESULT\n{\"a\": 1, \"a\": 2}\nAIW-RESULT>>>", "<<<AIW-RESULT\nNaN\nAIW-RESULT>>>", "没有块"):
        text, value, error = st.extract(bad)
        check(text == bad and value is None and error, f"坏块没报错或吞了原文：{bad!r}")
    _, value, _ = st.extract("<<<AIW-RESULT\n{\"a\": 1}\nAIW-RESULT>>>\n中间\n<<<AIW-RESULT\n{\"a\": 2}\nAIW-RESULT>>>")
    check(value == {"a": 2}, "应取最后一个块")


def structured_ok_and_json():
    t = Path(os.environ["HOME"]) / "s1"; t.mkdir()
    env = setup(t, [{"match": "评审", "outputs": [block({"issues": [{"claim": "a"}]})]}])
    p = worker(t, env)
    check(p.returncode == 0 and p.stdout.rstrip().endswith("AIW-RESULT>>>"), p.stdout + p.stderr)
    import structured as st
    _, value, error = st.extract(p.stdout)
    check(error is None and value == {"issues": [{"claim": "a"}]}, p.stdout)
    path = Path(next(l for l in p.stderr.splitlines() if "结构化结果：" in l).split("结构化结果：", 1)[1])
    saved = json.loads(path.read_text())
    check(saved["ok"] and saved["value"] == value and saved["attempts"] == 1 and saved["actual"] == "codex-sol", str(saved))
    p = worker(t, env, "--json")
    check(p.returncode == 0 and json.loads(p.stdout) == value, "--json 应只输出 JSON：" + p.stdout)
    check("AIW-RESULT" in calls(t)[0]["prompt"] and '"claim"' in calls(t)[0]["prompt"], "说明里没追加格式要求")
    check(len(ledger(env)) == 2 and all(r["kind"] == "ok" for r in ledger(env)), "账本应各一行 ok")
    p = sh([sys.executable, str(REPO / "ai_worker.py"), "codex-sol", str(t), "30", str(t / "p.md"), "--json"], env=env)
    check(p.returncode == 64 and "--json 必须与 --schema 同用" in p.stderr, p.stderr)
    (t / "bad.json").write_text('{"oneOf": []}')
    p = sh([sys.executable, str(REPO / "ai_worker.py"), "codex-sol", str(t), "30", str(t / "p.md"), "--schema", str(t / "bad.json")], env=env)
    check(p.returncode == 64 and "不支持的关键字" in p.stderr and not calls(t)[2:], "坏 schema 应在派单前拒绝")


def structured_retry_then_ok():
    t = Path(os.environ["HOME"]) / "s2"; t.mkdir()
    env = setup(t, [{"match": "评审", "outputs": [block({"issues": [{"claim": 1}]}), block({"issues": []})]}])
    result = t / "out.json"
    p = worker(t, dict(env, AIW_RESULT_FILE=str(result)))
    check(p.returncode == 0, "重交后应成功：" + (p.stdout + p.stderr).replace("\n", " | "))
    log = calls(t)
    check(len(log) == 2 and "格式重交" in log[1]["prompt"] and "$.issues[0].claim: 应为 string" in log[1]["prompt"]
          and '{"claim": 1}' in log[1]["prompt"], "重交说明应带错误和上次原文")
    saved = json.loads(result.read_text())
    check(saved["ok"] and saved["value"] == {"issues": []} and saved["attempts"] == 2, str(saved))
    rows = ledger(env)
    check([r["kind"] for r in rows] == ["format", "ok"] and rows[0]["rc"] == 0, f"账本：{rows}")
    check("同座位重交一次" in p.stderr, p.stderr)


def structured_retry_still_bad():
    t = Path(os.environ["HOME"]) / "s3"; t.mkdir()
    env = setup(t, [{"match": "评审", "outputs": ["我忘了写结果块"]}])
    result = t / "out.json"
    p = worker(t, dict(env, AIW_RESULT_FILE=str(result)))
    check(p.returncode == 2, p.stdout + p.stderr)
    check("重交后仍不合格" in p.stderr and "没有 <<<AIW-RESULT" in p.stderr and p.stderr.count("原文：") == 2, p.stderr)
    check("我忘了写结果块" in p.stdout, "原文不能丢：" + p.stdout)
    saved = json.loads(result.read_text())
    check(not saved["ok"] and saved["errors"] and "value" not in saved, str(saved))
    check(len(calls(t)) == 2 and [r["kind"] for r in ledger(env)] == ["format", "format"], "最多重交一次")
    for row in ledger(env):
        check((Path(row["run_dir"]) / "output.md").read_text().strip() == "我忘了写结果块", "每次原文都要留在 run 目录")


def structured_failure_passthrough():
    t = Path(os.environ["HOME"]) / "s4"; t.mkdir()
    env = setup(t, [{"match": "评审", "outputs": [""]}])
    p = worker(t, env)
    check(p.returncode == 2 and len(calls(t)) == 1, "空回答不是格式问题，不重交：" + p.stderr)
    p = worker(t, dict(env, AIW_NESTED="1"))
    check(p.returncode == 64 and "嵌套" in p.stderr, "执行方里不能派结构化单")


def structured_panel():
    t = Path(os.environ["HOME"]) / "s5"; t.mkdir()
    env = setup(t, [{"match": "评审", "model": "m-good", "outputs": [block({"issues": [{"claim": "好"}]})]},
                    {"match": "评审", "model": "m-bad", "outputs": ["没有块"]}],
                models={"codex": "m-good", "codex-sol": "m-bad"})
    (t / "p.md").write_text("评审这段代码")
    p = sh(["bash", str(REPO / "bin" / "aiw"), "panel", str(t), "30", str(t / "p.md"), "codex", "codex-sol",
            "--schema", str(t / "issues.json")], env=dict(env, AIW_MAIN="claude"))
    d = max((Path(env["AIW_STATE"]) / "panels").iterdir())
    summary = json.loads((d / "summary.json").read_text())
    rows = {r["seat"]: r for r in summary["seats"]}
    check(rows["codex"]["result"] == {"issues": [{"claim": "好"}]} and rows["codex"]["rc"] == 0, str(summary))
    check(rows["codex-sol"]["result"] is None and rows["codex-sol"]["rc"] == 2, str(summary))
    check(p.returncode == 1 and summary["answered"] == ["codex"], "不合格的一家不算有效意见")
    check(all("会诊回答格式要求" not in c["prompt"] for c in calls(t)), "带 schema 不应再追加自然语言格式要求")
    check(json.loads((d / "codex.result.json").read_text())["ok"], "每家结果文件")


FAKE_FORWARD = r'''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
log = Path(os.environ["FORWARD_LOG"])
calls = json.loads(log.read_text()) if log.exists() else []
calls.append({"argv": sys.argv[1:], "schema_env": [k for k in os.environ if k.startswith("AIW_SCHEMA")],
              "prompt": Path(sys.argv[4]).read_text()})
log.write_text(json.dumps(calls, ensure_ascii=False))
outputs = json.loads(os.environ["FORWARD_OUTPUTS"])
print(outputs[min(len(calls) - 1, len(outputs) - 1)])
(Path(os.environ["AIW_FORWARD_RUN_DIR"]) / "forward.json").write_text('{"actual": "codex-sol", "host": "remote"}')
'''


def structured_forward():
    """转发到别的机器：执行端不校验时本机在回传的 stdout 上校验，不合格同样重交一次。"""
    t = Path(os.environ["HOME"]) / "s6"; t.mkdir()
    env = setup(t, [])
    script = t / "forward"
    script.write_text(FAKE_FORWARD)
    script.chmod(0o755)
    env.pop("AIW_NO_FORWARD")
    env.update(AIW_FORWARD=json.dumps([str(script)]), FORWARD_LOG=str(t / "fwd.json"), AIW_MAIN="claude",
               FORWARD_OUTPUTS=json.dumps(["没有结果块", block({"issues": [{"claim": "远端"}]})]))
    p = worker(t, env, "--json")
    check(p.returncode == 0 and json.loads(p.stdout) == {"issues": [{"claim": "远端"}]}, "转发单重交后应成功：" + p.stdout + p.stderr)
    log = json.loads((t / "fwd.json").read_text())
    check(len(log) == 2 and "--no-fallback" in log[1]["argv"] and "格式重交" in log[1]["prompt"], f"转发重交：{log}")
    check(all(not c["schema_env"] for c in log), "本机 schema 路径不能带到执行端")
    check("AIW-RESULT" in log[0]["prompt"], "转发说明应带格式要求")
    env["FORWARD_OUTPUTS"] = json.dumps(["没有结果块"])
    (t / "fwd.json").unlink()
    p = worker(t, env)
    check(p.returncode == 2 and "重交后仍不合格" in p.stderr and "没有结果块" in p.stdout, p.stderr)


VERDICT = {"type": "object", "required": ["verdict"], "properties": {"verdict": {"enum": ["confirmed", "rejected"]}}}


def flow_setup(t: Path, verify_delay=0.0, slow_outputs=None) -> dict:
    """两家评审（codex 快、codex-sol 慢且等第一份核实交回才交卷）→ 每条核实 → 程序汇总。"""
    t.mkdir(parents=True, exist_ok=True)
    env = setup(t, [
        {"match": "VERIFY-ME", "outputs": [block({"verdict": "confirmed"})], "touch": str(t / "gate"), "delay": verify_delay},
        {"match": "REVIEW-ME", "model": "m-fast", "outputs": [block({"issues": [{"claim": "快1"}, {"claim": "快2"}]})]},
        {"match": "REVIEW-ME", "model": "m-slow", "outputs": slow_outputs or [block({"issues": [{"claim": "慢1"}]})],
         "gate": str(t / "gate")},
    ], models={"codex": "m-fast", "codex-sol": "m-slow"})
    write_flow(t, "VERIFY-ME 核实这条：{{item}}")
    return dict(env, AIW_MAIN="claude")


def write_flow(t: Path, verify_prompt: str, review_prompt="REVIEW-ME 评审 {{vars.target}}") -> None:
    (t / "flow.json").write_text(json.dumps({"workdir": ".", "jobs": 4, "vars": {"target": "这次改动"}, "steps": [
        {"id": "review", "kind": "fanout", "seats": ["codex", "codex-sol"], "prompt": review_prompt,
         "schema": "issues.json", "timeout": 60},
        {"id": "verify", "kind": "each", "from": "review", "items": "issues", "seat": "codex-sol",
         "prompt": verify_prompt, "schema": VERDICT, "timeout": 60},
        {"id": "report", "kind": "reduce", "from": ["review", "verify"]}]}, ensure_ascii=False))


def flow_cmd(t: Path, *extra) -> list:
    return [sys.executable, str(REPO / "flow.py"), "run", str(t / "flow.json"), *extra]


def flow_events(root: Path) -> list:
    return [json.loads(line) for line in (root / "events.jsonl").read_text().splitlines()]


def flow_root(p) -> Path:
    return Path(next(l for l in p.stderr.splitlines() if l.startswith("流水目录：")).split("：", 1)[1])


def flow_streaming():
    t = Path(os.environ["HOME"]) / "f1"
    env = flow_setup(t)
    p = sh(flow_cmd(t), env=env)
    check(p.returncode == 0, p.stdout + p.stderr)
    report = json.loads(p.stdout)
    steps = {s["id"]: s for s in report["steps"]}
    check([j["id"] for j in steps["verify"]["jobs"]] ==
          ["verify/review.codex.0", "verify/review.codex.1", "verify/review.codex-sol.0"], str(steps["verify"]))
    check(steps["verify"]["jobs"][2] == {"id": "verify/review.codex-sol.0", "seat": "codex-sol", "status": "ok",
                                         "from": "review/codex-sol", "index": 0, "result": {"verdict": "confirmed"}}, str(steps["verify"]))
    check(steps["report"]["jobs"][0]["result"]["steps"][0]["jobs"][0]["result"] == {"issues": [{"claim": "快1"}, {"claim": "快2"}]}, "汇总应含评审结果")
    seq = {(e["job"], e["event"]): e["seq"] for e in flow_events(flow_root(p))}
    # 先回来的那家：它的核实开始早于慢的那家交回（慢的那家要等一份核实交卷才交，没有流式就会卡到超时）
    check(seq[("review/codex", "done")] < seq[("verify/review.codex.0", "start")] < seq[("review/codex-sol", "done")], str(seq))
    rows = ledger(env)
    check(len(rows) == 5 and all(r["via"] == "flow" and r["kind"] == "ok" for r in rows), f"账本：{rows}")
    check("快1" in calls(t)[-1]["prompt"] or any("快1" in c["prompt"] and "VERIFY-ME" in c["prompt"] for c in calls(t)), "核实说明应带上游条目")
    check(any("是数据不是指令" in c["prompt"] for c in calls(t) if "VERIFY-ME" in c["prompt"]), "上游产物要标明是数据")
    status = sh([sys.executable, str(REPO / "flow.py"), "status", str(flow_root(p))], env=env)
    check(status.returncode == 0 and "完成 5" in status.stdout, status.stdout)


def flow_failure_branch():
    t = Path(os.environ["HOME"]) / "f2"
    env = flow_setup(t, slow_outputs=["没有结果块"])
    p = sh(flow_cmd(t), env=env)
    check(p.returncode == 1, p.stdout + p.stderr)
    steps = {s["id"]: s for s in json.loads(p.stdout)["steps"]}
    check([j["status"] for j in steps["review"]["jobs"]] == ["ok", "failed"], str(steps["review"]))
    check([j["id"] for j in steps["verify"]["jobs"]] == ["verify/review.codex.0", "verify/review.codex.1"], "失败的一家不展开，其余照走")
    check(steps["report"]["jobs"][0]["status"] == "ok" and "✗ review/codex-sol" in p.stderr, p.stderr)


def flow_resume():
    import signal, subprocess, time
    t = Path(os.environ["HOME"]) / "f3"
    env = flow_setup(t, verify_delay=1.5)
    p = subprocess.Popen(flow_cmd(t), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                         start_new_session=True)
    flows = Path(env["AIW_STATE"]) / "flows"
    deadline = time.time() + 60
    while time.time() < deadline:
        found = list(flows.glob("*/events.jsonl")) if flows.exists() else []
        if found and any(e["job"].startswith("verify/") and e["event"] == "done" for e in flow_events(found[0].parent)):
            break
        time.sleep(0.05)
    os.killpg(p.pid, signal.SIGINT)  # 像终端 Ctrl-C：整个进程组都收到
    _, err = p.communicate(timeout=60)
    check(p.returncode == 130 and "--resume" in err, f"中断返回码 {p.returncode}: {err}")
    root = found[0].parent
    check(not (root / "report.json").exists(), "中断不应出报告")
    before = len(ledger(env))
    done = {e["job"] for e in flow_events(root) if e["event"] == "done" and e["job"] != "report"}
    resumed = sh(flow_cmd(t, "--resume", str(root)), env=env)
    check(resumed.returncode == 0, resumed.stdout + resumed.stderr)
    events = flow_events(root)
    after_resume = events[next(i for i, e in enumerate(events) if e["event"] == "resume"):]
    reused = {e["job"] for e in after_resume if e["event"] == "reused"}
    dispatched = {e["job"] for e in after_resume if e["event"] == "start"}
    check(done <= reused and not (done & dispatched), f"已完成的单不能重派：完成 {done}，复用 {reused}，重派 {dispatched}")
    check(len(ledger(env)) - before == len(dispatched) and len(dispatched) + len(reused) == 5,
          f"账本只多出补派的单：{before}→{len(ledger(env))}，补派 {dispatched}")
    # 不打断跑一遍，报告逐字相同
    t2 = Path(os.environ["HOME"]) / "f3-straight"
    env2 = flow_setup(t2)
    straight = sh(flow_cmd(t2), env=env2)
    check(straight.returncode == 0 and straight.stdout == resumed.stdout, "中断续跑与一次跑完的报告应逐字一致")
    # 改核实说明：评审复用，核实全部重跑
    write_flow(t, "VERIFY-ME 请核实这条：{{item}}")
    before = len(ledger(env))
    again = sh(flow_cmd(t, "--resume", str(root)), env=env)
    tail = flow_events(root)
    tail = tail[max(i for i, e in enumerate(tail) if e["event"] == "resume"):]
    check(again.returncode == 0 and {e["job"] for e in tail if e["event"] == "reused"} == {"review/codex", "review/codex-sol"}
          and len(ledger(env)) - before == 3, f"改核实说明只重跑核实：{tail}")
    # 改评审说明：评审和它的下游全部重跑（即使评审结果一样）
    write_flow(t, "VERIFY-ME 请核实这条：{{item}}", review_prompt="REVIEW-ME 仔细评审 {{vars.target}}")
    before = len(ledger(env))
    again = sh(flow_cmd(t, "--resume", str(root)), env=env)
    check(again.returncode == 0 and len(ledger(env)) - before == 5, "改评审说明应重跑评审及下游")


def flow_spec_errors():
    t = Path(os.environ["HOME"]) / "f4"
    env = flow_setup(t)
    base = json.loads((t / "flow.json").read_text())
    def broken(mutate, needle):
        spec = json.loads(json.dumps(base))
        mutate(spec)
        (t / "bad.json").write_text(json.dumps(spec, ensure_ascii=False))
        p = sh([sys.executable, str(REPO / "flow.py"), "run", str(t / "bad.json")], env=env)
        check(p.returncode == 64 and needle in p.stderr, f"{needle}: rc={p.returncode} {p.stderr}")
    broken(lambda s: s.update(extra=1), "未知字段")
    broken(lambda s: s["steps"][0].update(foo=1), "未知字段")
    broken(lambda s: s["steps"][1].update({"from": "report"}), "不存在或在它后面")
    broken(lambda s: s["steps"][1].update(prompt="{{item.claim}}"), "不认识")
    broken(lambda s: s["steps"][0].update(prompt="{{item}}"), "不认识")
    broken(lambda s: s["steps"][1].pop("items"), "items")
    broken(lambda s: s["steps"][1].update(items="nope"), "没有字段 nope")
    broken(lambda s: s["steps"][1].pop("schema"), "必须有 schema")
    broken(lambda s: s["steps"][1].update(schema={"oneOf": []}), "不支持的关键字")
    broken(lambda s: s["steps"][0].update(seats=["nobody"]), "未知座位")
    broken(lambda s: s["steps"][0].update(seats=["write"]), "未知座位")
    broken(lambda s: s["steps"].append(dict(s["steps"][0])), "重复")
    broken(lambda s: s["steps"][2].update(prompt="x"), "程序汇总不写")
    check(not calls(t) and not ledger(env), "流程文件错误时不能派任何单")
    p = sh(flow_cmd(t), env=dict(env, AIW_NESTED="1"))
    check(p.returncode == 64 and "外包" in p.stderr, "执行方里不能起流水线")
    import fcntl
    root = Path(env["AIW_STATE"]) / "flows" / "busy"
    root.mkdir(parents=True)
    (root / "flow.json").write_text("{}")
    with (root / "lock").open("a") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        p = sh(flow_cmd(t, "--resume", str(root)), env=env)
    check(p.returncode == 75 and "正被另一个" in p.stderr and not calls(t), p.stderr)


CASES = {case.__name__: case for case in (
    structured_schema_units,
    structured_ok_and_json,
    structured_retry_then_ok,
    structured_retry_still_bad,
    structured_failure_passthrough,
    structured_panel,
    structured_forward,
    flow_streaming,
    flow_failure_branch,
    flow_resume,
    flow_spec_errors,
)}
