# 状态文件约定

aiw 负责写状态，可选的独立组件 aiw-board 只读展示，不修改状态、清理残留或操作派单锁。通常共享目录为 `~/.claude/aiw/state`；worker 默认写自身仓库的 `state`，aiw-board 默认读上述安装目录，双方可通过 `AIW_STATE` 指向同一目录。使用独立 worktree 时需注意两者路径一致。

## context-hints/<session_id>

UserPromptSubmit 钩子保存该会话上次上下文提醒的 bucket（十进制整数纯文本），先写同目录临时文件再原子 replace。session_id 只接受 UUID 或 `[A-Za-z0-9_-]{1,80}`；无效 ID 不提醒。仅 Claude 主会话使用此状态，同一会话只有更大的 bucket 才再次提醒；读取或写入异常静默跳过。读-比较-写及低于阈值时删除标记均在每会话非阻塞锁 `context-hints/.<session_id>.lock` 内进行，拿不到锁本次不提醒；transcript 必须是普通文件。

## handoffs/<YYYYmmdd-HHMMSS>-<项目名>.md

`aiw handoff --path` 创建 handoffs 目录并打印建议路径，不创建文件。项目名取工作目录最后一段，非 ASCII 字母数字替换为 `-`。主会话经用户同意后写入 Markdown 交接内容，`aiw handoff <文件>` 校验后在 Orca 打开新 Claude 会话；此目录保存交接材料，不由展示端修改。

## router-log.jsonl

每次路由判定追加一行 JSON，原有 ts、host、project、rule、source、escape、len 字段保留。新增 `ctx` 为上下文 token 整数或 null（未取得 usage、Codex 或非钩子调用）；计算方式为 input_tokens + cache_read_input_tokens + cache_creation_input_tokens。跨会话消息额外写 `cross_session: true`，rule 为 null；普通消息不含 cross_session 字段。上下文提醒节流不影响 ctx 记录。

## codex-home/

codex / codex-sol 执行方的独立 CODEX_HOME，目录权限 700。按需原子生成的 `config.toml` 权限 600，仅含源配置的 provider 白名单；`auth.json` 是指向源文件的软链，不保存凭据副本。Codex 会话记录（rollout）保存在 `codex-home/sessions/`。生成失败回退共享配置，不阻断派单；`AIW_CODEX_SHARED_HOME=1` 关闭隔离。详见参考手册「执行方独立 CODEX_HOME」。

## inflight/<pid>.json

worker 每次真正尝试某个座位前写入或覆盖自身 PID 对应的文件，顶替尝试沿用文件名并更新内容；先写临时文件再原子替换。worker 退出时在 finally 中删除。被跳过的熔断座位不写入；强制杀死进程或清理失败可能留下残留，展示端按下述文件锁约定判活，不负责删除。写入失败会警告，不阻断派单。

worker 在首次写 JSON 前创建或打开同名 `<pid>.lock` 并持有 `fcntl.LOCK_EX | fcntl.LOCK_NB`，持锁期间即在途。锁描述符不可继承，启动座位子进程保留 `close_fds` 默认值；即使 worker 被 SIGKILL 而座位子进程仍存活，锁也随 worker 退出释放。正常退出的 finally 先删 JSON，再删 lock，最后关闭描述符；拿锁或删除失败只向 stderr 警告，不阻断派单。

原则：**worker 活着时，`.lock` 要么被它持有，要么不存在**（不存在时展示端退回 pid 判断）。打开或拿锁失败时关闭描述符、删除锁文件并重试一次；仍失败则再次删除锁文件后继续派单，删不掉则只警告一次并停止本次 inflight JSON 发布。

展示端以只读方式打开 `.lock`（不存在时不创建），用 `LOCK_SH | LOCK_NB` 试锁：锁得上表示持有者已死、记录是残留，立即释放；锁不上表示在途。没有 `.lock` 的旧记录退回 `os.kill(pid, 0)`。展示端仍不得删除或修改任何文件。

| 字段 | 类型和含义 |
| --- | --- |
| pid | 整数，worker 进程 PID，也是文件名 |
| host | 字符串，本机 hostname，用于主机过滤 |
| session | 字符串或 null，`CLAUDE_CODE_SESSION_ID`，没有时取 `CODEX_THREAD_ID`，用于会话过滤 |
| claude_pid | 字符串或 null，环境变量 `CLAUDE_PID` 原值 |
| seat | 字符串，最初请求的座位 |
| actual | 字符串，本次实际尝试的座位，顶替时可能不同于 seat |
| model | 字符串，实际座位配置的模型，未配置时为 `default` |
| workdir | 字符串，派单工作目录 |
| prompt | 字符串，提示词 SHA1 的前 10 位，不含提示词原文 |
| ro | 布尔值，是否请求只读运行 |
| timeout | 数值，本次尝试的超时秒数 |
| started | 字符串，带时区的 ISO 8601 本次尝试开始时间，顶替时重置 |
| panel | 字符串或 null，`AIW_PANEL_DIR`，用于会诊分组；panel.py 为子进程设置 |
| nested | 布尔值，启动 worker 时 `AIW_NESTED` 是否等于 `1` |
| effort | 字符串，可选；本机 codex / codex-sol 本次实际推理档位（low/medium/high），转发时仅显式传参才出现 |
| run_dir | 字符串，可选；转发时为本机 run 目录的绝对路径 |

转发在启动命令前复用同一 liveness 锁和发布机制，`seat` 与 `actual` 均为请求的角色或座位名（执行期间尚不知道远端实际座位），`model` 为 `default`，`panel` 仍取 `AIW_PANEL_DIR`（map 为 null）。rc=69 或启动失败后，本机尝试覆盖同一 inflight 文件并复用锁；退出时统一清理。

## tracks/<pid>.json

`aiw track [--host 名称] [--timeout 秒] <标签> -- <命令> [参数...]` 登记主会话启动的本机盯梢命令，实际任务可在远端机器。STATE 同 worker：优先 `AIW_STATE`，否则使用本仓库 `state`。

| 字段 | 类型和含义 |
| --- | --- |
| pid | 整数，track 包装进程 PID，也是文件名 |
| host | 字符串，本机完整 hostname，与 inflight 相同，用于主机过滤 |
| target | 字符串，实际任务机器的展示名；`--host` 值，默认本机 hostname 短名 |
| session | 字符串或 null，`CLAUDE_CODE_SESSION_ID`，没有时取 `CODEX_THREAD_ID`，用于会话过滤 |
| label | 字符串，用户提供的任务标签 |
| timeout | 数值或 null，`--timeout` 秒数，仅展示，不终止命令 |
| started | 字符串，带时区 ISO 8601 开始时间 |
| progress | 字符串，合并输出最后一个非空行，去掉 ANSI 序列与控制字符后截到 80 个字符；初始为空串 |
| updated | 字符串，带时区 ISO 8601 最近一次状态写入时间 |

锁约定同 inflight：先在 `tracks/<pid>.lock` 持有 `LOCK_EX | LOCK_NB`，描述符不可继承，再用临时文件原子替换 JSON。拿锁失败清理后重试一次；锁不存在时按 PID 判活，锁残留且不能删除时停止发布。状态拿锁、写入或清理失败只向 stderr 警告，不阻断命令执行与输出转发。正常退出先删除 JSON，再删除 lock，最后关闭锁描述符；SIGKILL 可能留下记录，展示端按与 inflight 相同的共享锁规则判活。

首次写入空进度；此后仅在 progress 变化时更新，最多每 5 秒一次，`AIW_TRACK_FLUSH_SECS` 可覆盖秒数（测试用）。期间多行合并为最新一行，即使随后输出空闲也会在节流窗口结束时更新。读输出阻塞等待，进度线程用条件变量等待，不忙等、不访问网络、不启动额外子进程。结束时直接清理，不额外强制写入待刷新进度。`AIW_TRACK_TEST_WRITE_COUNT=1` 仅供离线测试在 stderr 输出成功原子替换次数。

子进程 stdout/stderr 合并后按原始字节逐行转发并 flush，保留空行、换行格式和末尾无换行内容；进度清洗不影响输出。SIGINT/SIGTERM 转发给本机命令进程组并等待退出，退出码（含信号终止状态）透传；不管理远端模型的生命周期。

**不写 `seat-ledger.jsonl`**：track 不是派单，不计入今日单数或周报。展示端对 tracks 的读取另行实现。

## seat-ledger.jsonl

追加写入，每行一个 JSON 对象，记录一次尝试或跳过事件。aiw-board 使用以下字段：

| 字段 | 含义 |
| --- | --- |
| ts | 带时区的 ISO 8601 记录时间，用于流水、今日与最近完成统计 |
| host | hostname，用于本机过滤 |
| session | 会话 ID 或 null，`CLAUDE_CODE_SESSION_ID`，没有时取 `CODEX_THREAD_ID`，用于会话过滤；旧记录可能缺失，仅在全部会话视图出现 |
| seat / actual | 请求座位和实际座位，用于展示顶替关系 |
| executor | 可选字符串；转发单固定为 `forward`，旧记录和本机单不写此字段 |
| forward_host | 转发单的远端主机展示名，来自 `forward.json` 的字符串 `host`，缺失或类型错误为 null；不替代本机 `host` |
| kind | 结果类别；判断成功只看 `kind == "ok"`，失败类型也以 kind 为准，如 `timeout`、`format`、`auth`、`transient`（供应商单次失败，顶替但不熔断）、`interrupted`、`skipped_breaker` |
| rc | 该次尝试的子进程退出码，不是 aiw 的最终退出码；跳过记录可为 null，不作为已完成尝试统计 |
| secs | 本次尝试耗时秒数 |
| err_tail | 错误输出最后 300 个字符，流水展示最后一行；跳过记录可能缺失 |
| substituted | 布尔值，actual 是否不同于 seat，用于顶替成功统计；跳过或旧记录可能缺失 |

例如 `kind=timeout, rc=1` 表示子进程报告超时；`kind=format, rc=0` 表示子进程成功退出但产出不合格，都不算成功。aiw 最终退出码由 [ai_worker.py 的 main() 结果决策分支](../ai_worker.py#L1702) 决定：`ok` → 0，`timeout` → 124，`format` / `empty` → 2，`locked` / `fatal` / 其它失败 → 1；`paid`、`transient`、`incompatible_agent_use` 和熔断类（`auth`、`quota`、`ratelimit`、`not_installed`、`incompatible`、`unavailable`、`verify`）继续顶替链，由后续结果决定，链耗尽返回 1。信号中断的退出码见下文兼容性说明。

账本还可能含 prompt、ro、model、detail 等字段，展示端不能假设所有事件都有同一组键。以下也是可能出现的字段，展示端不依赖它们：

| 字段 | 含义 |
| --- | --- |
| text | 可选布尔值；`--text`（含 draft/map 自动启用）时写入 true，否则省略。隐含 ro；仅 workspace agent_use 禁用工具、access=none，CLI 和非 workspace agent_use 仅受只读约束；顶替链每条执行或跳过记录沿用此值 |
| via | 调用来源：direct（直接派单）、panel（aiw panel 会诊）、map（aiw map 批处理）；旧记录可能缺失 |
| model | `agent_use` 座位的完成记录为实际产出答复的模型，按 `actual_model` → `selected_model` → `requested_model` 取值，都缺失时回退到座位配置；其他座位为座位配置的模型，未配置时为 `default` |
| freecheck | 每日免费检测由 freecheck.py 写入 true；周报座位质量统计据此剔除检测行，同时不计 skipped_* 跳过行 |
| agent_use_versions | 可选字符串：status.json 有 scheduler_versions 时，对 `{"scheduler": scheduler_versions, "adapter": 最后一个 attempt 的 adapter_versions 或 {}}` 以 `json.dumps(sort_keys=True, separators=(",", ":"))` 序列化，取 UTF-8 字节的 SHA256 前 12 位；仅记录摘要，不保存原始映射。旧版无 scheduler_versions 时不写此字段 |
| tokens | 仅实际执行座位的 seat_adapter(s) == "codex" 时写入，包含别名座位；历史用量报告按账本 token 元数据及 actual 汇总，不依赖当前座位配置。rollout 为 `{input: int, cached: int, cache_write: int, output: int, reasoning: int, calls: int, source: "rollout"}`，取最后非空 total_token_usage，calls 为非空 info 的 token_count 事件数；input 含缓存命中；rollout 在 CODEX_HOME、~/.codex、状态目录下的 codex-home 三处查找。stderr 兜底为 `{uncached_plus_output: int, source: "stderr"}`，为 input − cached + output，不含缓存命中；两者不可混算。无法测量时为 null；文本座位、跳过及旧记录没有此键 |
| tokens_null_reason | rollout 未用上时的原因：no_session_id、rollout_missing、rollout_unparsed、error；stderr 兜底或 tokens=null 时出现，正常 rollout 不写此键。异常只影响测量，不改变派单结果 |
| findings_n | 可选整数，本次收集的 observation 条数；未收集时无此键，展示端不依赖它 |
| changed_files | #6：写任务每次尝试相对派单开始时的变化文件数；非 git 或统计失败为 null；只读任务没有这个键 |
| worktree | 可选字符串，`ai_worker.py` 仅为 `--worktree` 单写入隔离工作树根目录的绝对路径；普通单不出现，结束后目录可能已清理 |
| worktree_branch | 可选字符串，`ai_worker.py` 仅为 `--worktree` 单写入对应的 `aiw/<run-id>` 分支名；普通单不出现，结束后分支可能已清理 |
| run_dir | #7：实际运行过的尝试都有 run 目录路径；归档失败或没有运行为 null（跳过记录也可能没有此键） |
| repo | #8：origin remote 经 findings._repo 规范化后的仓库标识；无 git、无 origin 或解析失败为 null；同仓库不同 worktree 相同，旧记录可能缺失 |
| workdir | #9：仅 repo 为 null 时记录工作目录的绝对真实路径，否则为 null；旧记录可能缺失 |
| effort | 字符串，可选；codex / codex-sol 本次实际推理档位（low/medium/high），其他座位不出现；weekly.py 按 (seat, effort) 计数 |

转发最终结果只记一条账本；rc=69 或命令启动失败不记转发尝试，回退后按原本机流程记账。转发单 `seat` 保留请求的角色或座位名，`actual` 取 `forward.json` 的字符串 `actual`，否则取请求名，允许不在本机 SEATS 中；评分和周报按未知座位名展示。`substituted` 固定为 false，远端实际座位变化不计本机顶替。`run_dir` 为本机目录，保存 `task.txt`，转发命令可写 `output.md`；不记录远端 token 或本机文件改动统计。转发单的 `effort` 仅在显式传参时记录，`model` 为 `default`。`kind` 按转发退出码是否为 0 记 `ok` / `other`；本机验收失败仍记 `ok`，`rc=4` 并写 `verify`，与本机验收语义一致。

## breaker.json

顶层为座位名到熔断对象的映射。aiw-board 使用 `until`（Unix 时间戳，秒）判断是否仍在熔断，使用 `reason` 展示原因。worker 还写 `since`（带时区的 ISO 8601 时间），当前展示不依赖它。熔断为本机共享状态，不按会话过滤。

## daily-count.json

- `date`：本机日期 `YYYY-MM-DD`；仅与今日一致时展示 `counts`。
- `counts`：座位名到当日成功顶替次数的映射，直接点名不计入。
- `reserved`：预约 token 到对象的映射。展示使用 `seat` 分组，并用 `ts`（Unix 时间戳，秒）仅计入未满 7200 秒的预约；对象还含 `pid`，当前展示不依赖它。预约在途占额度，完成时释放，成功时计入 counts。

每日用量和预约均为本机共享状态，不按会话过滤；展示端只读，不负责跨日重置或预约过期清理。

## 兼容性与验收

改字段名或语义时必须同步修改 aiw-board，并更新 `tests/cases/misc.py` 的 `test_state_contract`。该测试运行真实 worker 配合假后端，在运行中校验 inflight 键集合恰好为上述 14 个字段，完成后校验账本最新记录至少包含约定的 11 个字段，并检查普通单不含 worktree/worktree_branch、`--worktree` 单含这两个可选字段且值对应运行目录和分支。

运行：`python3 tests/test_harness.py test_state_contract`；完整回归：`python3 tests/test_harness.py`。

每次尝试恰好一行账本：尝试进行中（含归档）被中断，该行记为 `kind: "interrupted"`，stdout/stderr 照常归档。记账关键区（attempt 更新、顶替结算、变化统计、账本写入）延迟交付信号；账本保留真实结果，完成后以中断码退出，不再补写。SIGINT 为 130，SIGTERM 为 124。启动登记、进程组清理和归档同样延迟中断，清理完成后保留已收集输出，已归档结果不重复覆盖。

拿不到 liveness 锁且锁文件删不掉时，只警告一次，本次派单不发布任何 inflight JSON；派单照常进行。锁文件不存在时仍写 JSON，展示端按 PID 判断。

## runs/<尝试目录>/

直接派单成功时，默认在该次尝试的 run 目录写 `provider`，stderr 打印「实际执行方」及文件路径；`AIW_PROVIDER_FILE` 可显式指定位置，panel/map 继续使用各单的 `.provider`。已有工作区 `.provider` 不删除。Git 写单在执行前把 `git status --porcelain` 保存为 run 目录的 `baseline-status.txt`，结束报告分列开工前已有改动与本单期间新出现变化的文件，均列数量和文件。写锁使用 Git 仓库根目录的 realpath；非 Git 使用 workdir 的 realpath，锁文件不回收。`ai_worker.py` 和 `findings.py` 的 Git 调用统一经 `gitenv.repository_git_env()` 清除调用方的仓库定位变量，保留认证和普通 Git 配置环境变量。

## worktrees/<run-id>/、worktrees/<run-id>.json 与 worktrees/archive/

`ai_worker.py` 为 `--worktree` 单从源仓库 HEAD 创建隔离工作树及 `aiw/<run-id>` 分支，创建前在原 workdir 按全部附件规则预检；附件必须已提交，工作区及索引相对 HEAD 有差异或未跟踪时返回 64，不创建工作树、分支或元数据。通过后在隔离目录读取附件，普通单附件行为不变。

worker 创建时原子写入同级 `<run-id>.json`，含 `path`（工作树根目录绝对路径）、`branch`、`source_root`（源仓库根目录）、`base_head`（初始 HEAD 提交）、`created_at`（带 UTC 时区的 ISO 8601 创建时间）。本单无已跟踪/暂存改动、无未跟踪的非忽略文件且分支未前进时即时清理。worker 报告后及 `gc.py` 扫描跳过持写锁目录；已合并且干净的清理，超过固定 7 天的先由 worker 保存相对初始 HEAD 的二进制补丁 `archive/<run-id>.patch` 再强制删除工作树、分支和元数据，存档失败保留。被忽略文件不计入干净判断、不存档到补丁，删除工作树时一并删除。在初始 HEAD 上用 `git apply --binary <补丁>` 恢复。

源仓库丢失时，worker 将残留目录整体保存为 `archive/<run-id>.files/`（包含残留文件），再删除元数据；含子模块或嵌套仓库等无法生成补丁的情况保留工作树待检查。`gc.py` 按指定天数（默认 30 天）与 mtime 清理过期的 `*.patch` 普通文件及 `*.files` 目录，目录删除方式同 runs；跳过软链，`--dry-run` 只报告，不存档或删除。展示端只读。

## panels/<YYYYmmdd-HHMMSS>-<pid>/

`aiw panel` 按 `format_panel_id` 生成会诊目录名，保存各家共用的 `prompt.md` 快照（含预检读取一次后渲染的附件全文，所有座位含 CLI 均使用此快照）、有 `--prior` 时归档且不发给座位的 `prior.md`、供主会话逐条填写裁决的 `adjudication.md`、各座位的 `<座位>.md`（worker 的 stdout）、有错误输出时的 `<座位>.stderr.txt` 和成功 worker 写出的 `<座位>.provider`（实际执行方）。会诊外层超时时不写该座位的 `.md` 和 `.stderr.txt`。`summary.json` 含 `dir`（会诊目录路径）、按座位顺序排列的 `seats`（每家结果含 `seat`、`rc`、`chars`、`err`；chars 为 stdout 字符数，err 为 stderr 最后一个非空行的列表，无错误输出时为空列表，外层超时时为超时提示列表且 rc=124、chars=0）、`answered`（成功家名单，成功为 rc==0 且 chars>0）、`required`（固定为 2）、`sufficient`（answered 数量 ≥ required；false 时 panel 返回 1）。子进程通过 `AIW_PANEL_DIR` 标记账本 via=panel。`aiw gc` 与 runs、maps 一样按子目录修改时间清理过期 panels，默认保留 30 天，可指定非负整数天数；跳过软链，`--dry-run` 只预览。

有附件时写 `attachments.json`，按输入顺序列出 `{"path": 相对工作目录的路径, "bytes": 字节数, "sha256": 完整十六进制}`；字节数与 SHA256 均由实际渲染进 `prompt.md` 的原始附件字节计算，不重新读取源文件。无附件时不写 `attachments.json`。附件预检失败返回 64，不启动任何 worker。

## maps/<YYYYmmdd-HHMMSS>-<pid>/

`aiw map` 保存共用的 `prompt.md` 快照、各单的 `<两位序号>-<文件名>.md`、有错误输出时的 `.stderr.txt` 和成功 worker 写出的 `.provider`。`summary.json` 含 `dir`、`seat`、`jobs`、按输入顺序排列的 `items`（`file`、`rc`、`provider`、`chars`）；没有成功执行方时 provider 为 null。子进程通过 `AIW_MAP_DIR` 标记账本 via=map。`aiw gc` 与 runs、panels 一样清理过期 maps 子目录。

## findings/

改进缓冲区的本机状态位于 `$STATE/findings/`。记录只追加，处理状态单独存储；aiw-board 不读取这些文件，aiw gc 保留整个目录。

| 文件 | 写入方 | 内容 |
| --- | --- | --- |
| `records.jsonl` | worker、`aiw note` | answers / observation / changes；flock 保护，只追加 |
| `quarantine.jsonl` | worker、note | 命中密钥特征的记录 |
| `records.lock` | record、triage | 记录追加与评审状态修改的 flock 互斥 |
| `daily-<YYYY-MM-DD>.count` | record | 当日非隔离观察计数，超 daily_cap 只计数 |
| `status.jsonl` | triage apply | 每行 {id, status, hypothesis, note, triage_id, ts} |
| `hypotheses.json` | triage apply | {id: 假设对象}，含 created / updated |
| `hypotheses-log.jsonl` | triage apply | 每行 {ts, triage_id, id, before, after} |
| `triages.jsonl` | triage apply | 每行 {ts, triage_id, host, records, hypotheses} |
| `count-cache.json` | record、triage apply | {open, last_triage, oldest_open} |
| `triage.lock` | triage start / apply / abort | {host, pid, triage_id, started, expires}，两小时租约 |
| `signals.json` | findings extract | 机械信号与 coverage 快照 |
| `triage/<id>/` | triage start | batch.md / batch.json / lock.json |
| `hint.json` | 钩子 | 上次提示时间，四小时节流 |

open 为无状态或 unexplained 的 observation 条数，record 增量更新，apply 全量重算；钩子不扫描日志。signals.json 的 coverage 包含 sources（name/exists/rows/bad）、level（无数据/部分/完整）、reasons；来源只有本机 records、规则 Git 历史和 runs。周报读取本机记录、status.jsonl 与 hypotheses.json。详细字段校验见 [findings.md](findings.md)。

### 本地配置与配置指纹

findings 记录的 `rules_hash` 仍按顺序拼接四个公开文件（`CLAUDE.md`、`AGENTS.harness.md`、`router-rules.json`、`seats.json`）的字节计算 SHA-1 前 12 位。新增 `effective_hash` 为合并配置的行为字段规范化 JSON（键排序、紧凑分隔符）的 SHA-1 前 12 位：包含 `seats`（去掉每个座位的 `label`、`login_hint`、`tools_note`）、`fallback`、`panel_default`、`panel_free`、`self_seats`、`substitute_limit`、`breaker`；忽略 `notify`、`findings`、`_comment` 及其他字段。

snapshot 同样新增 `effective_hash` 和布尔 `local_config`（是否使用本地覆盖，不记录路径）；`seats_json_sha` 继续哈希公开文件字节。仅修改本地行为配置会改变 `effective_hash`，不会改变公开文件指纹。

`snapshot.fingerprint` 与 doctor 的单行指纹共享结构：`harness`、`agent_use` 为短 HEAD（dirty 加 `*`）；`registry` 含 `source`（env/user/repo）、`sha256`、`git`；`local` 含 seats/route/claude/agents 四个 SHA256 前 12 位；`local_git` 为按仓库根去重后排序的 HEAD 列表；`effective_hash` 为行为配置哈希。缺失分量为 `-`（无本地 Git 仓库时 `local_git=["-"]`），不存路径。

agent-use 协议不兼容的账本 `kind=incompatible` 对应 breaker 的 `not_installed` 类型。`kind=incompatible_agent_use` 表示本机 agent-use 不支持 workspace 参数；不启动子进程、不熔断，继续顶替链，链耗尽返回 1。可运行 `aiw sync` 或去掉座位的 workspace 配置。未知 reason、缺少预期 credits 的诊断复用现有 `source=worker,type=observation` 记录，不新增 findings 字段，写入失败静默跳过。

## tui/<AIW_TUI_ID>

纯文本一行，保存 Codex 主会话的线程 ID。`aiw tui` 生成随机标识并通过环境传给 codex，标识只接受 `[A-Za-z0-9_-]+`。ai_worker 在派单发布在途记录时，若会话来自 `CODEX_THREAD_ID` 且 `AIW_NESTED` 不为 `1`，先写同目录临时文件再原子 replace；写失败仅警告，不阻断派单。`aiw tui-status` 读取此文件，将 `{"session_id": "<id>"}` 送给可选组件 `aiw-board statusline`。首单前没有映射，显示空闲；`aiw gc` 按文件 mtime 和指定天数清理，支持 `--dry-run`。

workspace agent_use 写单与 CLI 写单共用 `changed_files`、`baseline-status.txt` 和文件变化汇报；ro 不增加改动字段。findings 的 `partial_context` 按实际执行座位和本次模式判断：agent_use 在非 workspace 或本次 `--text`（含 draft/map 自动启用）时为 true，workspace review 时为 false。snapshot 对启用 workspace 的 agent_use 座位形状记录 `workspace: true`，缺省形状保持不变。
