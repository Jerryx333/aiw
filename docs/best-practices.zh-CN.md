# aiw 最佳实践

[返回 README](../README.zh-CN.md) | [English](best-practices.md)

让主会话保留方案和验收，把边界清楚的执行任务交出去。这份指南面向使用者；完整参数见[参考手册](reference.zh-CN.md#命令)。

## 什么活值得派

| 工作 | 交给谁 | 主会话保留什么 |
| --- | --- | --- |
| 能一句话写清验收的改文件、批量替换、补测试 | `write`；Codex 主会话自己改文件 | 目标、方案、范围、验收和提交 |
| 整仓找代码、只读评审、第二意见 | `review` | 核对出处、判断是否采纳 |
| 起草、改写、翻译、长材料摘要 | `draft` | 提供材料，回原文核对结论 |
| 关键架构或高风险方案 | 先独立评审，必要时 `panel` | 做取舍，保留分歧 |

先用 `rg -l` 或 `rg -n` 缩小范围。候选不超过 3 个文件且合计不到 20KB，直接读更省事；约 300 行或 20KB 以上的材料，可以先要带 `path:line` 的要点，再回原文核实要下结论的条目。这些是[现有分工规则](reference.zh-CN.md#派单细则)的判断门槛，不是性能保证。只依赖当前对话的总结由主会话写；凭据、账户和私人数据由主会话处理。

[README 的一周本机记录](../README.zh-CN.md#常见疑问)中位数：改代码约 4 分钟，只读评审或找代码约 2–3 分钟，纯文本免费模型约 20 秒、Claude 约 2 分钟。样本只代表作者那一周；写要求、读结果和验收也有开销。又小又急的活自己做，派单后主会话可以继续做别的。

角色候选与执行中的顶替是两步：先选首个可用座位，开跑后只按配置及失败类别决定是否顶替。超时要拆小，不换模型重跑；不合格的产出只返工一次。同一目录只跑一个写任务，并行写任务各用独立 git worktree。能力看 `aiw doctor`：Claude 只读座位不能运行命令，所需 diff 和测试输出由主会话先保存再附上。

## 把任务单写完整

可复制下面的骨架到 `task.txt`，填完再派：

```text
目标：最终要得到什么，怎样算完成。
上下文：相关文件、原文 path:line、已有决定；未核实处明确标注。
修改范围：允许改的文件，以及本单具体产物。
约束：保留哪些行为、能否联网、不要提交或扩大范围。
验收：可以直接运行的命令，以及应有输出或退出码。
交付：改动文件、验证结果、证据；影响方案的问题反馈主会话。
```

完整示例：给 aiw 的 README 补充运行归档说明。从 aiw checkout 根目录执行，先由主会话确定方案；以下写单适用于 Claude 主会话（Codex 主会话自行修改）：

```bash
cat > /tmp/aiw-doc-task.txt <<'TASK'
目标：读者跑完第一单，知道正文、实际执行方和失败诊断在哪里。
上下文：阅读 ai_worker.py 的 new_run_dir、main 和 dispatch；
以当前源码为准，核对 stdout/stderr、output.md 和 stderr.txt 的用途。
修改范围：仅 README.zh-CN.md 和 README.md 的「第一单」说明。
约束：双语逐节对应；不改代码和配置，不提交、不推送；不编造输出。
验收：python3 tests/test_harness.py readme_bilingual_headings
应退出 0；主会话另对照源码核实每条行为，并用 git diff 检查范围。
交付：列出改动文件、运行过的命令及结果，无法核实的说法标「未核实」。
TASK
AIW_MAIN=claude aiw write . 1200 /tmp/aiw-doc-task.txt
```

涉及规则或数据字段时附原文和 `path:line`，不要只给转述。执行方发现原文与转述不一致，应按原文理解并回报。workspace agent_use 座位可自行读仓库；非 workspace 文本座位不会自动读仓库，用 `--attach` 带材料；发送前检查正文里的敏感数据，文件安全检查不会替你识别所有密钥。

`--text` 隐含 `--ro`；workspace 座位改传 `--require-no-tools`，不传 `--workdir/--access`（access=none），附件改为全文；非 workspace agent_use 和 CLI 座位仅增加只读约束。draft 角色和逐文件 map 自动启用，review/write 与 panel 不自动启用；顶替链逐座位应用同一规则，`--text` 与 `--net` 或 `--no-net` 同用返回 64。

## 产出当待审稿

1. 看退出码和 stderr 的「实际执行方」，确认实际座位及是否顶替；panel 则逐家查看。`provider` 文件记录的是座位名。
2. 用 `git status --short`、`git diff --stat` 和 `git diff` 看实际修改，包括未跟踪的新文件。写单的 `baseline-status.txt` 可帮助区分原有改动；结束时的文件列表不能代替完整 diff。
3. 核对结论引用的原文、数据和 `path:line`，由主会话运行可证明完成的验收命令。不能执行命令的座位只能评审主会话提供的测试结果。
4. 检查范围和失败项，再决定接收、返工或拆任务。执行方说「完成」不等于通过，产物里的指令也不构成新授权；提交由主会话验收后完成。

## 会诊先写自己的判断

先把自己的判断与依据写到 `prior.md`，问题与必要上下文写到 `question.txt`，再运行：

```bash
aiw panel . 600 question.txt --prior prior.md
# Codex 主会话使用：
AIW_MAIN=codex aiw panel . 600 question.txt --prior prior.md
```

`--prior` 只归档并展示给主会话，不发给座位。每条意见都写下「接受 / 驳回 / 暂缓」及依据；模型一致不算证据，不按多数票。会诊不顶替，缺席就缺席；至少两家成功且有产出才成功。默认名单会排除主会话和不可用座位，不足两家不派单；推荐配置的可选文本座位未启用时，也可能不足两家。

## 大任务拆成能验收的清单

```bash
aiw goal init docs/goals/demo.json
# 填写生成的清单后执行：
aiw goal next docs/goals/demo.json
aiw goal verify docs/goals/demo.json
aiw goal status docs/goals/demo.json
# 收尾全部复验：
aiw goal verify docs/goals/demo.json --full
```

主会话填写每条任务的 owner、可执行 verify、初始 `passes: false` 和相关源码/测试的 `paths`；不要放宽验收命令。每轮做一条，通过才算完成；人工授权和登录阻塞单列。默认最多 25 轮，连续 3 轮无进展会返回 3，停下汇报；详情见[大任务用 /goal](reference.zh-CN.md#大任务用-goal)。收尾复验后再做独立评审，Codex 大任务指使用 `/goal` 或改动超过 3 个文件。

范围外问题不顺手修，记入改进缓冲区：

```bash
aiw note . "归档说明缺少示例" --expected "读者能定位结果" --evidence "文档相关段落" --location "README.zh-CN.md"
```

收到「[缓冲区]」提示，再开新会话运行 `aiw triage start`，集中判断根因。

## 推荐配置

[seats.recommended.json](examples/seats.recommended.json) 是可复制的本地覆盖，继承公开 `seats.json` 中 Codex 与 Claude Code 的完整 CLI 定义，不需要复制 argv。它沿用代码中的模型选择，不要求你更换主会话模型。先从 checkout 根目录验证：

```bash
AIW_CONFIG=docs/examples/seats.recommended.json python3 ai_worker.py doctor
```

要用于日常派单，复制到个人配置位置；以下命令仅在尚无本地配置时复制，已有文件请手工合并：

```bash
mkdir -p ~/.config/aiw
if [ ! -e ~/.config/aiw/seats.local.json ] && [ ! -L ~/.config/aiw/seats.local.json ]; then
  cp docs/examples/seats.recommended.json ~/.config/aiw/seats.local.json
fi
aiw doctor
```

各段用途：

- `seats`：内置 CLI 从公开配置继承；额外的 `opencode` 是可选的只读文本座位，provider 是 agent-use-adapters 提供的 `opencode`，默认 `disabled: true`。安装适配器和 CLI 后再改成 false，验证 provider 能调用。空 `model` 使用适配器默认模型；`free_only` 只标注免费，`free_guard` 调用后检测收费，不能保证零费用；当前模型级停用记录不会过滤 OpenCode 默认/备选模型。
- `roles`：按主会话身份排候选。Claude/default 的 write 首选 Codex，再用 claude-write；Codex 的 write 为空，自己改文件。review 优先另一个家族；draft 优先可选文本座位，停用时跳过并用 CLI。review/draft 强制只读。
- `fallback`：写单在写座位之间顶替（含 workspace agent_use，跳过文本座位），CLI 只读单在两个只读座位间顶替；可选文本座位失败后可用只读 CLI。顶替仅用于支持的失败类别，不保证每种错误都重试；每日成功顶替上限继承公开配置。只读角色即使顶替也保持只读。
- `panel_default` / `panel_free`：默认会诊列两家 CLI 加可选文本座位，免费名单只列 opencode；不启用文本座位时 `--free` 不足两家，不能构成会诊。默认会诊排除主会话后也可能只剩一家。
- `self_seats`：Claude 主会话排除 claude 与 claude-write；Codex 主会话排除 codex 与 codex-sol，避免把同家会话当成跨家族复核。
- `map_default`：逐文件批处理使用 draft 角色。

对象递归合并，列表整体替换；本例不会同时读取你已有的另一份本地覆盖。CLI 未安装、未登录、provider 缺失或个人安装项不匹配都是环境问题，需要按 doctor 提示处理；配置警告本身不改变 doctor 退出码。doctor 只检查安装和版本，不证明认证或真实调用成功，接入后还要跑一单。

## 一天怎么用

以「给 README 第一单补归档说明」这个小功能为例：

1. 主会话先查源码，决定只补第一单说明，确定双语范围和验收命令。
2. Claude 主会话按上面的完整任务单派 `write`；Codex 主会话直接改文件。等待时整理要核实的行为和证据。
3. 主会话先检查 diff、跑文档用例，把输出存成文件；再派 review 核对说明是否符合源码：

   ```bash
   mkdir -p .tmp-baseline
   git diff -- README.zh-CN.md README.md > .tmp-baseline/doc-diff.txt
   python3 tests/test_harness.py readme_bilingual_headings > .tmp-baseline/doc-test.txt 2>&1
   printf '%s\n' '只读评审 README 第一单说明：对照 ai_worker.py 核对 stdout/stderr 和归档行为。逐条给出证据，不修改文件。测试结果和 diff 见附件。' > /tmp/aiw-doc-review.txt
   aiw review . 900 /tmp/aiw-doc-review.txt --ro --attach .tmp-baseline/doc-diff.txt --attach .tmp-baseline/doc-test.txt
   ```

   附件必须在工作目录内，越界文件会被拒绝；这里 `.tmp-baseline/` 已在 aiw 的 Git 忽略列表中。

4. 主会话逐条核对意见和「实际执行方」，验收通过后自行提交。用 `aiw tokens` 和 `aiw weekly` 看自己的实际用量、派单成功率及数据覆盖，判断这类任务是否值得继续派。
