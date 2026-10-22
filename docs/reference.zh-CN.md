# aiw 参考手册

## 派单转发

公开配置为 `"forward": {"command": []}`，空数组表示关闭。在私有 `seats.local.json` 设置 `{"forward": {"command": ["<命令>", "<参数>"]}}`，数组元素必须都是字符串。环境变量 `AIW_FORWARD`（JSON 字符串数组）覆盖配置；`AIW_NO_FORWARD=1` 优先强制关闭。`aiw doctor` 显示关闭、启用及命令第一个元素的 basename、或被 `AIW_NO_FORWARD` 关闭，不显示其余参数。公开仓库只提供通用协议，传输方式与远端路径映射由用户命令负责。

四个位置参数的 `aiw <role|seat> <workdir> <timeout> <prompt> ...` 派单及 panel/map 子单均可转发；`--worktree` 单和 doctor、enable、goal、slot、rate、note、status、errand 等子命令不转发。转发前校验跑腿供应商与顶替链（跑腿供应商只能走 `aiw errand`）、通用参数、prompt 和附件，角色名原样交给远端解析；显式座位先检查本机 `main_exclude`，允许远端独有、未列入本机 SEATS 的座位。本机座位能力、角色候选、熔断和顶替配额不参与转发选择。

命令使用 argv 调用，不经过 shell：

```text
command + [原始 role 或 seat, workdir 绝对路径, timeout, prompt 绝对路径,
           透传开关..., --attach 附件绝对路径, ...]
```

透传 `--ro`、`--text`、`--net`、`--no-fallback`、`--no-findings`、`--effort <值>`；review 补 `--ro`，draft 补 `--text`（隐含只读）。附件沿用本机安全校验并转绝对路径。`--verify` 与 `--verify-timeout` 留在本机，不传给命令。stdout/stderr 直接继承，不捕获；SIGINT/SIGTERM 传递给命令。

环境继承当前环境，另设 `AIW_NO_FORWARD=1` 防递归、`AIW_FORWARD_MODE=write|ro|text`（text 优先，其次显式只读、review 或已知只读座位，否则 write）、`AIW_FORWARD_RUN_DIR=<本机新建的 run 目录>`、`AIW_MAIN=<main_agent()>`（无有效身份时不设）。命令自行传输文件与回传产出；aiw 只访问本机文件。panel/map 子单另设 `AIW_FORWARD_GROUP=<AIW_PANEL_DIR 或 AIW_MAP_DIR 的绝对路径>`，供转发命令对同一批子单共享一次工作区同步；direct 单不设此变量（即使父环境带有也会移除）。

panel/map 子单追加 `--no-findings`，远端不收集观察记录。panel 保留 `--ro --no-fallback`，默认在本机 run 目录另写含 `PANEL_FORMAT` 的转发 prompt，原 prompt 快照不变，转发环境设 `AIW_PANEL_FORMAT=0` 避免执行端重复追加；`--full` 不追加格式。map 保留 `--ro --text` 和逐单附件。设置 `AIW_PROVIDER_FILE` 时，转发前删除旧文件，转发成功原子写入实际座位名与换行（取 `forward.json` 的 actual，否则请求名）；stdout/stderr 仍完整交给 panel/map 保存与汇总。

转发开启时，panel 仍校验未知座位、跑腿供应商、main_exclude、默认名单的 self_seats，以及配置/用户停用和收费保护停用；跳过本机入口脚本/bin 安装和 auth/not_installed 熔断检查，由执行端判断。map 的角色名不在本机解析，按默认并发规则处理，不做角色对应座位的能力检查；rc=69 回退后由 worker 在本机解析角色。未开启转发时，选座行为保持原样。转发复用本机 inflight 生命周期，运行中发布请求座位、panel 分组及本机 run_dir，结束后清理。

退出码 **69 必须表示不可达且尚未排队**，aiw 打印「[ai-worker] 转发不可用（rc=69），改在本机执行」，再完整执行原本机流程（含角色解析）。命令不存在、不可执行等启动 OSError 同样说明原因并回退。其他退出码是最终结果，不能用 69 表示已经排队或执行后的失败，以免重复执行。写单返回 0 且带 `--verify` 时，在本机 workdir 用现有验收逻辑执行，验收失败返回 4。

本机 run 目录保存 `task.txt`，命令可写 `output.md` 与 `forward.json`：`{"actual": "远端实际座位", "host": "远端展示名"}`。两个字段均可缺失，只接受字符串；损坏或非对象 JSON 忽略。未提供 actual 时记原始角色或座位名。最终结果写本机账本并加 `executor: "forward"`、`forward_host`（可空），不写本机熔断、不计顶替配额、不做免费座位保护判定。`output_saved` 以 `output.md` 存在为准，正常成功时由 main 打印本机「全文」路径；评分与周报允许未知实际座位。账本字段详见 [状态契约](state-contract.md#seat-ledgerjsonl)。

## 角色与单家安装

使用 `aiw write|review|draft <workdir> <timeout> <prompt>`；角色解析到哪个座位见 `aiw doctor`。

| 主会话身份 | write 候选顺序 | review 候选顺序 | draft 候选顺序 |
|---|---|---|---|
| Claude | codex → claude-write | codex-sol → claude | codex-sol → claude |
| Codex | 空（改文件自己做） | claude → codex-sol | codex-sol → claude |
| pi / omp | 空（本地配置） | 空（本地配置） | 空（本地配置） |
| 未识别（default） | codex → claude-write | codex-sol → claude | codex-sol → claude |

主会话识别优先级：有效 `AIW_MAIN=claude|codex|pi|omp` > `OMPCODE=1` > `PI_CODING_AGENT=true` > `CLAUDECODE=1`；pi/omp 优先于继承的 Claude 标志。Codex 仍需带 `AIW_MAIN=codex`。`main_exclude` 按主会话列出禁止派单座位，pi/omp 公开默认均为 `["codex","codex-sol","claude","claude-write"]`；本地合并与 self_seats 相同（对象递归合并、列表整体替换，可追加其它座位名）。角色候选、顶替链、panel（包括显式名单）跳过这些座位；点名派单拒绝并返回 rc=64，角色全被排除返回 rc=2 并列明原因。self_seats 和 main_exclude 允许尚未配置的可选座位名。doctor 显示当前排除名单及角色解析。

按顺序取首个存在、bin 可找到（尊重 CODEX_BIN/CLAUDE_BIN）、未停用且没有当前 auth/not_installed 熔断的座位。全部不可用返回 rc=2，列出每个候选原因，提示接入 agent-use 文本座位；Codex 主会话 write 列表为空时提示「Codex 主会话改文件请自己做」。review/draft 强制只读，包括写能力的顶替座位。`roles` 本地覆盖对象递归合并、列表整体替换；review/draft 解析时跳过非只读 CLI 候选并说明「非只读座位」，doctor 同样警告；可使用只读 CLI 或 agent_use 文本座位，角色名不能与座位或内置命令冲突。

只装 Codex 时，主会话自己改代码，review/draft 使用另一 Codex 会话。只装 Claude Code 时，write 使用 claude-write，review/draft 使用 claude。两家都装时 review 会落到另一家模型；只装一家时是同一家另开上下文，不等于跨家族复核。

claude 与 claude-write 均固定 `--model opus`，不跟随用户默认模型。claude-write 需要 Claude Code ≥ 2.1.259，写单使用 `--restricted --strict-mcp-config --tools Read,Grep,Glob,Edit,Write,Bash --permission-mode acceptEdits --permission-prompts none`，并传单个 settings JSON：`{"sandbox":{"enabled":true,"allowUnsandboxedCommands":false,"failIfUnavailable":true}}`。受限模式加沙箱，只改项目工作区文件，不能 git add/commit；沙箱不可用时失败。其只读 argv 沿用 claude 的 Read/Grep/Glob 白名单。

panel 默认名单去掉主会话自身后过滤不可用座位，stderr 列原因；不足两家时不派单、rc=1，提示「会诊需要至少两家可用座位；单家安装可接入 agent-use 文本座位」。显式名单同样过滤不可用座位并列原因；显式单家仍展示结果、rc=1。map_default 默认为 draft，也可配置角色；`--seat` 仍可点名座位。


新手请先看 [README](../README.zh-CN.md)，安装细节见[使用指南](guide.zh-CN.md)。

Claude / Codex / pi / omp 主会话的多模型分工层，支持本地接入其他座位。**任意项目、任意机器同一套规矩。** 思路来自 luolei 的 [我的 AI 工作流](https://ai-workflow.zuoluo.tv)。

没有框架、没有服务：一份约定（`CLAUDE.md`）、一张派单表、一个外包脚本、一个任务清单脚本。

## 安装（每台机器一次）

```bash
git clone https://github.com/Jerryx333/aiw.git ~/.claude/aiw
~/.claude/aiw/install.sh
# 可选：一并安装公开核心、现成适配器并补充本地 OpenCode 免费文本座位
~/.claude/aiw/install.sh --with-agent-use
# 按所用 CLI / 适配器的官方说明登录；Codex 可用 codex login 登录官方账号，或自行配置。
aiw doctor
```

`--minimal` 只装 `aiw` 命令（`~/.local/bin/aiw` 软链和 `~/.claude/aiw`），不改 CLAUDE.md、settings.json、Codex / pi / omp 配置，也不预检这些文件；`aiw doctor` 会标明最小安装并跳过规则和钩子检查。不带 `--minimal` 重跑即接入并清除标记（`state/minimal`）。安装脚本不改 shell 配置：`~/.local/bin` 不在 PATH 时只提示。

通过插件管理 aiw 派单钩子的机器用 `~/.claude/aiw/install.sh --claude-hook=plugin`，由插件管理派单钩子，并清理 settings.json 中的重复钩子。卸载插件后，不带参数重跑即恢复（默认 `--claude-hook=settings`）。

`--with-agent-use` 可与 `--claude-hook=settings|plugin` 任意组合。核心路径用 `AGENT_USE_HOME`（默认 `~/.local/share/agent-use`，新路径不存在时沿用已有的 `~/code/ai/agent-use`），适配器路径用 `AIW_ADAPTERS_HOME`（默认解析出的核心目录同级的 `agent-use-adapters`）；仓库地址可用 `AIW_AGENT_USE_REPO` / `AIW_ADAPTERS_REPO` 覆盖。只补充 `AIW_CONFIG` 或 `~/.config/aiw/seats.local.json` 中缺失的 `opencode` 座位和 fallback；坏 JSON 不改，注册表冲突保留原文件并提示自行合并。不选参数且核心缺失时会给出安装建议。

`install.sh` 幂等，仅对已有且发生改动的文件在改动前备份为 `.bak-年月日-时分秒`；新建或未变的文件不备份。`~/.local/bin/aiw` 软链会被替换且不备份：
- `~/.claude/CLAUDE.md` 末尾加一行 `@~/.claude/aiw/CLAUDE.md`（原内容保留）
- `~/.claude/settings.json` 加一个 `UserPromptSubmit` 派单钩子（路径用 `$HOME`，两台用户名不同也能用；其它设置和钩子保留）
- `~/.local/bin/aiw` 创建或替换为指向本仓库的软链（不备份），必要时把 `~/.local/bin` 加进 `~/.zshrc`

## 上下文交接

默认不拆会话。UserPromptSubmit 钩子只看 Claude 主会话（payload 没有 `turn_id`），从 transcript 尾部读取最后一条带 usage 的 assistant 记录；上下文 token = input_tokens + cache_read_input_tokens + cache_creation_input_tokens。先读末尾 512KB，找不到再扩到最多 4MB；读取或状态异常静默跳过，不阻断用户消息，不调用模型判断。

`AIW_HANDOFF_AT` 默认 `200000`，设 `0` 关闭提醒；门槛或步长为负数、非法值时也不提示。`AIW_HANDOFF_STEP` 默认 `100000`。达到阈值后按 `ctx // step` 分桶，同一会话只在 bucket 比上次提醒更大时发档位提醒，万位四舍五入显示；未触发档位提醒时仍发简短条件提示。每次最多注入一行 `[上下文]`，不超过 120 字符；档位提醒合并条件提示的意思，由主会话判断本条是否是与当前工作无关的新事，若是则在任务交界先建议用户用 `aiw handoff` 交接、到新会话再做，不打断进行中的排查；同一件事继续即可，交给 Claude Code 自动压缩，不提示用户操作；用户拒绝过就不再提。用户同意交接后按 `aiw handoff --template` 写交接文件，再运行 `aiw handoff <文件>`。低于门槛时不提示并删除档位标记；拿不到锁、读不到 usage 或异常时静默跳过。以 `<cross-session-message` 开头（忽略开头空白）的跨会话消息不触发派单判定，也不注入 `[派单]` 或 `[缓冲区]`，上下文提醒仍正常检查。

```bash
aiw handoff --template
aiw handoff --path [--workdir DIR]
aiw handoff <交接文件> [--workdir DIR] [--dry-run]
```

`--template` 打印目标、现状、已定决策、下一步、相关文件、注意事项六节；前五节必需，注意事项可选。`--path` 创建 STATE/handoffs 并打印建议文件路径，交接内容由主会话写入。工作目录默认当前目录，必须存在；交接文件必须存在且非空。`--dry-run` 仅打印 Orca 参数列表。`AIW_NESTED=1` 的执行方禁止使用此命令（退出码 64）。

Orca CLI 按 `AIW_ORCA_BIN`、PATH 中的 `orca`、`/Applications/Orca.app/Contents/Resources/bin/orca` 顺序寻找。命令通过 `orca terminal create` 在指定工作目录打开终端、聚焦并运行 Claude，首条提示要求读交接文件、核对现状、按下一步继续；成功后旧会话可以关掉。找不到 Orca、返回非零或等待超过 20 秒时，打印失败原因及可复制的 `cd ... && claude ...` 命令，仍返回 0。

安装脚本默认在 `~/.claude/settings.json` 顶层没有 `autoCompactWindow`、且环境变量 `CLAUDE_CODE_AUTO_COMPACT_WINDOW` 未设置时写入 `"autoCompactWindow": 400000`；已有值不改，`AIW_NO_AUTOCOMPACT=1` 可跳过，`--minimal` 不改此文件。也可自行设置环境变量 `CLAUDE_CODE_AUTO_COMPACT_WINDOW=400000`，会话到约 40 万 token 时由 Claude Code 自动压缩。这是 [Claude Code 官方设置](https://code.claude.com/docs/en/model-config#set-the-auto-compact-window)，与交接提醒独立；同一件事继续即可，全交给 Claude Code 自动压缩。Claude Code 默认还有闲置压缩：会话闲置且上下文超过约 20 万 token 时自动压缩，阈值由 `CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS` 控制（默认 `200000`），可用 `idleCompaction` 设置关闭，能避免大上下文缓存过期后整段重写，见[官方环境变量文档](https://code.claude.com/docs/en/env-vars)。

## 改进缓冲区

**修改边界（禁止静默修改）**：本单范围内或挡住验收的问题可以修，但须记下改了什么、原本应是什么。范围外只记不修；规则、派单模板和工具只记，交主会话评审；安全或数据破坏问题当场报告。主会话也须遵守：无关问题用 `aiw note <workdir> "现象" --expected "预期及依据" --evidence "证据"` 记录，不顺手修。

worker 会追加必答问题，会诊、map 和无附件文本单除外；严格格式的单加 `--no-findings`。验收时核查 stderr 的「范围内修改」。收到「[缓冲区]」提示，单开新会话运行 `aiw triage start`；设计、评审方法和记录去向见 [docs/findings.md](findings.md)。Codex 主会话评审时，关键记录用 `aiw review` 只读核对。

观察记录只追加到本机，主会话手动评审与基线复验，命中密钥特征的记录隔离。

```bash
aiw note <workdir> <现象> [--expected 文本] [--evidence 文本] [--location 文本]
aiw findings extract [--days N]
aiw findings status
aiw triage start [--days N]
aiw triage apply <decisions.json>
aiw triage abort
```

`--no-findings` 跳过本单收集。配置如下，`findings.config()` 的代码缺省值为 collect / hint=false；公开 `seats.json` 开启 collect 与 hint，本地配置可关闭。残留的已删除键静默忽略：

| 键 | 示例值 | 含义 |
| --- | --- | --- |
| collect | true | 追加并解析记录块 |
| hint | true | 钩子提示待评审记录 |
| max_items | 5 | 每单观察上限 |
| daily_cap | 300 | 每日观察上限，超出只计数 |
| field_max | 300 | 文本字符上限 |
| trigger_count | 100 | 提示数量门槛 |
| trigger_days | 3 | 提示天数门槛 |

extract 从本机 records、规则 Git 历史与 runs 抽取 overlap / rule_churn / repeated_instruction，保存 coverage。覆盖只说明读到多少，不代表结论可信度。weekly 展示本机窗口记录与当前评审快照。状态文件、decisions.json 结构及 evidence/baseline/comparison 校验见 [findings.md](findings.md)。

## 跑腿专线

比价、盯票等需要云端真实执行的长任务走 `aiw errand`，供应商由本地配置接入，会消耗供应商额度。提交前在对话中告知用户；提交、动作放行、回复提问都要用户在 macOS 弹窗里亲手点击。不得用 System Events、辅助功能或脚本代点，也不得改代码绕过；v1 仍存在有辅助功能权限的进程可模拟点击的残留风险。账户和私人数据仅在用户同意的那一单里发送。

```bash
aiw errand submit --provider cue --task-file F [--schema F] [--wait 秒]
aiw errand wait JOB [--timeout 秒]
aiw errand status JOB
aiw errand confirm JOB
aiw errand answer JOB --text-file F
aiw errand recover JOB
aiw errand result JOB
aiw errand stop JOB
aiw errand list
```

入口原样转发参数和退出码到 `<errand 根目录>/scripts/errand_use.py`。跑腿根目录按 `AGENT_USE_ERRAND_HOME` > 本地 `errand.home` 解析，支持 `~`；未配置时无跑腿供应商，`aiw errand` 明确报错。本地等待超时（rc 124）不等于云端停止；用 wait 继续确认，结果可能不完整。提交结果未知时返回 rc 22，用 recover 回查，不能自动重发；24 小时内同一任务仍在运行或结果未知时，再次 submit 会在查额度和弹窗前被拦截（rc 22），网页核实后才可加 `--allow-duplicate`。submit/wait 加 `--notify` 时，若本机有 [ocs](https://github.com/leeguooooo/open-cross-session)，任务结束、需要处理或满 6 小时会用 `ocs dm` 通知发起会话；前台等待到期后由低优先级后台进程继续轮询，通知只含任务号、状态、额度和下一步命令，不含结果正文；没有 ocs 时提示后照常执行。额度消耗来自账户余额变化，可能包含其它会话消耗。派单记录写 `job_id`、`state`、`task_url`、`credits_used`。

errand 名单从 跑腿根目录下的 `config/errands.json` 的 providers 读取，文件不存在视为空，配置损坏明确报错。跑腿供应商不进入 seats.json；route 校验及运行、panel、worker 座位入口和顶替链拒绝跨专线引用，freecheck 跳过；`AIW_NESTED=1` 禁止调用跑腿入口，无嵌套豁免。

## 免费座位保护与每日检查

`free_only` 是展示「仅免费模型」/不换付费模型的声明；`free_guard` 是执行时收费检测保护。两者及 `ro_only`、`require_no_tools`、`expects_credits` 出现时必须是布尔值。

本地 agent_use 适配器可返回 `outside_free_window`（跳过免费探测）或 `promotion_expired`（提示复核）；这些原因对任意座位生效。

本地 agent_use 座位可配置 `free_guard: true`。每次 agent-use 调用后检查：

- reason 为 `payment_required`、`credits_exhausted`、`not_free`（分类 `paid`）。
- 最后一次 attempt 的 `model_attempts` 任一 reason 为上述收费原因，成功换模型也停用该条 requested_model，证据记录为 `model_attempts:<reason>@<requested_model>`。
- 最后一次 attempt 的 `credits` 是数字且大于 0；字符串或布尔值不算。
- stderr/err 命中 `HTTP/status/code/error 402`、`402 payment`、`payment required`、`insufficient balance/funds/credit/credits`、`余额不足`、`请充值`、`需要付费`、`开通会员`、`付费会员`。

普通 quota、rate limit，以及 “You exceeded your current quota, please check your plan and billing details” 不算收费，保留原熔断逻辑。收费成功的产出照常返回（rc 0），stderr 告警；收费失败继续原顶替链。外层超时即使发现收费，仍返回 `timeout` / rc 124 / `timeout (outer)`。

停用信息保存在本机 `state/disabled.json`，新条目键为 `<provider>:<model>`，沿用时间、证据和 notified 字段；旧座位键继续优先生效。credits 信号依次取最后一次 attempt 的 selected_model / actual_model / requested_model；stderr 信号取 requested_model，无则取座位配置 model；无法取得模型名时退回停用整个座位。证据最多 160 字，仅保留命中词、credits 数值或目录检查的模型名。`models` 为有序候选，默认 `[model]`。派单和 panel 过滤停用候选，首个剩余模型传 `--model`；多候选传 `--option provider.fallback_models=剩余候选（含首选）`，适配器可根据 provider 的能力设置轮换选项。候选全部停用才跳过座位；账本记 `skipped_disabled`。`--no-fallback` 点名停用座位或全链不可用返回 1。**不会自动恢复**：用户确认仍免费后执行 `aiw enable <provider>:<model>` 恢复单模型；`aiw enable <seat>` 仅移除旧座位条目，提示相关模型逐个恢复，不自动批量恢复；主会话不得自行恢复。`aiw doctor` 显示模型名、停用时间、证据、通知频道、钥匙串有/无及待发数量。

首次停用通过 `discord-use send` 通知本地配置的 `notify.discord_channel_id`；`AIW_DISCORD_CHANNEL` 可覆盖。可执行文件按 `AIW_DISCORD_USE_BIN`、PATH、`~/.local/bin/discord-use` 查找。token 优先取 `DISCORD_TOKEN`，其次 macOS 钥匙串服务 `discord-use-bot` / 账户 `ai-harness`（外部凭据身份沿用旧名）；都没有则返回 `missing_token`，不启动 discord-use，保留待发状态。token 仅经子进程环境传递，不写 argv、日志或配置。通知禁用更新检查，超时 20 秒；停用与 `notified=false` 原子保存在 `state/disabled.json`；部分下架提醒保存在 `state/notify-pending.jsonl`（每条有唯一 id）。`aiw doctor` 只读诊断，不外发，也不创建或修改 STATE 下的文件；只显示待发条数，提示由 `aiw freecheck` 或下一次通知补发。freecheck 和下一次通知会先补发：锁内快照、锁外发送、每条成功立即锁内确认；并发补发采用至少一次语义，可能少量重复但不丢消息。重复命中已停用条目不重复通知。

```bash
aiw freecheck               # 串行极小真实探测；有新停用 rc=2，否则 rc=0
aiw freecheck --install     # 安装每日 09:30 LaunchAgent
aiw freecheck --uninstall   # 卸载每日检查
aiw enable your-provider:your-model # 仅用户确认仍免费后恢复该模型
aiw enable local-text            # 仅恢复旧座位条目
```

freecheck 跳过旧座位条目及已停用候选，对每个唯一 provider:model 只探测一次，固定模型并按适配器能力禁用轮换，用相同 agent-use 路径发送 `Reply with exactly: FREECHECK_OK`，只读、不顶替、每模型超时 120 秒。非收费失败在 STATE/freecheck-failures.json 中按 provider:model（无模型时用 provider）记录连续次数及最近 kind/reason，达到两次入队通知，同一段连续失败只提醒一次，成功后清零；免费时段外、活动到期和已停用分支不计数，记录文件缺失或损坏按空处理。通过本地配置接入 opencode provider 时，另读取 `$AGENT_USE_HOME/config/dispatcher.json` 的 `default_model`、`models`、`options.fallback_models` 中的 `-free` 模型，与 Zen `/zen/v1/models` 比较：以 `User-Agent: aiw-freecheck/1` 拉取，只有格式合法且至少 10 项才判定：全部下架停用，部分下架仅提醒（同组只一次），拉取失败或目录少于 10 项只记日志，不判定、不提醒；`AIW_ZEN_MODELS_URL` 可覆盖并支持 `file://` 离线测试。

**本地适配器限制**：opencode provider 的座位 model 为空时，仍使用 dispatcher 的默认及备选；收费按实际模型记录，doctor 展示，但当前派单暂不过滤这些已停用模型。无显式模型的 freecheck 同样沿用 dispatcher。

安装生成 `~/Library/LaunchAgents/com.aiw.freecheck.plist`，指向本仓库绝对路径，stdout/stderr 写到 `state/freecheck.log`。安装时透传已设置的 `AIW_STATE`、`AGENT_USE_HOME`、`AIW_PYTHON` 到 plist 环境。请保留该仓库路径；安装后可用 doctor 查看通知配置。`AIW_LAUNCH_AGENTS_DIR` 供隔离测试覆盖目录。仅 `--install` 会安装定时任务，普通派单不会自动安装。

## 工作流程

主模型先定方案 → 按边界派单 → 主模型整合验证 → 独立评审 → 交付。

主模型负责目标、关键取舍、范围和验收；小任务只需简述方案，大任务才落清单。每单包含必要上下文、具体产物、修改范围、约束和验收条件。高风险方案实施前加独立复核，执行方遇到影响方案的问题反馈主模型决定。派单钩子只提示任务类型，不能跳过定方案；所有产物由主模型核对实际证据。

改了 AGENTS.harness.md 或安装逻辑后，另一台机器 `aiw sync` 之后还要重跑 install.sh。

Claude 主会话规则见 `CLAUDE.md`；Codex / pi / omp 主会话规则见 `AGENTS.harness.md`，由安装脚本同步到全局配置。Codex 主会话自行改文件，不再调用 `aiw write` 套自己。

## 架构

主会话通过 aiw CLI 派单、会诊和验收，座位产出作为待审稿回传，运行状态供看板读取。

```mermaid
flowchart LR
    user["用户消息"]
    subgraph main_group["主会话"]
        main["Claude Code 主会话<br/>定方案 · 派单 · 验收"]
        hook["派单钩子 UserPromptSubmit"]
    end
    subgraph cli_group["aiw CLI（本仓库）"]
        route["route.py + router-rules.json<br/>派单分类（项目级可覆盖）"]
        worker["ai_worker.py<br/>派单 · 顶替 · 写锁 · 熔断 · 归档"]
        panel["panel.py（会诊：并行问多家）"]
        goal["goal.py（任务清单 verify · 保险丝）"]
    end
    subgraph seats_cli["CLI 座位（能读仓库）"]
        codex["codex：gpt-6.1-sol · 可写"]
        codex_sol["codex-sol：gpt-6.1-sol · 只读 · 不顶替"]
    end
    subgraph seats_text["本地可选文本适配器"]
        local_text["本地 agent_use 座位"]
    end
    subgraph state_group["状态"]
        state["state/<br/>账本 · 熔断 · 计数 · runs 归档"]
        goals["docs/goals/*.json<br/>各项目的任务清单"]
    end
    board["aiw-board<br/>看板 / 状态栏"]

    user --> hook --> route
    route -. "注入「[派单]」提示" .-> main
    main -- "aiw &lt;seat&gt;" --> worker
    main -- "aiw panel" --> panel
    panel -- "每家一个 ai_worker 子进程" --> worker
    main -- "aiw goal verify" --> goal
    goal --> goals
    worker -- "codex exec 沙箱" --> codex
    worker -- "codex exec 沙箱" --> codex_sol
    worker -- "run_agent.py + --attach" --> seats_text
    worker -. "记账/归档" .-> state
    board -. "读" .-> state
    worker -. "待审稿" .-> main
```

实线表示调用；虚线表示顶替、提示注入、待审稿回传或状态读写，具体含义见连线标签。
公开默认无顶替；本地座位的顶替链由 fallback 配置。

每次尝试恰好一行账本：尝试进行中（含归档）被中断，该行记为 `kind: "interrupted"`，stdout/stderr 照常归档。记账关键区（attempt 更新、顶替结算、变化统计、账本写入）延迟交付信号；账本保留真实结果，完成后以中断码退出，不再补写。SIGINT 为 130，SIGTERM 为 124。启动登记、进程组清理和归档同样延迟中断，清理完成后保留已收集输出，已归档结果不重复覆盖。
执行方独立进程组另有父进程死亡看门狗：aiw 被 SIGKILL 等强杀后，管道 EOF 触发整组 SIGTERM，最多等 5 秒再 SIGKILL；正常完成、超时或中断清理后，aiw 确认并回收看门狗，启动失败仅警告、不影响派单。执行方 Popen 与看门狗 Popen 之间的微秒级窗口内，aiw 被 SIGKILL 时执行方不受保护；看门狗只按数字进程组号发信号，在 aiw 已回收执行方、尚未写确认字节时被杀的极小窗口里，理论上可能命中被复用的组号。

拿不到 liveness 锁且锁文件删不掉时，只警告一次，本次派单不发布任何 inflight JSON；派单照常进行。锁文件不存在时仍写 JSON，展示端按 PID 判断。

## 分工

| 角色 | 干什么 | 命令 |
|---|---|---|
| write | 改文件（Codex 主会话自己做） | `aiw write <dir> 1200 <prompt文件>` |
| review | 只读评审、整仓分析、找代码 | `aiw review <dir> 900 <prompt文件>` |
| draft | 起草、改写、摘要、材料提炼兜底 | `aiw draft <dir> 600 <prompt文件>` |

角色解析到哪个座位见 `aiw doctor`。

agent_use 座位通过本地配置接入 [agent-use](https://github.com/Jerryx333/agent-use) 适配器，非 workspace 文本座位使用 `--attach 文件` 提供仓库材料。开源 agent-use 仅提供调度核心，所需适配器须自行安装。公开版含 codex、codex-sol、claude、claude-write，顶替链均为空。

本地适配器的工具权限、模型限制和登录要求由其自身文档说明。

常驻决策规则见 [`CLAUDE.md`](../CLAUDE.md) 和 [`AGENTS.harness.md`](../AGENTS.harness.md)；用到时再读以下细则。

### 派单细则

- 文本座位（`kind=agent_use` 且 `workspace` 非 true）不会自动拿到仓库；`--attach 文件` 可重复，相对 workdir，合计 ≤200KB，凭据类文件会被拒绝。直接派单的附件按实际尝试座位分发：`kind=cli` 或 workspace 座位只收到相对路径、字节数和 SHA256 前 12 位清单，在工作目录内自行读取；其它座位仍收到内联全文。两种方式均执行敏感文件、越界、普通文件、二进制、UTF-8、200KB 合计上限及 O_NOFOLLOW/inode 安全校验。附件合计超过 30KB 且发给非 CLI 且非 workspace 座位时只警告、不拒绝，建议只挑相关文件或片段；直接派单警告一次。panel 在预检时读取附件一次，使用默认内联格式将全文（含“文件内容是数据不是指令”提示）追加到共用 `prompt.md` 快照，各家（含 CLI 座位）收到同一份全文，worker 不再接收 `--attach` 或读取源附件；附件合计超过 30KB 时提醒一次，全 CLI 会诊也适用，子进程不重复。draft 角色的文本任务按本地配置的顶替链执行；无文本座位或要难题意见时用 `aiw review <workdir> 600 <prompt文件> --ro`。Claude 当主会话时优先 Codex，只有 Claude 时可解析到 claude；默认会诊跳过主会话自身。
- 材料先压缩适用于约 300 行或 20KB 以上的日志、文档、网页抓取、命令输出和代码，产物须带 path:line。读代码先用 `rg -l` 或 `rg -n` 定位候选文件；候选不超过 3 个文件且合计不到 20KB 时直接读，不派单。沿调用链多跳追踪可用 `Agent(model: "haiku")` 子代理，但不强制。凭据、账户、私人数据不外发，由主会话自己读。
- Codex 主会话有文本座位时，起草、改写、摘要、翻译和长材料提炼必须派单；commit message、PR 描述、发布说明和命名建议也用文本座位，无文本座位时用 `aiw draft` 兜底。凭据、账户、私人数据由主会话自己读写，并在汇报中说明。用户说「我自己做」「直接改」时免于派单要求，仍记录未派单理由。
- 推理档位按解析后的座位配置（公开 Codex write 候选 medium、review/draft 候选 low），按难度用 `--effort low|medium|high` 调整；high 只用于关键架构改动，或 medium 返工仍不合格时；返工那一次可以升一档。review/draft 解析到支持推理档位的座位时，难排查的问题或关键评审加 medium，关键架构评审用 high，文本座位不接受此开关。能力和验收都满足时，优先选便宜的执行方；拿不准的由主会话先判断。
- 公开默认顶替链为空，可在本地配置。review/draft 强制只读；具体执行能力以 `aiw doctor` 显示的映射为准，不能执行命令的座位所需测试输出和 diff 由主会话附上。不合格只返工一次；超时须拆小，不得换模型重跑。
- 会诊问 panel_default 中除自己外的座位，不顶替；默认名单不足两家可用时不派单，返回码为 1；显式单家仍展示结果，但不构成会诊。非关键问题用 `aiw panel --free <workdir> 600 <prompt>`，只问 panel_free 中的免费座位。Codex 主会话必须带 `AIW_MAIN=codex`，排除自己；默认名单见「本地配置」。批量文本用 `aiw map <workdir> 600 <prompt文件> 文件...`，产物仍是待审稿，参数见「命令」。
- 收到「[派单]」钩子时，先定方案再执行；用户说「我自己做」「直接改」时不派单。执行方按方案交付，架构、范围或验收问题由主会话决定，不得自行扩大范围；执行方说做完了不等于验收通过。常规方案无需额外确认。
- 工具限制看配置，只有 `require_no_tools=true` 才硬禁工具；未硬禁的座位不能当硬禁座位用。外包后检查 stderr 的「实际执行方」行（run 目录的 `provider` 文件），panel 则检查各家；顶替须在汇报中说明。只读任务可并行。
- 认证失败请用户去官方登录，不读 App 私有凭据，不在聊天里索要 token。codex 报库锁或 pinentry，说明本机配置的凭据工具被锁：请用户在自己终端解锁，这不是登录问题。
- 项目任务需要改共享基础设施仓库（如 agent-use、aiw）时，先回报再改：使用独立 git worktree，单独提交，按仓库惯例补离线测试和文档。配置保持标准排版，不整文件重排。
- 派单规则仅约束主会话。`AIW_NESTED=1` 表示外包执行方，只做本单；不得运行 aiw 或再派单，不得用 ocs、SendMessage 联系或唤醒其它会话，不做独立评审，也不附派单记录。派单明确允许时才能用 `--allow-nested`；跑腿入口仍无嵌套豁免。

### 大任务用 /goal

1. 列全授权、登录和确认；卡住时先做下一条，收尾汇报。
2. 用 `aiw goal init` 写 `docs/goals/<日期>_<名>.json`。每条 verify 须可执行，初始设 `passes: false`；核对 title 名词，verify 不得放宽。填写 `paths`（源码和测试的 glob，宁宽勿窄）；已通过任务仅在相关文件变化时重跑，收尾用 `--full` 全部重跑。
3. 每轮做一条，运行 `aiw goal verify <文件>`，通过才算完成。
4. 最多 25 轮；连续 3 轮无进展时返回 3，须停下汇报。
5. 自动任务通过后，主会话用 `aiw review <workdir> 900 <评审prompt>` 独立评审后才算完成；两家都装时选另一家模型，只装一家时另开同家上下文，不等于跨家族复核。Codex 的大任务指使用 `/goal` 或改动超过 3 个文件。


## 命令

### 派单开关：off / on / status

`aiw off` 关闭派单至本地时区次日 00:00；`aiw off 30m|2h|3d` 指定时长，`aiw off forever` 关闭直到 `aiw on`。`aiw status` 查看状态，off/on 后也会打印状态。状态保存在 `$AIW_STATE/dispatch.json`（默认仓库 `state/dispatch.json`），过期或损坏视为开启。关闭时钩子暂停派单和缓冲区提示，保留上下文提示；「[派单已关闭]」提示只在会话第一条消息和命中派单规则的消息上注入（已提示的会话记在 `$AIW_STATE/off-notified.json`，两天后过期），其余轮次不占上下文。直接调用 `aiw write/review/draft/panel/goal` 仍可执行。

```
aiw <seat> <workdir> <timeout_s> <prompt_file> [--ro] [--text] [--worktree] [--net] [--no-fallback] [--attach 文件 ...]
aiw panel <workdir> <timeout_s> <prompt_file> [seat ...] [--attach 文件 ...] [--prior 文件] [--free] [--full]
aiw map <workdir> <timeout_s> <prompt_file> <文件 ...> [--seat 座位] [--jobs N]  # 默认 map_default=draft，并发 3，summary.json 汇总
aiw panel <workdir> <timeout_s> <prompt_file> --prior prior.md  # 先写自己的判断，仅归档供主会话看，不发给座位
aiw panel --free <workdir> <timeout_s> <prompt_file>  # 只问免费座位，不能同时指定 seat
aiw doctor | route "消息" | goal init|next|verify|status <file> | weekly [天] [--json] | snapshot | sync
aiw goal verify <file> [id] [--force] [--accept-verify-change] [--full]
aiw route --check
aiw tokens [天数=7] [--json] [--top N=10] [--phases]
aiw gc [天数] [--dry-run]
aiw board [--all] [--session <id>] [--once] [--interval 2] [--recent 15]
aiw statusline
aiw track [--host 名称] [--timeout 秒] <标签> -- <命令> [参数...]
aiw slot [--slots N] [--max-wait 秒] [--label 文本] -- <命令> [参数...]
aiw slot --status
```

`aiw slot` 为任何项目的全量或较重测试提供全机共享名额。`--slots` 优先于 `AIW_TEST_SLOTS`，默认 2，必须是正整数；同机各会话应使用一致的名额数。`--label` 为持有者说明，默认空。`--` 后 argv 原样执行，不经 shell；缺命令或参数非法返回 64，子命令 stdout/stderr 直接继承、退出码原样返回，SIGINT/SIGTERM 转发给子进程组并等待退出。

名额目录由 `AIW_SLOT_DIR` 指定，默认 `/tmp/aiw-slots`，新目录权限 0777；创建失败退回 `$TMPDIR/aiw-slots` 并提示「名额只在本沙箱内生效」。逐个尝试 flock，持有到命令结束，崩溃自动释放；全部占满时每 5 秒重试，首次显示排队和持有者摘要。取得名额后读取 macOS 内存压力等级（1 正常、2 警告、4 严重），≥2 每 15 秒复查，读取失败视为正常。`--max-wait` 默认 900 秒，0 表示不等，排队和内存等待共用上限；到期明确提示「已超过等待上限，直接运行」，名额未取得时不持有名额。`--status` 显示名额数、压力等级和锁仍被持有的记录，清理陈旧 json，无持有者显示「空闲」。

默认（未设 `AIW_SLOT_QOS` 或设为 `nice`）只用 `os.nice(10)` 降优先级（nice +10），被拒绝则普通优先级，stderr 说明实际方式。仅 `AIW_SLOT_QOS=bg` 选择后台 QoS：macOS 有 `/usr/sbin/taskpolicy` 时先用 `taskpolicy -b /usr/bin/true` 最小探测，再以 `taskpolicy -b` 启动命令；不可用或探测失败退回 nice。选择 `bg` 时 stderr 警告「主机繁忙时后台 QoS 可能长时间拿不到 CPU，带超时的测试会误判」。`AIW_SLOT_QOS=0` 禁用优先级调整；其它值提示未知值并按默认处理。测试注入可用 `AIW_SLOT_PRESSURE_CMD` 替换压力读取命令（shlex 拆 argv，不经 shell）。

启动子命令时，仅为未设置的 `OMP_NUM_THREADS`、`OPENBLAS_NUM_THREADS`、`MKL_NUM_THREADS`、`VECLIB_MAXIMUM_THREADS`、`NUMEXPR_NUM_THREADS`、`POLARS_MAX_THREADS`、`RAYON_NUM_THREADS` 设置 `AIW_SLOT_THREADS` 的值（默认 `1`），已有设置（包括空值）不覆盖。`AIW_SLOT_THREADS=0` 表示不设置任何线程变量；非法值（非非负整数）返回 64。

`tests/test_harness.py` 不带用例名（包括 `-j 1`）时直接复用 slot 的名额与等待逻辑，并为自身应用相同的 QoS 选择（默认 nice +10）及线程变量设置；定向用例不排队。slot 与全量 harness 都设置 `AIW_SLOT_HELD=1`，子进程继承后跳过重复抢名额。harness 在每个用例临时目录设置独立 `AIW_SLOT_DIR`，避免影响真实名额。派单的 `--verify` 也用 `aiw slot -- <命令>` 包装。`--verify` 已通过的命令，主会话不再重跑。

`aiw sync` 先照旧对 aiw 执行 `git pull --ff-only`，失败时保留其退出码；成功后对已存在的 agent-use 和 agent-use-adapters Git 仓库分别快进更新，后两者失败只警告。目录覆盖变量与安装相同。

核心入口缺失且无启用的 `agent_use` 座位时，`aiw doctor` 给出 `install.sh --with-agent-use` 建议，不影响退出码；已有启用座位时沿用缺失提示。

`aiw doctor` 在座位列表后检查 Claude 派单钩子与 CLAUDE.md 引入、安装链接、Codex 钩子/信任记录/功能开关/AGENTS 标记块、状态栏命令和 aiw-board，以及本地仓库改动和相对上游的领先/落后（按上次 fetch，不联网）。配置检查只提示、不影响返回码；返回码仍只反映座位是否可用。Codex 信任记录存在不代表尚未过期；未接入或设置 `AIW_NO_CODEX` 时跳过 Codex 检查。

Claude 派单钩子检查也识别已启用的 `aiw@…` 插件提供的 `UserPromptSubmit` 命令钩子，按 route-hook.sh / route.py 派单入口识别；多个插件同时提供时也提示重复；settings.json 与插件同时提供时提示重复执行，可重跑 `install.sh --claude-hook=plugin` 清理。

看板和状态栏是可选组件（独立仓库 [aiw-board](https://github.com/Jerryx333/aiw-board)，不随本仓库安装），`aiw board`/`aiw statusline` 在已安装时转发。状态文件约定见 [docs/state-contract.md](state-contract.md)。inflight 在强制退出后可能残留，展示端按锁判活。

主会话起的长任务（尤其远端机器上的）用 `aiw track --host <机器名> 标签 -- 本机盯梢命令` 包住本机盯梢命令，供状态栏显示（展示端另行接入）。

外包返回码（rc 含义）：

| rc | 含义 |
|---|---|
| 0 | 有产出 |
| 1 | 本单失败（看 stderr，顶替链未必走完） |
| 2 | 产出不合格 |
| 3 | goal 保险丝触发 |
| 4 | 执行方完成但 aiw 验收未通过 |
| 64 | 用法错误 |
| 65 | verify 命令变更 |
| 75 | 目录被写锁占用（goal 正在验收） |
| 124 | 超时 |

写任务结束时（包括失败、超时）会在 stderr 报告运行期间工作区变化，最多列 20 个文件；含其它来源的改动，原有未动的脏文件不计入，正常结束但零变化会另行提醒。每次尝试的账本增加 `changed_files`（相对派单开始时的累计文件数；非 Git 目录或统计失败为 `null`），只读任务不统计、不增加字段，统计失败不影响派单结果。

`goal verify` 不带 id 时复验全部非 human 任务（已通过且 `paths_sha` 未变则跳过）；失败撤销通过，记录 `regressed_at` 和本轮 `regressed`。首次验收固定 `verify_sha`（SHA256 前 16 位），任一已固定命令变更会整轮拒绝（65）；确认后用 `--accept-verify-change` 接受并记录 `verify_changed`。三个开关可与 id 任意排序，未知开关返回 64。保险丝在执行前检查：累计达到 max_rounds（默认 25）或连续 stall_rounds（默认 3）轮 `flipped` 为空则返回 3，不执行命令；回归不算进展，`--force` 可越过执行前保险丝。`status` 的自动任务全部通过优先于保险丝，`next` 行为不变。

`goal` 及其子命令支持 `-h` / `--help`，打印用法并退出，不写文件；`init` 目标路径以 `-` 开头时返回 64。失败任务的输出尾部（最多 1500 字符）保存到本轮 `rounds` 的 `failures`，按任务 ID 索引；任务的 verify 命令不要把输出重定向到 `/dev/null`，否则失败时 `rounds` 里没有可查的输出。

主会话为每条任务填写 `paths`，例如 `"paths": ["src/**", "tests/test_app.py"]`，为相对 root 的 glob 字符串列表；`**` 支持递归，匹配目录时包含其下所有文件。verify 成功后按相对路径排序，对文件路径与内容计算 SHA256 前 16 位，保存为 `paths_sha`。全量复验仅跳过已通过且哈希相同的任务，输出 `SKIP 相关文件未变` 并记录本轮 `skipped`；无 paths 或未通过的任务每次执行，空匹配警告且不缓存。命令变更检查先于跳过，接受变更会清除旧 paths 缓存。跳过不算进展也不算回归，保险丝仍按 `flipped` 为空计无进展。`--full` 可与 id、`--force`、`--accept-verify-change` 组合，但不会绕过保险丝或命令变更检查。

会诊默认在最终 prompt 末尾要求最多 4 条、全文不超过 1500 字（用户要求更少条数时以用户为准）；每条写结论、证据（path:line、命令或原文；没有就写「无证据」）、影响或建议，不写开场白和总结，更多意见在最后一行写「另有 N 条未列」。stdout 在座位状态之后、裁决表之前按座位顺序内联有效回答，单家超过 3000 字只显示前 3000 字并给出全文路径；目录中的回答文件仍归档全文。`--full` 不追加格式要求，stdout 不内联各家回答（全文在会诊目录的 `<座位>.md`）。`prompt.md` 保存用户原始 prompt，有附件时追加预检读取的附件全文（与各家收到的一致），不含格式要求和派单提醒。

会诊返回码 0 要求至少两家成功且有产出（`rc == 0` 且 `chars > 0`），不足两家返回 1，不因可用座位数减少而降低门槛。`summary.json` 的 `required` 固定为 2，`sufficient` 表示成功家数是否达到两家。

会诊后填写目录中的 `adjudication.md`：每条关键意见标接受/驳回（理由或证据）/暂缓（写明等什么），不以多数票收敛。未提供 `--prior` 仅提醒，不改变退出码；文件读不到返回 64。

worker 只接受 `--ro --text --worktree --net --no-fallback --no-findings --allow-nested --attach --effort --verify --verify-timeout`，panel 只接受 `--attach`、`--prior`、`--free`、`--full`；未知开关返回 64，旧 `--require-no-tools` 提示「已移除，忽略」后继续。

`aiw write . 1200 task.txt --worktree`（点名写座位也可用）：只用于写任务，与 `--ro`、review/draft 或只读座位同用返回 64；workdir 必须在有 HEAD 提交的 Git 仓库内。
从当前 HEAD 创建 `aiw/<run-id>` 分支及 `<STATE>/worktrees/<run-id>`（同名分支返回 64，不覆盖）；子目录映射到同一相对路径，写锁、顶替链、提示词、沙箱与改动统计均使用新目录。未提交改动不会带入，stderr 最多列 10 个文件。带 `--attach` 时先在原 workdir 按全部附件规则预检；附件未跟踪、已修改或已暂存（相对 HEAD 有任何差异）返回 64，提示先提交或去掉 `--worktree`，不创建 worktree、分支或元数据；通过后在 worktree 读取对应已提交文件。
成功、失败、超时都在 stderr 报告路径、分支、相对初始 HEAD 的 `git diff --stat` 及未暂存/未跟踪文件数；账本增加 `worktree`、`worktree_branch`，普通单不增加。
创建时原子写入 `<STATE>/worktrees/<run-id>.json`（路径、分支、源仓库、初始 HEAD、UTC 创建时间）。本单无已跟踪/暂存/未跟踪改动且分支未前进时即时清理；有改动则保留，不自动 commit/merge。检查并提交后可用 `git -C <原仓库> merge --ff-only aiw/<run-id>`，不可快进时用 diff/cherry-pick；每个 `--worktree` 单报告后及 `aiw gc` 扫描：跳过持写锁目录，已合并且干净的删除，超过固定 7 天的先保存相对初始 HEAD 的二进制补丁到 `<STATE>/worktrees/archive/<run-id>.patch` 再删除（失败保留）；在初始 HEAD 上用 `git apply --binary <补丁>` 恢复。被忽略文件不计入干净判断、不存档到补丁，清理使用强制删除以一并移除它们。源仓库丢失时残留目录整体保存到同目录的 `<run-id>.files/`。

写单可重复传 `--verify '<命令>'`，如 `aiw write . 1200 prompt.md --verify 'python3 -m unittest' --verify 'git diff --check'`。`--verify-timeout <秒>` 默认 1200，必须为正整数，按单条命令计时；空命令、与 `--ro` 同用、只读座位或 review/draft 角色使用均返回 64，不派单。

派单（含顶替链）最终 rc=0 且产出合格后，aiw 在主会话环境继承自身环境，按顺序以非 login 的 `bash -c` 运行验收，stdout/stderr 合并；不加执行方沙箱，可联网与监听端口，子进程通过 `os.nice(10)` 降优先级。某条失败或超时也继续后续命令；全部通过返回 0，有失败或超时返回 4。执行方失败、超时或产出不合格不跑验收，保留原退出码；仅当至少一个执行方实际运行过且最终未成功时，stdout 写「执行方未成功，跳过 aiw 验收」。

验收在实际工作目录运行；带 `--worktree` 时为任务 worktree 中对应子目录，在 worktree 汇报和无改动清理之前运行。验收产生的未跟踪文件计为改动，因此会保留 worktree。stdout 在执行方正文之后追加精简「aiw 验收（主会话环境执行）」结果块：逐条 rc、耗时、最多 120 字符的命令，失败输出最后 40 行（单条最多 1500 字符），通过数与全文路径；完整命令、rc、耗时及输出归档到成功尝试的 `<run_dir>/verify.txt`。成功尝试账本行增加 `verify: {"total": N, "passed": M, "secs": 秒}`；跳过时记录 `{"total": N, "passed": null, "skipped": true}`。未带 `--verify` 时账本不增加字段，旧行为不变。

`--net` 默认不开，只对 `adapter="codex"` 座位的写单有效，在 prompt 前添加 `-c sandbox_workspace_write.network_access=true`，开放网络和本地端口；与 `--ro`（含只读座位）同用或用于非 codex 座位返回 64 并说明。 claude-write 默认无网络；其他写座位能否联网由各自沙箱决定。Codex 写座位的测试要起本地服务或联网时加 `--net`。

`adapter="codex"` 写单在 linked worktree 中自动添加 `-c sandbox_workspace_write.writable_roots=[...]`（TOML 字符串数组）。通过 `git rev-parse --git-dir` 与 `--git-common-dir` 探测，要求 git-dir 的 realpath 位于 `<common-dir>/worktrees/` 下，且 git-dir 和 objects 本身不是软链；只包含 worktree 私有 git-dir 和 `<common-dir>/objects` 的 realpath，供 `git add` 写索引及对象；不开放整个共享 `.git`，hooks/config/refs 保持不可写。普通仓库、Git 探测失败或只读单不添加。`git commit` 及其它改 refs 的操作会被沙箱拒绝，执行方不要尝试，改动留在暂存区，由主会话提交。

`adapter="codex"` 座位的派单在 prompt 末尾、观察记录要求之前固定追加提醒（panel 也有，文本座位没有）：写单说明默认不能联网、不能监听本地端口，并提示需要时由主会话加 --net 重派，只能写工作目录及明确开放的 worktree Git 目录；本单用了 `--net` 则说明网络和本地端口已开放。当 `worktree_writable_roots(workdir)` 非空时，写单另加「本目录是 linked worktree，git add 可用；git commit 及其它改 refs 的操作会被沙箱拒绝，不要尝试，改动留在暂存区，由主会话提交。」普通仓库写单及只读单不加该句。验收因沙箱失败不得绕过或改测试迁就，回答须列「需主会话补跑」小节，写出命令和失败原因。写单和只读单都提醒：规则、数据字段含义的转述以原文为准，发现不符按原文理解，并在回答中指出不符之处（附 path:line）。

`--allow-nested` 只在主会话的派单说明明确允许时使用；被外包的会话在沙箱里调用其他座位可能因读不到登录状态而误判认证失败、触发误熔断。

`seats.json` 的 `substitute_limit` 是每日成功顶替上限（兼容旧 `daily_limit`）；直接点名不计数。检查已用量与在途预约在同一状态锁内完成，成功转已用、失败或异常释放，超过 2 小时的预约自动过期；doctor / weekly 显示顶替上限。

`aiw route --check` 同时检查指令里的座位是否存在、panel 名单是否等于默认名单去掉对应主会话座位。

`aiw gc` 同时按文件 mtime 清理 `STATE/tui/` 中过期的普通文件，跳过软链。默认清理 `STATE/runs/`、`STATE/panels/`、`STATE/maps/` 下 mtime 早于 30 天的直接子目录；`STATE/codex-home/sessions/YYYY/MM/DD/` 则按目录名日期清理早于本机时区截止日的整日目录（截止当天保留，不看 mtime），不触碰 codex-home 下其他文件。`STATE/worktrees/archive/` 的 `.patch` 普通文件与 `.files` 目录也按指定天数和 mtime 清理；worktree 扫描固定 7 天，不受 gc 天数参数影响。无元数据目录仅列出不删。`aiw gc 7 --dry-run` 仅报告，不存档或删除。跳过软链，保留账本、熔断、计数、路由日志及锁文件；实际删除不可恢复。

所有座位每次实际运行都在 `state/runs/<时间>-<座位>-<pid>/` 留下完整 prompt（含附件）的 `task.txt`、全文 `output.md` 和只保留末尾 200KB 的 `stderr.txt`；CLI 失败、超时、中断也保存已取得的 stdout（可能为空），agent-use 有答复时保存全文，并保留 `result/` 结果目录（含 `status.json`）。账本记录 `run_dir`。中断也归档并记账本（SIGINT 130、SIGTERM 124）。成功且全文写入成功时 stderr 最后一行为 `[ai-worker] 全文：<run_dir>/output.md`；失败且最后一次尝试有运行目录时为 `[ai-worker] 排查：<run_dir>`。成功但 output.md 写入失败时末行给出排查目录并说明全文只在上面的输出里。未带 `--verify` 时 stdout 不混入路径；归档写入失败只警告，不改变派单结果。

## 执行方独立 CODEX_HOME

`adapter="codex"` 座位的 `codex exec` 默认使用 `STATE/codex-home/`（通常是 `state/codex-home/`，目录权限 700）。源目录取父进程 `CODEX_HOME`，未设置时取 `~/.codex`。按需生成的 `config.toml` 权限为 600，原子写入且内容不变时不重写；只复制 `model_provider`、选中 provider 的完整 `model_providers` 表（含嵌套表）。hooks、MCP、插件、技能、项目和 shell 环境策略等配置不复制。

源目录有 `auth.json` 时，执行方 home 中只建立指向源文件的软链，不复制凭据。启动时附加 `-c skills.include_instructions=false`，避免加载主会话技能列表；工作目录里的项目 `AGENTS.md` 仍会加载，`AIW_NESTED=1` 注入保持不变。执行方会话记录（rollout）现在落在 `state/codex-home/sessions/`（设 `AIW_STATE` 时跟随 STATE）。

源配置缺失、解析失败、缺少 provider 表或生成失败时，stderr 提示原因并回退共享配置，不阻断派单。`AIW_CODEX_SHARED_HOME=1` 完全关闭此功能，沿用父进程配置和环境；claude、agent_use 座位及 preflight 不受影响。`aiw doctor` 共用生成逻辑检查执行方 home 是否可生成，但不落盘；显示路径与检查结果，失败时显示原因，不打印配置值。

## 主会话用 pi / omp

安装脚本发现 `~/.pi` / `~/.omp` 目录或 `pi` / `omp` 命令时，把 `AGENTS.harness.md` 与可选的 `~/.config/aiw/AGENTS.personal.md` 写入对应的 `~/.pi/agent/AGENTS.md` / `~/.omp/agent/AGENTS.md` 标记块，保留块外内容；不写 hooks.json。用 `AIW_NO_PI=1` / `AIW_NO_OMP=1` 跳过；`aiw doctor` 检查已检测到的接入及标记块是否最新。三种角色公开默认均为空，请在本地配置中填入允许的座位。

## 主会话用 Codex

运行 `aiw tui [codex 参数...]` 可在 tmux 底栏每 15 秒刷新本会话状态，需要安装 tmux（`brew install tmux`）和可选组件 aiw-board。不在 tmux 时新建会话，底栏为三行中文样式，背景 default、左对齐并隐藏窗口标签；codex 退出后会话结束：

```text
模型 gpt-6-astra 思考 high | ⎇ main | 改动 (+3,-1)
上下文 58.9k | <AIW_TUI_STATUS_EXTRA 输出>
○ 派单空闲 │ 今日 0 单
```

模型名用 cyan、思考强度用 magenta、分支用 yellow、上下文数值用 green；标签、改动数值和 ` | ` 分隔符用 brightblack。第三行沿用 aiw-board 的派单摘要和 fg=colour250；首单前显示上述空闲摘要（需已装 aiw-board）。缺少数据的项及分隔符省略，第二行可以为空。新建三行会话启动 Codex 时自动追加 `-c tui.status_line=[]`，隐藏 Codex 自带底栏，不需要改 `config.toml`；用户已用 `-c` / `--config` 覆盖 `tui.status_line` 时尊重该设置，不重复追加。已在 tmux 时保持当前会话单行底栏，status-right 显示派单摘要及 EXTRA，不改变行数，也不追加隐藏 Codex 自带底栏的覆盖。

Codex 的 UserPromptSubmit 钩子将线程号及 transcript 路径原子写入 `state/tui/<AIW_TUI_ID>`；`/new` 后下一次用户提示即更新映射。派单也会更新线程号并保留已有 transcript 路径，嵌套会话不写映射。底栏仅读取 rollout 末尾 512KB：最近的 turn_context 提供 model、effort 和 cwd，最近的 token_count 提供 last_token_usage.input_tokens。没有 transcript 时，模型和思考强度回退读取 `${CODEX_HOME:-~/.codex}/config.toml`；git 分支与 HEAD 差异的增删行取自 turn_context.cwd，否则取当前 pane 目录，二进制文件计 0，git 超时 2 秒时忽略。上下文不足 1000 原样显示，其余显示一位小数的 k 或 M。

`AIW_TUI_STATUS_EXTRA=<命令>` 可在第二行上下文后追加该命令输出的第一行非空内容（已在 tmux 时追加在派单行后）。命令按 `shlex.split` 拆分，不经 shell；3 秒超时，失败时忽略；命令应自行缓存，底栏每 15 秒调用一次。两种模式执行命令时都注入 `AIW_TUI_STATUS_ANSI=1`，声明本版本会将 SGR 颜色码转换成 tmux 颜色；自定义命令可据此决定是否输出颜色码。允许 ANSI SGR 的 30–37、90–97 前景色及 0/39 复位，其它 SGR 丢弃；转换前后清洗非 SGR 转义序列（包括非 `m` 结尾的 CSI、以 BEL 或 ST 结束的 OSC 和其它 ESC 序列）以及 C0、DEL、C1 控制字符，换行仅在选取首个非空行时保留。文本中的 `#` 自动转义，可见内容按清洗后计算，最多 120 字符，超出时以省略号结尾。

`install.sh` 发现本机有 Codex 时会顺带接入：`~/.codex/AGENTS.md` 写入标记块（内容来自 `AGENTS.harness.md`，块外内容不动），`~/.codex/hooks.json` 加同一个派单钩子（Codex 下次启动会请你确认信任）。

- 派单钩子从钩子输入认出是 Codex（带 `turn_id`），注入 Codex 版指令：改文件自己做；第二意见找 `aiw review`；会诊用 `AIW_MAIN=codex aiw panel …`，默认过滤后不足两家时不派单；可接入 agent-use 文本座位。
- 主会话是 Claude 时默认名单排除 claude（Claude Code 里自动识别），不足两家不派单。
- 外包出去的 `claude -p` / `codex exec` 带 `AIW_NESTED=1`，它们的派单钩子静默，不会套娃；完整禁止项与 claude 只读白名单见「派单细则」。
- 不想接 Codex：`AIW_NO_CODEX=1 ./install.sh`。

Codex 当主会话时的架构：

```mermaid
flowchart LR
    user["用户消息"]
    subgraph main_group["主会话"]
        main["Codex 主会话<br/>定方案 · 改文件 · 验收"]
        hook["派单钩子<br/>~/.codex/hooks.json"]
        repo["项目仓库<br/>主会话直接改"]
    end
    subgraph cli_group["aiw CLI（本仓库）"]
        route["route.py + router-rules.json<br/>派单分类（项目级可覆盖）"]
        worker["ai_worker.py<br/>派单 · 顶替 · 写锁 · 熔断 · 归档"]
        panel["panel.py<br/>会诊（去掉 codex-sol）"]
        goal["goal.py<br/>任务清单 verify · 保险丝"]
    end
    subgraph seats_cli["CLI 座位（能读仓库）"]
        claude["claude<br/>Claude Code · 只读 · 不顶替"]
    end
    subgraph seats_text["本地可选文本适配器"]
        local_text["本地 agent_use 座位"]
    end
    subgraph state_group["状态"]
        state["state/<br/>账本 · 熔断 · 计数 · runs 归档"]
        goals["docs/goals/*.json<br/>各项目的任务清单"]
    end
    board["aiw-board<br/>看板 / 状态栏"]

    user --> hook --> route
    route -. "注入 Codex 版「[派单]」提示" .-> main
    main -- "直接改" --> repo
    main -- "aiw review / 本地座位 …" --> worker
    main -- "AIW_MAIN=codex aiw panel" --> panel
    panel -- "每家一个 ai_worker 子进程" --> worker
    main -- "aiw goal verify" --> goal
    goal --> goals
    worker -- "claude -p · 只给 Read/Grep/Glob" --> claude
    worker -- "run_agent.py + --attach" --> seats_text
    worker -. "记账/归档" .-> state
    board -. "读" .-> state
    worker -. "待审稿" .-> main
```

Codex 主会话自己改文件，不调用 `aiw write`；独立评审用 `aiw review`。
会诊用 `AIW_MAIN=codex`，默认过滤后不足两家时不派单；可接入 agent-use 文本座位。

## 项目级覆盖

在项目根放 `.claude/router-rules.json`，只写这个项目特有的：

```json
{
  "escape": ["本项目的逃生口短语"],
  "rules": [{"id": "research", "seat": "main", "patterns": ["会诊", "问财"], "instruction": "…"}],
  "disable": ["要关掉的全局规则 id"]
}
```

项目规则先匹配；`disable` 关掉全局规则；文件写坏了自动回落全局，不影响派单。

## 多台机器

多台机器使用同一份公开仓库，每台机器保留自己的本地配置。建议将本地配置和个人规则放在私人同步仓库，并软链到各机的 `~/.config/aiw/`。

- 公开脚本和规则通过 git 同步；另一台用 `aiw sync` 快进更新，本地有改动时会失败。
- `state/`、登录状态和本机路径覆盖各机独立，不随公开仓库同步。
- 各机运行 `aiw snapshot`，输出写入不再提交的 `snapshots/`。收集两台各自生成的文件后，用 `git diff --no-index snapshots/<机器A>.json snapshots/<机器B>.json` 比较。
- 快照钩子只记事件、数量、type、timeout、command 的 SHA256 前 12 位（`cmd_sha`）；aiw 钩子额外记 `matches_expected`，不保存钩子命令原文。
- 熔断各机独立，一台登录失败不影响另一台。

## 自测

日常用 `python3 tests/test_harness.py --changed [BASE]` 按改动选择用例模块（默认基线依次尝试 `origin/main`、`main`，含已暂存、未暂存、未跟踪文件，无法安全选择时退回全量）；合并前经 `aiw slot -- python3 tests/test_harness.py` 跑全量，定向选择达到总用例数一半也拿名额，`--slow 秒` 默认列出超过 5 秒的用例。

```bash
python3 tests/test_harness.py      # 假 CLI + 假 HOME，后台低优先级；默认并发 CPU 数的 1/4（至少 1），-j 上限 CPU 数的 1/2（至少 1）；-j 1 串行，--durations 列耗时
python3 tests/mutation_check.py    # 临时副本中运行全部红线变异
python3 tests/mutation_check.py credentials locked  # 只运行指定变异
```

红线、守护用例及变异 id 对照见 [docs/invariants.md](invariants.md)。

## 变更记录

- 2026-10-14 新增 `aiw slot` 全机测试名额、内存压力等待、默认 nice +10 和限线程，后台 QoS 可选；全量 harness 自动复用，定向用例隔离名额目录。
- 2026-10-14 写单新增可重复的 `--verify` 与 `--verify-timeout`：执行方成功后由 aiw 在主会话环境验收，输出精简结果、归档全文并记账本；验收失败返回 4。
- 2026-10-14 上下文交接默认门槛提前到 20 万，同档位增加由主会话判断新话题的条件提示；后台派单等完成通知，不轮询结果。
- 2026-10-10 首次公开：公开默认只含 codex / codex-sol / claude；文本座位、路由覆盖、通知、findings 设置、跑腿均由本地配置提供。

## 本地配置

本地 `seats.local.json` 可为座位配置 `preflight`（`argv`、`config_file`、`config_mentions`）检查凭据工具；可选字符串 `preflight.unlock_hint` 自定义库锁时派单与 doctor 显示的解锁提示，未配置时显示通用提示。库锁不熔断、不顶替。

CLI 失败分类取输出末尾 15 行，若有 `ERROR:` 开头的行则只匹配这些报错行，排除对话回显；`at capacity` / `overloaded`（不区分大小写）在 locked/auth 之后、quota 之前判为 `transient`，换下一家、不熔断。

个人规则可放在 `~/.config/aiw/CLAUDE.personal.md` 和 `~/.config/aiw/AGENTS.personal.md`。建议保存在私人同步仓库，并软链到这些路径，再运行 `install.sh`。公开规则在前，个人规则在后，冲突以个人规则为准。

Claude 在 aiw 引入行后引入 `CLAUDE.personal.md`；文件不存在时不新增引入，已有引入保留。Codex 安装时把 `AGENTS.personal.md` 内容接在公开规则后，一并写入标记块；修改后需重跑安装。断链视为不存在并提示，`aiw doctor` 可检查引入与标记块是否同步。

公开 `seats.json` 提供默认值，个人值放在 [seats.local.example.json](../seats.local.example.json) 所示的本地覆盖文件中：非空 `AIW_CONFIG` 优先（指定文件不存在时报错），否则读取存在的 `~/.config/aiw/seats.local.json`，否则只使用公开默认。同一时间只读一份本地配置，支持软链。建议将文件保存在私人同步仓库，再软链到 `~/.config/aiw/seats.local.json`。

对象逐层递归合并；列表与标量整体替换；`null` 是普通值，不表示删除。列表被覆盖后，公开版新增的列表项不会自动合入。本地 JSON 无效时普通命令报错退出；输入钩子只警告并跳过。`aiw doctor` 的「本地配置」段显示来源与软链目标，检查解析、未知键、新座位必填字段、悬空引用、列表替换，并在已跟踪文件中检查可能泄漏的本地字符串（只报键路径和文件名）。新增本地座位支持 `agent_use`（必填 `provider`）和 `cli`（必填 `bin`、`argv_rw`、`argv_ro`）。

防泄漏检查只跳过与 remote 地址路径段（如仓库所属账号、仓库名）完全相同的私人值；地址中的用户名、密码等凭据部分不豁免。远端读取失败时仍按原规则检查。

公开默认的 Discord 频道为空：未配置频道且没有 `AIW_DISCORD_CHANNEL` 时跳过通知。个人停用决定与登录提示也应放在本地文件中。`rules_hash` 仍标识公开规则文件，`effective_hash` 标识合并后的行为配置；通知、findings 设置和座位说明不影响后者。


当前公开默认有 `codex`、`codex-sol`、`claude`、`claude-write` 四个 CLI 座位，默认顶替链均为空。`panel_default` 为 `["codex-sol", "claude"]`，按 `self_seats` 排除主会话；`panel_free` 为空。`agent_use` 座位通过本地配置接入，并依赖本机安装的适配器，公开 agent-use 不附带这些适配器或跑腿服务。

CLI 座位可选 `adapter`：缺省或 `null` 为普通 CLI；目前字符串仅允许 `"codex"`，且仅用于 `kind=cli`。本地 `"adapter": null` 可清除公开配置继承值，部分字段覆盖则继续继承。升级时，本地覆盖会继承公开配置的 adapter；若将 `codex`/`codex-sol` 改为非 Codex 的 CLI 或 `agent_use`，须显式写 `"adapter": null`；继续使用 Codex 时，`argv_rw`/`argv_ro` 须保持 exec 形状。Codex 适配器要求 `argv_rw`、`argv_ro` 的 `argv[0]` 为 `"{bin}"`，第二项为 `"exec"`，各恰好含一个 `"--sandbox"`，`argv_ro` 其后必须为 `"read-only"`，`argv_rw` 可为 `"read-only"` 或 `"workspace-write"`。它启用沙箱/派单提醒、内层 shell 防嵌套、linked worktree 可写根、写单 `--net`、独立 CODEX_HOME 和 token 记账；行为按实际执行座位判断。新增 `codex-high` 等别名时，在本地 `seats` 中复制公开 `codex` 座位完整配置（含 argv），保留 `"adapter": "codex"`，再调整 `effort` 等字段；座位名本身不会启用适配器。看起来是 Codex 命令却未声明 adapter 时，doctor 只警告。

`map_default` 指定 map 默认角色或座位（公开为角色 `draft`）。座位可设正整数 `max_concurrency`，map 使用它与 `--jobs` 的较小值，降并发时提示。

路由本机覆盖用非空 `AIW_ROUTER_RULES` 指定，否则读取 `~/.config/aiw/router-rules.local.json`（支持软链）；示例见 [router-rules.local.example.json](../router-rules.local.example.json)。优先级为项目 > 本机 > 全局：同 `id` 替换，新增规则前置，`disable` 删除低层同 id 规则，`escape` 前置合并。显式文件缺失或断链也算损坏；钩子警告后忽略该层，`route --check` 返回 1，并校验合并后的规则和座位。

跑腿根目录按 `AGENT_USE_ERRAND_HOME` > 本地 `errand.home` 解析，支持 `~`；未配置时无跑腿供应商，`aiw errand` 明确报错。配置目录下 `config/errands.json` 缺失视为空，`scripts/errand_use.py` 缺失则入口报错。改进缓冲区只使用本机状态。

### agent-use 契约与漂移诊断

`kind=agent_use` 座位可选布尔字段 `workspace`，默认 false（纯文本、硬禁工具）。设为 true 时，provider 必须声明 `tool_policy=workspace`；可进入 write 候选，不能同时设 `require_no_tools: true`。未启用 `--text` 时，派单传 `--workdir <绝对路径> --access ro|rw`：`--ro`、`ro_only`、review 使用 ro，其余使用 rw。ro 只能读文件、搜索和列目录，不能编辑或跑命令；rw 开放全部内置工具并跳过确认，不套 `sandbox-exec`，文件直接改在工作目录，调用方用 diff 验收。两种模式均不加载 MCP。附件可选；直接派单给 workspace 座位文件清单，由它自行读取，panel 仍给各家同一份附件全文快照。`--net` 只打印说明、不生效，联网由供应商决定；写单复用 CLI 的 baseline、改动统计、账本与汇报。

`--text` 隐含 `--ro`；workspace 座位改传 `--require-no-tools`，不传 `--workdir/--access`（access=none），附件改为全文；非 workspace agent_use 和 CLI 座位仅增加只读约束。draft 角色和逐文件 map 自动启用，review/write 与 panel 不自动启用；顶替链逐座位应用同一规则，`--text` 与 `--net` 同用返回 64。

agent-use 的 `status.json` 必须声明整数 `protocol_version: 1`。缺失或不兼容时记为 `incompatible`，按 `not_installed` 时长熔断并沿顶替链换下一家。外层超时仍保留超时兜底。失败结果的未知 reason（非 rc=124）会在 stderr 提醒更新 `AGENT_USE_REASON`，分类保持 `other`。

私有适配器 reason：`native_cli_incompatible` 映射为 `not_installed`（App 升级后内置 CLI 结构变化，需更新适配器）；`account_restricted` 映射为 `unavailable`（厂商账号受限）。

本地座位可选布尔字段 `expects_credits`（默认 false）：`kind=agent_use` 的座位启用后，成功但最后一个 attempt 未报告数值型 `credits` 时提示免费保护无法核对收费，成功判定不变。未知 reason 和缺少 credits 的提醒会在 findings 收集开启时写入 `source=worker` 的 observation；关闭收集或写入失败不影响派单。

`aiw doctor --fingerprint` 只输出一行指纹，返回 0，可保存两台机器的输出再 diff。普通 doctor 也显示该行，snapshot 的 `fingerprint` 保存相同结构：aiw、显式 `$AGENT_USE_HOME` 的 Git HEAD（有改动加 `*`），生效注册表的 env/user/repo 来源、SHA256 前 12 位与 Git HEAD，本地座位/路由覆盖及两份个人规则的文件哈希、按仓库根去重的 Git HEAD，以及 `effective_hash`。缺失或无法读取的分量为 `-`，不输出路径。doctor 同时检查启用的 agent_use 座位 provider 是否存在于生效注册表且未设 `enabled=false`；注册表不可读或不匹配只警告，不影响座位安装检查返回码。


### aiw tokens

`aiw tokens [天数=7] [--json] [--top N=10] [--phases]` 只读测量本机 Claude Code 用量，默认最近 7 天、列出最大的 10 个主会话。无数据时文本打印「无数据」，退出码为 0；JSON 仍输出完整的零值结构。`--top` 只限制列表，不影响总计。`--phases` 默认关闭，开启后追加主会话环节拆账和用户回合起点上下文，不改变默认输出结构。

- **构成**：主会话 input、cache_read、cache_creation、output 的合计和占比；token 合计是四者之和。参考 API 权重为 input 1、cache_creation 1.25、cache_read 0.1、output 5。订阅额度的计量权重未公开，折算仅供参考，不代表费用或订阅额度。
- **上下文**：ctx = input + cache_read + cache_creation；展示轮数、中位、最近秩 p90、最大，以及 ctx 严格超过 20 万、40 万的轮次占比和 token 占比（含 output）。固定开销估计 F 是窗口内各会话首轮 ctx 的中位；固定部分 = Σmin(F, ctx)/Σctx，增长部分为其补数。这是窗口内估计，不是实际会话启动开销。
- **会话集中度**：前 3、前 10 个主会话的 token 占比；Top N 展示 session 前 8 位、项目名、轮数、ctx 中位/最大、token、截图数、关联派单数和 codex input。项目名取 cwd 最后一段，缺失时取项目目录名。截图数统计窗口内用户 tool_result content 列表中的 image 块，不推断图片与上下文增长的因果关系。
- **子 agent**：isSidechain 或 subagents 目录中的助手回复不进入主会话统计，按父会话汇总；全局子 agent 占比 = 子 agent token /（主会话 + 子 agent token）。
- **派单关联**：窗口内账本 rc 非 null 的记录按 actual 计数，包含失败和超时；展示有 session 的记录比例，按完整 session 关联主会话。用量按账本记录本身的 token 元数据汇总，不读取当前座位配置：任何 actual（含 codex-high 等别名座位）只要 tokens 是对象且 source=rollout，就合计其 input/cached/output；input 已包含 cached，不再叠加。没有 session 的派单不猜测关联。

`--phases` 只统计主会话 assistant 轮次（排除 isSidechain 和 subagents 文件），一个 message id 算一轮，同 id 各行的工具分类合并，usage 只计一次；权重沿用上述参考 API 权重。分类是按工具调用的启发式，不判断消息正文的意图；同轮多个工具按以下优先级归到第一个命中的环节：

- **发起派单**：Bash 调用 aiw 或 ai_worker.py 的 codex、write、claude-write、goal run、review、draft、panel、codex-sol、workbuddy、workbuddy-cn、qoder、opencode、doubao、qianwen、claude、map。
- **主会话改代码**：Edit、Write、MultiEdit、NotebookEdit 修改非 /private/tmp、/tmp/、scratchpad 路径。
- **跑测试/验收**：Bash 调用 pytest、npm test/npm run test、go test、cargo test、node --test、bun test、swift test、xcodebuild、make、*check.sh、tests/test_harness.py 或 aiw goal verify。
- **提交/PR**：Bash 调用 git commit/push/merge/rebase/tag、gh pr 或 gh issue。
- **等待派单**：Monitor、TaskOutput，或 Bash 命令文本含 `sleep` 加数字、`until `、`ps aux`、`pgrep` 或 `/tasks/<id>.output` 路径（按文本匹配，不判断命令是否真的在等）。
- **写单/草稿**：Edit、Write 修改 scratchpad、/private/tmp、/tmp/ 下文件。
- **调查/读取**：其余 Bash，以及 Read、Grep、Glob、codegraph 等读工具。
- **子代理**：Agent 工具调用；此处统计主会话发起调用的轮次。
- **文字回复**：没有 tool_use 的轮次。
- **其他**：剩余工具调用。

环节展示轮数、加权花费占比、每轮平均加权 token，按花费占比降序排列。**每单等待轮数** = 等待派单轮数 / 发起派单轮数，衡量主会话等待开销；没有发起派单时显示「无派单」。**回合起点上下文**：真实用户文本消息到下一条真实用户消息前为一回合，起点 ctx 取其后第一轮主会话 assistant 的 ctx，花费为该回合主会话轮次的加权合计；展示回合数、起点 ctx 中位，以及严格超过 20 万、30 万的回合数占比和花费占比。排除 isMeta、tool_result、空白文本，以及去空白后以 `<task-notification`、`<system-reminder`、`<local-command`、`Caveat:`、`[Request interrupted`、`<bash-`、`This session is being continued` 开头的消息；窗口内没有后续有效 assistant 轮次的用户消息不计入起点统计，只计算窗口内花费。

`--json --phases` 顶层追加 `phases`（每环节 `rounds/weighted/share`）、`wait_per_dispatch`（无派单为 null）、`turn_start`（`turns/ctx_median`，以及 `over_200k/over_300k` 各自的 `turns_share/weighted_share`）。新增统计只输出环节名和数字，不输出消息正文、命令或路径；额外读取用户文本不改变原有 coverage 计数口径。

转录根目录为 `AIW_CLAUDE_PROJECTS`（直接指向 projects），否则取 `${CLAUDE_CONFIG_DIR:-~/.claude}/projects`；账本为 `${AIW_STATE}/seat-ledger.jsonl`，默认是当前 aiw 仓库的 state 目录，与 weekly 相同。按时间窗口内助手 message.id 去重，缺 id 用 uuid；跳过全零 usage，损坏行跳过并在 stderr 汇报数量。时间无时区的旧格式按本机时区解释。

命令逐行读取，不写文件、不缓存，不输出消息正文；统计项只展示数字、截短 session id 和项目名。项目名仍是本地元数据，分享报告前可自行检查。JSON 包含 totals、weighted、context、concentration、sessions、subagents、dispatch 等对应数字，占比为 0–1。`coverage.sources` 只有两项：转录（`name` 为 `transcripts`）与账本（`name` 为 `seat-ledger.jsonl`），每项包含 `name/exists/rows/bad`，不列出文件名或本机路径。转录项另有 `files`（扫描文件数）和 `files_with_rows`（窗口内至少一条有效记录的文件数）；`rows` 统计通过包含 `"assistant"` 或 `"tool_result"` 的文本预筛后、窗口内的有效行，在助手去重和全零 usage 过滤前计数；`bad` 统计通过预筛后的解析失败行，不再固定为 0。解析失败可能发生在时间戳可读取之前，因此坏行不保证属于窗口内。账本按窗口内 rc 非 null 的有效行计数。根目录不存在时 coverage 为「无数据」，即使账本仍能读到数据。dispatch.rollout 始终保留 codex、codex-sol 键（无用量时 input/cached/output 为 0）；其它有 rollout 用量的实际座位按名字排序追加，字段结构不变。

三个报告的 coverage 均含 `sources`、`level` 和 `reasons`：所有来源不存在或窗口内有效行合计为 0 时为「无数据」；否则有预期来源缺失、解析失败或被跳过时为「部分」，其余为「完整」。例外：tokens 的转录根目录不存在时，无论账本是否有有效行，均为「无数据」。reasons 记录缺失、坏行、跳过或窗口无有效行的原因。文本打印「数据覆盖：」及固定说明「覆盖只说明读到多少，不代表结论可信度」，无数据时也会打印；没有读到与没有发生须分别理解。

`aiw weekly` 的「token（codex 类座位）」小节按账本 token 元数据展示派单的 rollout 合计及覆盖率，同时区分 stderr 兜底、tokens=null 和旧记录；始终展示 codex、codex-sol（无尝试时显示「无数据」），再按名字排序追加窗口内账本行中出现过 tokens 键的 actual（含别名座位及 tokens=null / stderr 记录），没有 tokens 键的普通座位不展示。尝试数及用量仍只计 rc 非 null 的记录。展示及汇总不依赖当前座位配置，配置改动不会改变历史报告；Claude 主会话用量通过 `aiw tokens` 测量。「按仓库」小节按 repo、workdir、未记录分组展示成功率、超时和中位数；`--json` 输出派单统计 JSON，token 和改进缓冲区小节仅在文本模式输出。JSON 顶层新增 `coverage`，统计 seat-ledger.jsonl 和 router-log.jsonl 的窗口内有效行与坏行，原有字段保留；文本 coverage 还覆盖读取的 findings 本机记录、quarantine、status 和当前假设快照（快照 rows 为 0）。


## 派单质量评分：aiw rate

```bash
aiw rate <run> ok|fixed|rework|discard [--note 文本]
aiw rate --pending [--days N] [--session ID]
```

主会话核对产物后评分：ok=直接采纳，fixed=主会话小修后采纳，rework=要返工或重派，discard=丢弃不用。run 接受 run-id、run_dir 的绝对/相对路径或其中的 output.md 路径；按账本 run_dir 的 basename 查最后一条执行行，不要求运行目录仍存在。未知 run 返回 2；`AIW_NESTED=1` 拒绝评分和查询，返回 64。成功输出 run_id、actual 和评分。

评分独立加锁追加到 `state/seat-ratings.jsonl`，字段为 ts、host、session、run_id、seat、actual、role、task_class、rating、note；同一 run_id 以最后一次评分为准。`--pending` 默认近 2 天，有当前会话 ID 时只列当前会话，可用 `--session ID` 指定；列出 kind=ok、非 freecheck 且未评分的执行单，包含 run_id、actual、task_class、时间和 prompt 头，无待评分单时打印「无待评分派单」。

新账本行增加可选 role（用户的 write/review/draft 别名，直接点名座位为 null）、task_class。类别按 ro=false → write、text=true → draft、否则 review 推断；旧行没有 ro 时为 unknown。旧账本不改写。

写类成功单在 run_dir 保存 `changes.json`：workdir、base_head、files（相对路径映射到本单新增非空白行，strip 后去重）、truncated。脏文件使用开工时的文本基线（≤256KB），其余使用开工 HEAD 的内容，不存在视为空；不可用的脏基线、二进制和读失败文件跳过。最多 200 文件、5000 行，超出置 truncated；保存异常只警告，不影响派单和记账。worktree 单的 workdir 指向 source_root，路径相对该源仓库根目录。

`aiw weekly [days=7] [--json]` 在成功率之后展示「按座位 × 任务类别的质量」，JSON 新增 quality。窗口按执行时间，只计 rc 非 null、kind=ok、非 freecheck 的执行单；按 actual × task_class 分组，展示成功单、已评分数/覆盖率、四档计数、采纳率=(ok+fixed)/已评分、返工率=(rework+discard)/已评分、实际执行 verify 的 passed/total 合计通过率（skipped 不入分母）。缺评分文件照常报告。

改动存活率只统计写类、距现在至少 48 小时且有 changes.json 的单：记录行在 workdir 当前文件中 strip 后仍出现即存活；文件删除则其行不存活；workdir 不存在或证据缺失不计样本。空证据不计样本，组内取各单比例平均，并显示样本数。它测量记录行的留存，不等于语义正确性；truncated 单只测已记录部分。runs 默认 30 天 GC，证据可能随之清理。

每项比率的分母/样本数不足 10 时标「样本不足」。评分 ≥10 时，返工率 ≥30% 建议该类少派该座位；采纳率 ≥90% 且存活率在有至少 10 个样本时 ≥80%，建议多派；存活样本不足 10 时不限制多派条件。无达标组显示「暂无足够数据给建议」。建议不自动改变派单顺序。
