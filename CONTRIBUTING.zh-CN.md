# 参与贡献

[返回 README](README.zh-CN.md) | [English](CONTRIBUTING.md)

欢迎补充可复现的问题、文档和离线测试。提交信息中文、英文都接受；许可证是 [Apache-2.0](LICENSE)。

## 开发环境

需要 Python 3.11+ 和 Git；脚本只用 Python 标准库，测试使用假 CLI 和隔离 HOME，不需要真实模型账号。项目目前只测试过 macOS，Linux 未测试。

```bash
git clone https://github.com/Jerryx333/aiw.git
cd aiw
python3 tests/test_harness.py readme_bilingual_headings
bash bin/aiw help
```

不运行 `install.sh` 就能试 checkout 里的改动，不会替换你已安装的命令、规则或钩子。用 `bash bin/aiw …` 或 `python3 <入口>.py …` 指向本 checkout；需独立状态与配置时：

```bash
mkdir -p .tmp-baseline/dev-state
printf '%s\n' '{}' > .tmp-baseline/dev-seats.json
AIW_CONFIG=.tmp-baseline/dev-seats.json AIW_STATE=.tmp-baseline/dev-state python3 ai_worker.py doctor
```

这隔离运行状态和本地座位覆盖；doctor 仍检查本机安装。若测试路由，还应设置 `AIW_ROUTER_RULES` 指向测试覆盖文件，并检查项目 `.claude/router-rules.json`。真实派单会使用你已安装的 CLI 和登录状态。

## 代码地图

根目录的每个 Python、Shell 和配置/规则文件：

| 文件 | 职责 |
| --- | --- |
| [ai_worker.py](ai_worker.py) | 派单执行、角色解析、沙箱、顶替、熔断、写锁、归档及 doctor 入口。 |
| [seats_config.py](seats_config.py) | 加载公开与本地座位配置，合并、校验与行为哈希。 |
| [doctor_config.py](doctor_config.py) | 只读安装/配置检查、provider 注册表检查和指纹。 |
| [route.py](route.py) | 合并项目/本机/全局路由，输出派单建议或钩子上下文。 |
| [panel.py](panel.py) | 并行收集独立只读意见，排除主会话、预检附件，不顶替。 |
| [map.py](map.py) | 按文件并发派只读任务，保存有序批处理结果。 |
| [goal.py](goal.py) | 任务清单初始化、下一条、验收、状态和无进展保险丝。 |
| [findings.py](findings.py) | 解析与清洗观察记录，本机加锁追加、信号抽取和缓冲区提示。 |
| [triage.py](triage.py) | 建立本机评审批次，校验并应用决定，或撤销评审批次。 |
| [tokens.py](tokens.py) | 只读统计 Claude Code 转录及派单账本的用量、上下文与覆盖。 |
| [weekly.py](weekly.py) | 从本机账本、路由及 findings 生成统计周报。 |
| [track.py](track.py) | 运行并登记长任务，保存进度、输出和进程存活信息。 |
| [slot.py](slot.py) | 全机测试名额、内存压力等待及后台 QoS。 |
| [tui.py](tui.py) | `aiw tui`：在 tmux 里启动 Codex 主会话，底栏通过 aiw-board 显示本会话派单摘要。 |
| [snapshot.py](snapshot.py) | 生成不含凭据的本机配置形状快照，检查漂移。 |
| [handoff.py](handoff.py) | 生成交接模板与建议路径，校验交接文件并打印绝对路径，供新会话读取。 |
| [freecheck.py](freecheck.py) | 串行探测免费座位、记录停用并管理 macOS 定时检查。 |
| [gc.py](gc.py) | 清理旧运行/会诊/批处理及 Codex 日期会话归档，支持预览。 |
| [harness_ids.py](harness_ids.py) | 统一运行、记录与会诊 ID 的格式和识别。 |
| [gitenv.py](gitenv.py) | 隔离调用方的 Git 定位环境变量，按工作目录访问仓库。 |
| [env_compat.py](env_compat.py) | 读取环境变量并处理 Python、状态路径及 agent-use 默认值（`~/.local/share/agent-use`，新路径不存在时沿用已有的 `~/code/ai/agent-use`）；适配器默认在解析出的核心目录同级。 |
| [install.sh](install.sh) | 幂等安装规则、钩子、命令软链及可选 agent-use 接入，备份已有文件。 |
| [agent_use_setup.sh](agent_use_setup.sh) | 安装脚本与 sync 共用的生态仓库路径、Git 隔离及更新函数。 |
| [seats.json](seats.json) | 公开座位、角色、顶替、会诊和运行策略默认值。 |
| [seats.local.example.json](seats.local.example.json) | 本地座位覆盖示例，不是自动读取的配置。 |
| [router-rules.json](router-rules.json) | 公开派单匹配规则，第一条命中即停。 |
| [router-rules.local.example.json](router-rules.local.example.json) | 本机路由覆盖示例。 |
| [.coderabbit.yaml](.coderabbit.yaml) | CodeRabbit 自动评审语言、分支条件和各路径评审规则。 |
| [.gitignore](.gitignore) | 排除本机状态、快照、Python 缓存与测试临时产物。 |
| [CLAUDE.md](CLAUDE.md) | Claude 主会话使用的公开分工规则。 |
| [AGENTS.harness.md](AGENTS.harness.md) | 安装到 Codex 全局标记块的公开分工规则。 |

命令分发入口是 `bin/aiw`；用例在 `tests/cases/`，公共测试辅助在 `tests/support.py`。

## 测试

日常运行 `python3 tests/test_harness.py --changed`，只跑改动影响的用例模块（默认比较 `origin/main`，其次 `main`，包含本地改动）。合并前经 `aiw slot -- python3 tests/test_harness.py` 跑全量；harness 会复用名额。`--changed [BASE]` 不能与用例名混用，选择达到总用例数一半时也会拿名额；`--slow 秒` 默认提示超过 5 秒的用例。

```bash
python3 tests/test_harness.py
python3 tests/mutation_check.py
```

测试覆盖正常行为，变异检查确认破坏红线时测试真的能发现。并发、指定用例和变异选项见[参考手册「自测」](docs/reference.zh-CN.md#自测)。新增行为优先补最小离线用例，用临时目录、假 CLI 与隔离 HOME；不要访问真实账号或改弱断言。若受沙箱限制，记录命令和失败原因，不绕过或改测试迁就。

workspace 座位变更须用假 agent-use 入口验证 ro/rw 参数、文本默认行为、配置冲突及写单顶替；rw 测试在临时仓库验收 baseline、账本与文件变化。

## 文档约定

README 中英逐节对应，二级标题数一致；指南、最佳实践与贡献指南同步维护。先讲读者能得到什么，再解释机制；命令可复制运行，行为按源码核实，数字附测法与出处，未知产品或版本标「未核实」。公开仓库不写凭据、私人座位、机器名、本机绝对路径或个人账号信息。只修改相关段落，不全文件重排；状态字段变化同步更新状态约定和相应测试。

## PR 流程

从 main 建分支，说明具体问题与最终行为，跑相关检查后走 PR，使用模板交代原因、验证和双语文档影响。面向 main 的非草稿 PR 由 CodeRabbit 自动评审，设置见 [.coderabbit.yaml](.coderabbit.yaml)；处理有证据的意见，未采纳的说明理由，再由维护者审阅。提交信息中文英文都可，描述实际改动即可。

## 贡献者参考

- [findings.md](docs/findings.md)：本机观察缓冲区、信号与评审流程。
- [state-contract.md](docs/state-contract.md)：状态字段、写入责任、原子写入和锁约定。
- [invariants.md](docs/invariants.md)：不能破坏的行为及守护/变异测试对应关系。

## 从哪里开始

先运行 README 标题用例，复现一个小问题；补充 doctor 环境警告解释、对齐双语命令示例，或为已确认边界补离线回归用例都适合新人。提 issue 时使用表单，附脱敏的 `aiw doctor --fingerprint`，不要贴凭据；安全问题按 [SECURITY.md](SECURITY.md) 私密报告。
