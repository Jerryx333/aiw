# 红线与守护测试

## 角色与单家安装

使用 `aiw write|review|draft <workdir> <timeout> <prompt>`；角色解析到哪个座位见 `aiw doctor`。

| 主会话身份 | write 候选顺序 | review 候选顺序 | draft 候选顺序 |
|---|---|---|---|
| Claude | codex → claude-write | codex-sol → claude | codex-sol → claude |
| Codex | 空（改文件自己做） | claude → codex-sol | codex-sol → claude |
| 未识别（default） | codex → claude-write | codex-sol → claude | codex-sol → claude |

按顺序取首个存在、bin 可找到（尊重 CODEX_BIN/CLAUDE_BIN）、未停用且没有当前 auth/not_installed 熔断的座位。全部不可用返回 rc=2，列出每个候选原因，提示接入 agent-use 文本座位；Codex 主会话 write 列表为空时提示「Codex 主会话改文件请自己做」。review/draft 强制只读，包括写能力的顶替座位。`roles` 本地覆盖对象递归合并、列表整体替换；review/draft 候选只能是只读 CLI 或 agent_use 座位（文本/workspace），角色名不能与座位或内置命令冲突。

只装 Codex 时，主会话自己改代码，review/draft 使用另一 Codex 会话。只装 Claude Code 时，write 使用 claude-write，review/draft 使用 claude。两家都装时 review 会落到另一家模型；只装一家时是同一家另开上下文，不等于跨家族复核。

claude 与 claude-write 均固定 `--model opus`，不跟随用户默认模型。claude-write 需要 Claude Code ≥ 2.1.259，写单使用 `--restricted --strict-mcp-config --tools Read,Grep,Glob,Edit,Write,Bash --permission-mode acceptEdits --permission-prompts none`，并传单个 settings JSON：`{"sandbox":{"enabled":true,"allowUnsandboxedCommands":false,"failIfUnavailable":true}}`。受限模式加沙箱，只改项目工作区文件，不能 git add/commit；沙箱不可用时失败。其只读 argv 沿用 claude 的 Read/Grep/Glob 白名单。

panel 默认名单去掉主会话自身后过滤不可用座位，stderr 列原因；不足两家时不派单、rc=1，提示「会诊需要至少两家可用座位；单家安装可接入 agent-use 文本座位」。显式座位名单同样过滤不可用座位并在 stderr 列原因；显式只给一家可用座位时照旧执行并展示结果、rc=1，全部不可用时不派单、rc=1。map_default 默认为 draft，也可配置角色；`--seat` 仍可点名座位。


运行 `python3 tests/mutation_check.py` 检查全部红线，或追加表内变异 id 只检查指定项；仅使用临时副本，先验证基线再逐条变异。修改红线相关代码之前和之后都必须运行，并运行 `python3 tests/test_harness.py` 完整回归。出现“补丁失效”时，核对实现后更新 `MUTATIONS` 的精确 old/new（old 必须唯一），保持只破坏对应红线，并同步本表及守护测试，不能把语法错误当作杀死。变异必须由预期的那条断言杀死才算数；无关失败不算通过。

| 不变量 | 代码位置（文件:函数） | 守护测试用例 | 变异 id |
| --- | --- | --- | --- |
| 凭据类附件拒发 | `ai_worker.py:sensitive` | `attach` | `credentials` |
| 附件不出 workdir | `ai_worker.py:build_attachments` | `attach` | `workdir` |
| AIW_NESTED 不再派单 | `ai_worker.py:dispatch` | `nested_dispatch` | `nested` |
| 写任务不顶替给只出文本的座位 | `ai_worker.py:dispatch` | `write_fallback_text_seat` | `write-fallback` |
| 写单跳过只读 workspace 顶替 | `ai_worker.py:dispatch` | `workspace_write_fallback_skips_ro_only` | `write-fallback-ro-only` |
| 混合名单保留附件范围提示 | `panel.py:main` | `workspace_panel_mixed_attachment_scope` | `panel-mixed-workspace-scope` |
| 全 workspace 名单省略附件范围提示 | `panel.py:main` | `workspace_panel_all_workspace_attachment_scope` | `panel-all-workspace-scope` |
| text 场景保留附件范围提示 | `panel.py:main` | `workspace_panel_text_attachment_scope` | `panel-text-workspace-scope` |
| 只出文本的座位不接写任务 | `ai_worker.py:dispatch` | `worker_regression` | `text-write` |
| Codex 专属行为按 adapter 而非座位名启用 | `ai_worker.py:codex_workspace_write` | `codex_adapter_alias` | `codex-adapter-name` |
| codex-sol 不顶替 | `seats.json:fallback` | `codex_sol_no_fallback` | `sol-fallback` |
| 超时不换座位 | `ai_worker.py:dispatch` | `worker_regression` | `timeout` |
| rc=0 且有输出优先判 ok，回答谈 quota/401 不熔断 | `ai_worker.py:classify` | `worker_regression` | `success-first` |
| 工具调用伪答复必须识别 | `ai_worker.py:looks_like_tool_calls` | `tool_call_detection` | `tool-calls` |
| goal verify 命令防篡改 | `goal.py:_verify` | `goal_verify_integrity` | `verify-sha` |
| goal 保险丝在执行前判定 | `goal.py:_verify` | `goal_fuse_preflight` | `goal-fuse` |
| 会诊不顶替 | `panel.py:ask` | `panel` | `panel-fallback` |
| 会诊至少两家成功且有产出，单家不能降低门槛 | `panel.py:main` | `panel_opinion_threshold` | `panel-opinion-threshold` |
| 写任务零变化必须提示 | `ai_worker.py:WorkChanges.__exit__` | `write_changes_report` | `write-zero` |
| CLI output.md 必须落盘 | `ai_worker.py:run_seat` | `run_dir_persist` | `output-file` |
| Codex 信任检查不能恒为通过 | `doctor_config.py:trust` | `doctor_config` | `codex-trust` |
| 运行中库锁识别为 locked，不熔断、不顶替 | `ai_worker.py:classify` | `codex_locked_runtime` | `locked` |
| inflight 字段符合状态文件约定 | `ai_worker.py:dispatch` | `test_state_contract` | `inflight-contract` |
| worker 持锁表明在途且锁不被子进程继承 | `ai_worker.py:acquire_liveness_lock` | `inflight_liveness_lock` | `inflight-lock` |
| 拿锁失败不留下假锁文件 | `ai_worker.py:acquire_liveness_lock` | `inflight_liveness_lock` | `inflight-lock-cleanup` |
| 子进程创建登记完成后才交付中断 | `ai_worker.py:run_group` | `signal_startup_window` | `startup-deferred` |
| aiw 强杀后看门狗清理独立执行方进程组 | `ai_worker.py:WATCHDOG_CODE` / `run_group` | `executor_watchdog` | `executor-watchdog` |
| 延迟信号不遮蔽关键区或 finally 清理区正在传播的真实异常，保留待处理信号；切换恢复嵌套深度与屏蔽字，首次信号决定退出码 | `ai_worker.py:deferred_signals` / `run_group` / `interruptible_signals` / `interrupted` | `signal_windows` | `deferred-exception` |
| finally 清理区不交付正在传播真实异常时的待处理信号 | `ai_worker.py:run_group` | `signal_windows` | `cleanup-deferred-exception` |
| 停用座位不能派单 | `ai_worker.py:dispatch` | `free_guard` | `disabled-dispatch` |
| agent-use transient 失败顶替且不熔断；未知 reason 不自动改派 | `ai_worker.py:dispatch` / `AGENT_USE_REASON` | `agent_use_transient` | `transient-fallback` |
| 已存在的 errand 名单必须严格校验 | `ai_worker.py:errand_names` | `errand_registry_corrupt_rejected` | `errand-registry` |
| errand 不进入 panel | `panel.py:main` | `errand_panel_rejects` | `errand-panel` |
| errand 不进入运行时 route | `route.py:route` | `errand_route_runtime_rejects` | `errand-route` |
| freecheck 不探测 errand | `freecheck.py:check` | `errand_freecheck_skips` | `errand-freecheck` |
| 支持及试修验证必须有非线索证据 | `triage.py:validate` | `triage_validate_core` | `triage-evidence` |
| 已验证必须有可比且改进的基线复验 | `triage.py:validate` | `triage_baseline_verified` | `triage-baseline` |
| unexplained 观察仍计入 open | `findings.py:recompute_count_cache` | `findings_local_cache_hint` | `findings-open` |
| 结构化结果不合格先同座位重交一次，不直接报错 | `structured.py:run` | `structured_retry_then_ok` | `schema-retry-once` |
| 结构化结果不合格的尝试在账本记 kind=format | `ai_worker.py:dispatch` | `structured_retry_then_ok` | `schema-format-ledger` |
| 流水线续跑复用钥匙相同且已完成的单，不重派 | `flow.py:Scheduler.add` | `flow_resume` | `flow-reuse` |
| 上游单钥匙进下游钥匙，上游说明改了下游必重跑 | `flow.py:Scheduler.add` | `flow_resume` | `flow-parent-key` |
| 转发必须携带本机解析的完整链 | `ai_worker.py:forward_dispatch` | `forward_resolved_chain` | `forward-resolved-chain` |
| 接收端不得展开自己的 fallback | `ai_worker.py:dispatch_chain` | `receiver_explicit_chain` | `receiver-chain-boundary` |

跑腿只能点名 `aiw errand`，不进入文本座位和顶替链；嵌套执行方不得调用。提交前说明会花额度，账户及私人数据仅限用户同意的该单。提交、动作放行、回复提问必须由用户亲手点击 macOS 弹窗；禁止 System Events、辅助功能、脚本代点或改代码绕过。派单记录包含 job_id、state、task_url、credits_used。弹窗及额度逻辑由 agent-use 实现，本仓库离线测试只验证转发和隔离。

workspace agent_use 写单须跳过非 workspace 文本顶替座位并说明；ro 禁编辑/命令，rw 全工具跳过确认、不加 sandbox-exec，MCP 不加载。默认文本行为保持不变，workspace 与 require_no_tools 冲突报错。离线用例见 `tests/cases/workspace.py`。
