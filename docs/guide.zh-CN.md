# aiw 使用指南

先看 [README](../README.zh-CN.md) 了解它解决什么问题；本文讲安装细节、工作原理和定制。

## 角色与单家安装

使用 `aiw write|review|draft <workdir> <timeout> <prompt>`；角色解析到哪个座位见 `aiw doctor`。

| 主会话身份 | write 候选顺序 | review 候选顺序 | draft 候选顺序 |
|---|---|---|---|
| Claude | codex → claude-write | codex-sol → claude | codex-sol → claude |
| Codex | 空（改文件自己做） | claude → codex-sol | codex-sol → claude |
| 未识别（default） | codex → claude-write | codex-sol → claude | codex-sol → claude |

pi / omp 主会话会自动识别；安装时向 `~/.pi/agent/AGENTS.md` / `~/.omp/agent/AGENTS.md` 写派单规则（`AIW_NO_PI=1` / `AIW_NO_OMP=1` 跳过），不加派单钩子。公开默认三个角色候选为空，需本地配置座位；`main_exclude` 默认禁止派给四个 GPT/Claude 座位，覆盖角色、顶替、panel 和点名派单。

按顺序取首个存在、bin 可找到（尊重 CODEX_BIN/CLAUDE_BIN）、未停用且没有当前 auth/not_installed 熔断的座位。全部不可用返回 rc=2，列出每个候选原因，提示接入 agent-use 文本座位；Codex 主会话 write 列表为空时提示「Codex 主会话改文件请自己做」。review/draft 强制只读，包括写能力的顶替座位。`roles` 本地覆盖对象递归合并、列表整体替换；review/draft 解析时跳过非只读 CLI 候选并说明原因，doctor 同样警告；可使用只读 CLI 或 agent_use 座位（文本/workspace），角色名不能与座位或内置命令冲突。

只装 Codex 时，主会话自己改代码，review/draft 使用另一 Codex 会话。只装 Claude Code 时，write 使用 claude-write，review/draft 使用 claude。两家都装时 review 会落到另一家模型；只装一家时是同一家另开上下文，不等于跨家族复核。

claude 与 claude-write 均固定 `--model opus`，不跟随用户默认模型。claude-write 需要 Claude Code 2.1.259 或更高（`--restricted` 自 2.1.248、`--permission-prompts` 自 2.1.259，见 [CLI 参考](https://code.claude.com/docs/en/cli-reference.md)），写单使用 `--restricted --strict-mcp-config --tools Read,Grep,Glob,Edit,Write,Bash --permission-mode acceptEdits --permission-prompts none`，并传单个 settings JSON：`{"sandbox":{"enabled":true,"allowUnsandboxedCommands":false,"failIfUnavailable":true}}`。受限模式加沙箱，只改项目工作区文件，不能 git add/commit；沙箱不可用时失败。其只读 argv 沿用 claude 的 Read/Grep/Glob 白名单。

**claude-write 已知限制：** Claude Code 沙箱除项目目录外，还允许写每用户临时目录（如 `$TMPDIR`、`/tmp/claude-<uid>`），这是 Claude Code 的设计。项目外其他位置（如主目录）写入会被拒绝。2026-10-13 在 Claude Code 2.1.295 上实测过上述行为：文件工具越界被 `--restricted` 拒绝；shell `cp` 到 `~/` 下报 `Operation not permitted`；`curl` 被拒 403。

panel 默认名单去掉主会话自身后过滤不可用座位，stderr 列原因；不足两家时不派单、rc=1，提示「会诊需要至少两家可用座位；单家安装可接入 agent-use 文本座位」。显式名单同样过滤不可用座位并列原因；显式单家仍展示结果、rc=1。map_default 默认为 draft，也可配置角色；`--seat` 仍可点名座位。


## 安装做了什么

安装脚本会做这些改动：

- `~/.claude/CLAUDE.md`：添加公开规则引入行；个人规则存在时，紧接着引入。保留其他文字。
- `~/.claude/settings.json`：添加或刷新 `UserPromptSubmit` 派单钩子。保留其他设置和钩子。
- 两种钩子模式都在顶层没有 `autoCompactWindow`、且环境变量 `CLAUDE_CODE_AUTO_COMPACT_WINDOW` 未设置时，写入 `"autoCompactWindow": 400000`，作为约 40 万 token 的自动压缩兜底。已有值不改，设 `AIW_NO_AUTOCOMPACT=1` 可跳过。
- `~/.local/bin/aiw`：创建或替换指向本仓库的软链，**不备份**。
- `~/.zshrc`：不改。`~/.local/bin` 不在 PATH 时只打印提示。
- 若存在 `~/.codex` 或 PATH 中有 `codex`：更新 `~/.codex/AGENTS.md` 的 aiw 标记块，以及 `~/.codex/hooks.json` 的钩子。保留块外文字。设 `AIW_NO_CODEX=1` 可跳过。
- 创建仓库的 `state/` 目录，给入口脚本加执行权限，检查路由并运行 doctor。检查可能只警告，不使安装失败。

`--minimal` 只创建 `~/.claude/aiw` 和 `~/.local/bin/aiw` 软链并写 `state/minimal` 标记，不改上述任何文件、不预检 JSON；`aiw doctor` 据标记跳过规则和钩子检查。不带参数重跑即接入并清除标记。

已有文件改动前备份为 `.bak-年月日-时分秒`，新建或未变的不备份。先检查 JSON；重复安装不重复追加。`--claude-hook=plugin` 会移除 settings 中的派单钩子，交给另行管理的插件；它不安装插件。

## 它是怎么工作的

aiw 帮 Claude Code 或 Codex 主会话把活分给不同模型：改代码用 write，只读评审用 review，起草和摘要派可选的便宜或免费模型。主会话规划和验收；Codex 主会话也自己改代码。**座位**就是一个可派单的模型或命令行工具。顶替链让失败的任务换下一家；熔断暂时跳过不可用座位。不是框架或服务：规则文件和仅用标准库的 Python 脚本。

`codex`/`codex-sol` 需 Codex CLI，`claude`/`claude-write` 需 Claude Code；CLI 即终端程序。

- `aiw`：统一的派单命令。
- 派单钩子：在 Claude Code 和 Codex 里提示任务该派给谁，不会自动派单。
- `aiw panel`：多模型会诊，即收集多个座位的独立意见。返回码 0 要求至少两家成功且有产出（`rc == 0` 且 `chars > 0`），不足两家返回 1；默认名单不足两家不派单；显式单家仍执行并展示结果，但不构成会诊。
- `aiw goal`：带可执行验收命令的任务清单。
- 改进缓冲区：保存观察，集中分析根因。
- `aiw doctor`：检查座位安装和配置问题。

```text
主会话 → 派单钩子提示 → 主会话运行 aiw → 座位
                                          ↓ 失败
                                按配置的顶替链换下一家
                                / 暂时熔断
产出 → 主会话核对文件、证据和测试
```

产出都是**待审稿**，主会话要核对实际执行方和改动。附件发送方式与运行记录见[进阶细节](#进阶细节)。

## 接入更多模型（可选）

一键安装：

```bash
~/.claude/aiw/install.sh --with-agent-use
```

它克隆或快进更新公开核心和 [agent-use-adapters](https://github.com/Jerryx333/agent-use-adapters)，运行适配器安装脚本。安装成功后，adapters 的 `opencode` 等 provider 注册完成；要用它做座位，需自行在 `~/.config/aiw/seats.local.json`（或 `AIW_CONFIG`）中添加，示例见本节下方。适配器克隆或安装失败（如已有注册表冲突）时警告，安装流程继续，原注册表保留。CLI 仍需按官方说明安装和登录。此参数可与 `--claude-hook=plugin` 组合。

核心默认位于 `~/.local/share/agent-use`，新路径不存在时沿用已有的 `~/code/ai/agent-use`（`AGENT_USE_HOME` 可覆盖），适配器默认在解析出的核心目录同级 `agent-use-adapters`（`AIW_ADAPTERS_HOME` 可覆盖）。`aiw sync` 会同时快进更新已安装的两个 Git 仓库，失败只警告。`free_only` 是 doctor 的免费标注，`free_guard` 在发现收费证据时停用座位或模型；免费模型选择由适配器负责；目前 OpenCode 模型级停用记录不会过滤 dispatcher 的默认/备选模型。

也可以按以下步骤手工接入：

使用公开的 [agent-use 核心](https://github.com/Jerryx333/agent-use)。若不装在上述默认位置，用 `AGENT_USE_HOME` 指定其仓库位置。

1. 在 `~/.config/agent-use/dispatcher.json` 注册 **provider**，即调用模型/工具的适配器。可用 `examples/cli_adapter.py` 包装已有 CLI，参照其注册表示例。任务送到标准输入，标准输出即答复；工具需另行安装和登录。
2. 在 `~/.config/aiw/seats.local.json` 加匹配的座位，替换 provider 和 model 占位值：

   ```json
   {
     "seats": {
       "local-text": {
         "kind": "agent_use",
         "provider": "your-provider",
         "model": "your-model"
       }
     },
     "fallback": {"local-text": []}
   }
   ```

   如果要使用 adapters 已注册的 `opencode` provider，可自行添加以下文本座位（空 `model` 由适配器选择模型）：

   ```json
   {
     "seats": {
       "opencode": {
         "kind": "agent_use",
         "provider": "opencode",
         "model": "",
         "free_only": true,
         "free_guard": true
       }
     },
     "fallback": {"opencode": []}
   }
   ```

3. 可选：用 `router-rules.local.json` 将起草类任务路由过去；参照[座位示例](../seats.local.example.json)和[路由示例](../router-rules.local.example.json)。
4. 运行 `aiw doctor`。启用座位的 provider 在生效注册表中缺失或被禁用时会警告；这类警告不改变退出码。

`kind=agent_use` 座位可选布尔字段 `workspace`，默认 false（文本派单，不传 `--workdir/--access`，但不会因此硬禁供应商工具；只有配置 `require_no_tools: true` 才传 `--require-no-tools`）。设为 true 时，provider 必须声明 `tool_policy=workspace`；可进入 write 候选，不能同时设 `require_no_tools: true`。未启用 `--text` 时，派单传 `--workdir <绝对路径> --access ro|rw`：`--ro`、`ro_only`、review 使用 ro，其余使用 rw。ro 只能读文件、搜索和列目录，不能编辑或跑命令；rw 开放全部内置工具并跳过确认，不套 `sandbox-exec`，文件直接改在工作目录，调用方用 diff 验收。两种模式均不加载 MCP。附件可选；直接派单给 workspace 座位文件清单，由它自行读取，panel 仍给各家同一份附件全文快照。`--net` 只打印说明、不生效，联网由供应商决定；写单复用 CLI 的 baseline、改动统计、账本与汇报。

`--text` 隐含 `--ro`；workspace 座位改传 `--require-no-tools`，不传 `--workdir/--access`（access=none），附件改为全文；非 workspace agent_use 和 CLI 座位仅增加只读约束。draft 角色和逐文件 map 自动启用，review/write 与 panel 不自动启用；顶替链逐座位应用同一规则，`--text` 与 `--net` 同用返回 64。

## 自定义

本地文件放在 `~/.config/aiw/`：

| 文件 | 管什么 |
| --- | --- |
| `seats.local.json` | 座位、模型、顶替链、会诊名单等本地设置。 |
| `router-rules.local.json` | 哪种任务描述提示派给哪个座位。 |
| `CLAUDE.personal.md` | 在公开 Claude 规则之后引入的个人规则。 |
| `AGENTS.personal.md` | 安装时复制到公开 Codex 规则之后的个人规则。 |

冲突时以个人规则为准。新增个人文件或修改 Codex 规则后重跑 `install.sh`。项目路由放在 `.claude/router-rules.json`，优先级是项目 > 本地 > 公开。座位配置的对象递归合并，列表整体替换。

日常只需调整角色候选、顶替链和会诊名单；可直接从[最佳实践的推荐配置](best-practices.zh-CN.md#推荐配置)开始。自定义 CLI 的 adapter 与 argv 约束见[进阶细节](#进阶细节)。

## 多台机器

每台都克隆同一个公开仓库。本地配置和个人规则放进自己的私有仓库，软链到 `~/.config/aiw/`。比较两台的 `aiw doctor --fingerprint`；登录和运行状态各机独立。

## 卸载

执行卸载脚本（可先加 `--dry-run` 预览）：

```bash
~/.claude/aiw/uninstall.sh
```

脚本删除 aiw 的规则引入、派单钩子、AGENTS 标记块及旧版安装器添加的 PATH 设置，仅删除指向本仓库的软链。改动前备份为 `.bak-*`；JSON 不合法时退出且不改任何文件，可重复运行。仓库和 `state/`、`~/.config/aiw/`、agent-use / agent-use-adapters 及插件钩子均保留；如装过 aiw 插件，请在 Claude Code 里停用。

卸载保留 `~/.claude/settings.json` 中的 `autoCompactWindow`；不再需要自动压缩兜底时可自行删除。

也可手动撤销以下改动，保留其他设置：

- 从 `~/.claude/CLAUDE.md` 删除 aiw 引入行及其个人规则引入行。
- 从 `~/.claude/settings.json` 和已接入时的 `~/.codex/hooks.json` 中，仅删除命令含 `aiw/route.py` 的钩子，保持 JSON 合法；从 `~/.codex/AGENTS.md` 以及存在时的 `~/.pi/agent/AGENTS.md`、`~/.omp/agent/AGENTS.md` 删除 `aiw:begin` 到 `aiw:end` 的整个标记块。
- 删除 `~/.local/bin/aiw` 软链；旧版安装器可能在 `~/.zshrc` 加过 `# aiw: on PATH` 及其 PATH 设置，有则删除。
- 停用另行安装的插件钩子。不再需要时才删除仓库（含本地 `state/`）和本地 aiw 配置。也可用 `.bak-*` 恢复代替手改，但须确认不会丢掉后续修改；命令软链没有备份。

## 进阶细节

### 附件与运行记录

产出都是**待审稿**。成功时，stderr 的「实际执行方」行报告实际座位及 `provider` 文件的实际路径，可核对顶替。默认写在 run 目录；设置 `AIW_PROVIDER_FILE` 时写到指定路径。Git 仓库根目录与子目录的写单共用一把锁。Git 写单开工前保存 `baseline-status.txt`，结束时分列开工前已有改动与本单期间新出现变化的文件。默认有 `codex`、`codex-sol`、`claude`、`claude-write`，顶替链为空，需本地配置。文本座位需显式用 `--attach 文件` 才能看到仓库材料。直接派单的附件按实际座位分发：CLI 或 workspace 座位只收通过安全校验的文件清单（相对路径、字节数、SHA256 前 12 位），在工作目录自行读取；其它座位收内联全文。两种方式均保留 200KB 上限及安全检查；直接派单到文本座位时，附件超过 30KB 只警告一次、不拒绝。panel 预检读取附件一次，将全文渲染进共用 `prompt.md` 快照，各家（含 CLI 座位）收到同一份全文；附件合计超过 30KB 在预检时提醒一次，全 CLI 会诊也适用。附件内容会原样发给对应座位背后的模型服务；安全检查只拦凭据类文件、越界路径和二进制等，不识别正文里的密码、密钥或私人数据，发送前请自行确认。

### 自定义 CLI 适配器

CLI 座位可选 `adapter`：缺省或 `null` 为普通 CLI；目前字符串仅允许 `"codex"`，且仅用于 `kind=cli`。本地 `"adapter": null` 可清除公开配置继承值，部分字段覆盖则继续继承。升级时，本地覆盖会继承公开配置的 adapter；若将 `codex`/`codex-sol` 改为非 Codex 的 CLI 或 `agent_use`，须显式写 `"adapter": null`；继续使用 Codex 时，`argv_rw`/`argv_ro` 须保持 exec 形状。Codex 适配器要求 `argv_rw`、`argv_ro` 的 `argv[0]` 为 `"{bin}"`，第二项为 `"exec"`，各恰好含一个 `"--sandbox"`，`argv_ro` 其后必须为 `"read-only"`，`argv_rw` 可为 `"read-only"` 或 `"workspace-write"`。它启用沙箱/派单提醒、内层 shell 防嵌套、linked worktree 可写根、写单 `--net`、独立 CODEX_HOME 和 token 记账；行为按实际执行座位判断。新增 `codex-high` 等别名时，在本地 `seats` 中复制公开 `codex` 座位完整配置（含 argv），保留 `"adapter": "codex"`，再调整 `effort` 等字段；座位名本身不会启用适配器。看起来是 Codex 命令却未声明 adapter 时，doctor 只警告。
