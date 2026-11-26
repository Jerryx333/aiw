# Contributing

[Back to README](README.md) | [中文](CONTRIBUTING.zh-CN.md)

Reproducible reports, documentation and offline tests are welcome. Commit messages may be Chinese or English. License: [Apache-2.0](LICENSE).

## Development setup

You need Python 3.11+ and Git. Scripts use only the Python standard library; tests use fake CLIs and isolated HOME, without real model accounts. Only macOS has been tested; Linux is untested.

```bash
git clone https://github.com/Jerryx333/aiw.git
cd aiw
python3 tests/test_harness.py readme_bilingual_headings
bash bin/aiw help
```

Try checkout changes without running `install.sh`, so your installed command, rules and hooks stay in place. Use `bash bin/aiw …` or `python3 <entry>.py …` to target this checkout. For isolated state/configuration:

```bash
mkdir -p .tmp-baseline/dev-state
printf '%s\n' '{}' > .tmp-baseline/dev-seats.json
AIW_CONFIG=.tmp-baseline/dev-seats.json AIW_STATE=.tmp-baseline/dev-state python3 ai_worker.py doctor
```

This isolates runtime state and the local seat override; doctor still checks machine installation. For routing, also point `AIW_ROUTER_RULES` to a test override and check project `.claude/router-rules.json`. Real assignments use your installed CLIs and authentication.

## Code map

Every root Python, Shell and configuration/rule file:

| File | Responsibility |
| --- | --- |
| [ai_worker.py](ai_worker.py) | Assignment execution, roles, sandboxing, fallback, breakers, write locks, archives and doctor entry. |
| [seats_config.py](seats_config.py) | Load, merge and validate public/local seat configuration; compute behavior hashes. |
| [doctor_config.py](doctor_config.py) | Read-only installation/configuration checks, provider registry checks and fingerprints. |
| [route.py](route.py) | Merge project/local/global routing; emit suggestions or hook context. |
| [panel.py](panel.py) | Collect independent read-only opinions concurrently, exclude the main session, preflight attachments, no fallback. |
| [map.py](map.py) | Dispatch read-only work per file concurrently; save ordered batch results. |
| [goal.py](goal.py) | Initialize task lists, select next tasks, verify, report status and stop stalled work. |
| [findings.py](findings.py) | Parse/sanitize observations; append locally under locks, extract signals and emit buffer hints. |
| [triage.py](triage.py) | Build local review batches, validate/apply decisions or abort a batch. |
| [tokens.py](tokens.py) | Read-only usage, context and coverage statistics from Claude Code transcripts and assignment ledger. |
| [weekly.py](weekly.py) | Generate local statistics from the ledger, routing and findings. |
| [track.py](track.py) | Run/register long tasks; save progress, output and process liveness. |
| [slot.py](slot.py) | Machine-wide test slots, memory pressure waiting and background QoS. |
| [tui.py](tui.py) | `aiw tui`: start a Codex main session in tmux whose status bar shows this session's assignments via aiw-board. |
| [snapshot.py](snapshot.py) | Generate local configuration-shape snapshots without credentials to compare drift. |
| [handoff.py](handoff.py) | Generate handoff templates and suggested paths, and validate handoff files, print the absolute path, resumption instructions and a copyable Claude Code command, and accept `--dry-run` as a compatibility flag with no effect. |
| [freecheck.py](freecheck.py) | Probe free seats serially, record disabling and manage scheduled macOS checks. |
| [gc.py](gc.py) | Clean old run/panel/batch and dated Codex session archives; support dry-run. |
| [harness_ids.py](harness_ids.py) | Shared run, record and panel ID formatting/recognition. |
| [gitenv.py](gitenv.py) | Isolate caller Git location variables; resolve repositories by working directory. |
| [env_compat.py](env_compat.py) | Read environment settings and normalize Python/state-path defaults; resolve agent-use to `~/.local/share/agent-use`, retaining an existing `~/code/ai/agent-use` when the new path is absent; adapters default to a sibling of the resolved core directory. |
| [install.sh](install.sh) | Install rules, hooks, command symlink and optional agent-use setup idempotently; back up changed files. |
| [agent_use_setup.sh](agent_use_setup.sh) | Shared ecosystem paths, isolated Git operations and update helpers for installation/sync. |
| [seats.json](seats.json) | Public defaults for seats, roles, fallback, panels and runtime policies. |
| [seats.local.example.json](seats.local.example.json) | Example local seat overrides; not automatically loaded. |
| [router-rules.json](router-rules.json) | Public routing patterns; first match wins. |
| [router-rules.local.example.json](router-rules.local.example.json) | Example machine-local routing overrides. |
| [.coderabbit.yaml](.coderabbit.yaml) | CodeRabbit review language, branch conditions and path-specific rules. |
| [.gitignore](.gitignore) | Exclude local state, snapshots, Python caches and temporary test outputs. |
| [CLAUDE.md](CLAUDE.md) | Public operating rules for Claude main sessions. |
| [AGENTS.harness.md](AGENTS.harness.md) | Public operating rules installed into the Codex global marked block. |

`bin/aiw` dispatches commands; cases live in `tests/cases/` and shared helpers in `tests/support.py`.

## Tests

For daily work, run `python3 tests/test_harness.py --changed` to test affected case modules (compares against `origin/main`, then `main`, including local changes). Before merging, run the full suite through `aiw slot -- python3 tests/test_harness.py`; the harness reuses the slot. `--changed [BASE]` cannot be combined with case names; selections covering at least half the cases also reserve a slot. `--slow SECONDS` reports cases exceeding 5 seconds by default.

```bash
python3 tests/test_harness.py
python3 tests/mutation_check.py
```

Behavior tests cover normal operation; mutation checks confirm tests detect broken invariants. See [test options](docs/reference.zh-CN.md#自测) (Chinese) for concurrency, named cases and mutations. Prefer minimal offline cases with temporary directories, fake CLIs and isolated HOME. Do not access real accounts or weaken assertions. If a sandbox blocks a check, record the command and reason instead of bypassing it or changing tests.

Workspace seat changes need fake agent-use tests for ro/rw arguments, text defaults, configuration conflicts and write fallbacks; verify baselines, ledger entries and file changes in a temporary repository for rw.

## Documentation conventions

Keep README sections corresponding across languages with equal second-level heading counts. Update both languages of guides, best practices and contribution instructions. Explain reader outcomes before mechanisms; make commands copyable, verify behavior against source, give sources/methods for numbers and label unknown products or versions unverified. Public files must not contain credentials, private seats, machine names, local absolute paths or personal account information. Edit relevant paragraphs without reformatting whole files. State-field changes require updates to the state contract and tests.

## PR process

Branch from main, describe the concrete problem and resulting behavior, run relevant checks and submit a PR. Use the template to explain why, verification and bilingual documentation impact. CodeRabbit automatically reviews non-draft PRs targeting main; see [.coderabbit.yaml](.coderabbit.yaml). Address evidence-backed findings and explain declined suggestions before maintainer review. Chinese and English commit messages are both accepted; describe the actual change.

## Contributor references

- [findings.md](docs/findings.md): local observation buffer, signals and review workflow.
- [state-contract.md](docs/state-contract.md): state fields, writer responsibilities, atomic writes and locks.
- [invariants.md](docs/invariants.md): required behaviors and corresponding regression/mutation checks.

## Starting points

Run the README heading case and reproduce a small issue. Clarifying doctor environment warnings, aligning bilingual command examples or adding an offline regression for a confirmed boundary are useful first contributions. Use issue forms and attach a sanitized `aiw doctor --fingerprint`; never include credentials. Report security issues privately as described in [SECURITY.md](SECURITY.md).
