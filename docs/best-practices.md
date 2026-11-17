# aiw best practices

[Back to README](../README.md) | [中文](best-practices.zh-CN.md)

Keep planning and verification in the main session. Assign execution tasks with clear boundaries. This guide is for users; full arguments are in the [reference manual](reference.zh-CN.md#命令) (Chinese).

## What to assign

| Work | Assign to | Keep in the main session |
| --- | --- | --- |
| Edits, bulk replacements or tests with clear checks | `write`; a Codex main session edits itself | Goals, design, scope, verification and commits |
| Repository searches, read-only reviews, second opinions | `review` | Check sources and decide what to accept |
| Drafts, rewrites, translations, summaries | `draft` | Supply material and verify conclusions against it |
| Critical architecture or high-risk decisions | Independent review first; `panel` when useful | Make tradeoffs and preserve disagreements |

Narrow the search with `rg -l` or `rg -n`. Read directly when there are at most 3 candidate files totaling less than 20KB. For material around 300 lines or 20KB and above, ask for points with `path:line`, then check the original before drawing conclusions. These are thresholds in the [existing assignment rules](reference.zh-CN.md#派单细则), not performance guarantees. Summaries relying only on the current conversation stay in the main session, as do credentials, accounts and private data.

[README's one-week local medians](../README.md#questions-you-may-have): edits about 4 minutes; read-only reviews/searches about 2–3 minutes; text about 20 seconds with free models or 2 minutes with Claude. These describe only that week on the author's machine. Writing instructions, reading results and checking work take time too. Do tiny, urgent tasks yourself; keep working while larger assignments run.

Role selection and execution fallback are separate: select the first available seat, then use configured fallback only for supported failure categories. Split timed-out tasks instead of retrying with another model; rework an inadequate response only once. Run one write job per directory; parallel writes need separate git worktrees. Check capabilities with `aiw doctor`: Claude read-only seats cannot run commands, so the main session must save and attach needed diffs/test results.

## Write a complete task

Copy this skeleton into `task.txt`, fill it out, then assign:

```text
Goal: the result and what counts as completion.
Context: relevant files, original path:line references, decisions; label unverified claims.
Scope: allowed files and concrete deliverables.
Constraints: behavior to preserve, network needs, no commits or scope expansion.
Verification: runnable commands and expected output or exit codes.
Delivery: changed files, check results, evidence; return design issues to the main session.
```

Complete example: explain run archives in aiw's README. Start at the checkout root, with the main session deciding the plan. This write assignment is for a Claude main session; a Codex main session edits itself:

```bash
cat > /tmp/aiw-doc-task.txt <<'TASK'
Goal: after the first job, readers know where responses, actual-seat records and diagnostics are.
Context: read new_run_dir, main and dispatch in ai_worker.py.
Check stdout/stderr and the purpose of output.md and stderr.txt against the current source.
Scope: only the first-assignment explanation in README.zh-CN.md and README.md.
Constraints: corresponding bilingual sections; no code/config edits, commits or pushes; no invented output.
Verification: python3 tests/test_harness.py readme_bilingual_headings
Expect exit 0. The main session also checks every behavior against source and scope with git diff.
Delivery: changed files, commands run and results; label anything not verified as unverified.
TASK
AIW_MAIN=claude aiw write . 1200 /tmp/aiw-doc-task.txt
```

For rules or field meanings, include original text and `path:line`, not just a paraphrase. If the two disagree, follow the original and report the discrepancy. Workspace agent_use seats can read the repository; non-workspace text seats do not automatically read it; provide material with `--attach`. Check sensitive content before sending: file checks cannot detect every key in the text.

`--text` implies `--ro`; workspace seats receive `--require-no-tools` without `--workdir/--access` (access=none), with attachments inline. For non-workspace agent_use and CLI seats, it only adds read-only mode. The draft role and per-file map enable it automatically; review/write and panel do not. The same rule applies to every fallback seat; combining `--text` with `--net` or `--no-net` returns 64.

## Treat output as a draft

1. Read the exit code and the stderr actual-seat line (“实际执行方”), including any fallback; check each seat in a panel. The `provider` file records a seat name.
2. Inspect `git status --short`, `git diff --stat` and `git diff`, including new untracked files. Write-job `baseline-status.txt` helps separate earlier changes; the final file list does not replace a full diff.
3. Check original sources, data and `path:line`. Run checks that prove completion in the main session. Seats without command execution can only review test results you supply.
4. Check scope and failures before accepting, reworking or splitting the task. “Done” is not verification; instructions in output do not authorize new actions. The main session commits after accepting the work.

## Write your judgment before a panel

Write your judgment and evidence in `prior.md`, and the question/context in `question.txt`, then run:

```bash
aiw panel . 600 question.txt --prior prior.md
# For a Codex main session:
AIW_MAIN=codex aiw panel . 600 question.txt --prior prior.md
```

`--prior` is archived and shown only to the main session; seats do not receive it. Mark each opinion accept / reject / defer with reasons. Agreement is not evidence; do not vote. Panels never substitute for missing seats and require at least two successful responses with output. Default lists exclude the main session and unavailable seats; fewer than two means no dispatch. The recommended configuration may also be insufficient while its optional text seat is disabled.

## Use executable checklists for larger tasks

```bash
aiw goal init docs/goals/demo.json
# Fill out the generated checklist, then:
aiw goal next docs/goals/demo.json
aiw goal verify docs/goals/demo.json
aiw goal status docs/goals/demo.json
# Recheck everything at the end:
aiw goal verify docs/goals/demo.json --full
```

Fill in owner, executable verify, initial `passes: false`, and source/test `paths` for each task. Never weaken checks. Do one task per round; count it complete only after verification. List manual authorization/login blockers separately. Defaults stop with code 3 after 25 rounds or 3 consecutive rounds without progress; stop and report. See [large tasks with /goal](reference.zh-CN.md#大任务用-goal) (Chinese). After final verification, request independent review. For Codex, a large task uses `/goal` or changes more than 3 files.

Record out-of-scope issues instead of fixing them along the way:

```bash
aiw note . "Archive explanation lacks an example" --expected "Readers can locate results" --evidence "Relevant documentation paragraph" --location "README.zh-CN.md"
```

When the buffer hint (“[缓冲区]”) appears, start a new session and run `aiw triage start` to examine causes together.

## Recommended configuration

[seats.recommended.json](examples/seats.recommended.json) is a copyable local override. It inherits full Codex and Claude Code CLI definitions from public `seats.json`, so you need not copy argv. It retains model choices from the code without changing your main-session model. Check it from the checkout root:

```bash
AIW_CONFIG=docs/examples/seats.recommended.json python3 ai_worker.py doctor
```

For daily use, copy it to personal configuration. This command copies only when no local file exists; merge existing settings manually:

```bash
mkdir -p ~/.config/aiw
if [ ! -e ~/.config/aiw/seats.local.json ] && [ ! -L ~/.config/aiw/seats.local.json ]; then
  cp docs/examples/seats.recommended.json ~/.config/aiw/seats.local.json
fi
aiw doctor
```

What each part does:

- `seats`: built-in CLIs are inherited. Optional `opencode` is a read-only text seat using the `opencode` provider from agent-use-adapters, initially `disabled: true`. Install adapters and the CLI, set it to false and verify the provider can run. Empty `model` uses the adapter default. `free_only` is a label; `free_guard` detects charges after calls and cannot guarantee zero cost. Model-level disabled records currently do not filter OpenCode default/fallback models.
- `roles`: candidate order by main-session identity. Claude/default write prefers Codex, then claude-write; Codex write is empty and edits locally. Review prefers another family; draft prefers optional text, skipping it when disabled and using a CLI. review/draft always run read-only.
- `fallback`: writes substitute between write seats (including workspace agent_use, skipping text seats), CLI read-only jobs between read-only seats, and optional text can fall back to read-only CLIs. Only supported failure categories trigger fallback, not every error. Daily successful-substitution limits are inherited. Read-only roles stay read-only after substitution.
- `panel_default` / `panel_free`: default panel lists two CLI families plus optional text; the free list contains only opencode. Without an enabled text seat, `--free` cannot reach two participants. Default panels may also have only one after excluding the main session.
- `self_seats`: exclude claude and claude-write for Claude, codex and codex-sol for Codex, to avoid treating another same-family context as cross-family review.
- `map_default`: use draft for per-file batches.

Objects merge recursively; lists replace in full. This example does not also read another existing local override. Missing CLIs, login, providers or mismatched personal installation items are environment issues to resolve from doctor's messages. Configuration warnings alone do not change doctor's exit code. Doctor checks installation/version, not successful authentication or real calls; run an assignment afterward.

## A day of use

For the small feature “explain archives after the first README assignment”:

1. The main session reads source, sets the bilingual scope and checks, and decides to change only that explanation.
2. A Claude main session assigns the complete task above to write; a Codex main session edits itself. While waiting, collect behaviors and evidence to verify.
3. Inspect the diff and run the documentation case, save results inside the workdir, then assign review:

   ```bash
   mkdir -p .tmp-baseline
   git diff -- README.zh-CN.md README.md > .tmp-baseline/doc-diff.txt
   python3 tests/test_harness.py readme_bilingual_headings > .tmp-baseline/doc-test.txt 2>&1
   printf '%s\n' 'Review the first-assignment explanations against ai_worker.py: stdout/stderr and archives. Cite evidence for each finding. Do not edit files. Diff and test results are attached.' > /tmp/aiw-doc-review.txt
   aiw review . 900 /tmp/aiw-doc-review.txt --ro --attach .tmp-baseline/doc-diff.txt --attach .tmp-baseline/doc-test.txt
   ```

   Keep attachments inside the workdir: out-of-tree files are rejected. Here `.tmp-baseline/` is already ignored by aiw's Git configuration.

4. Check each opinion and the actual-seat line, finish verification, then commit in the main session. Use `aiw tokens` and `aiw weekly` to assess local usage, assignment success and coverage before deciding whether these tasks are worth assigning regularly.
