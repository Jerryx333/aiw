# aiw usage guide

Start with the [README](../README.md) to see what problems it solves; this guide covers installation details, how it works and customization.

## Roles and single CLI installations

Use `aiw write|review|draft <workdir> <timeout> <prompt>`. Run `aiw doctor` to see which seat each role resolves to.

| Main session | write candidates | review candidates | draft candidates |
|---|---|---|---|
| Claude | codex → claude-write | codex-sol → claude | codex-sol → claude |
| Codex | Empty: edit files yourself | claude → codex-sol | codex-sol → claude |
| pi / omp | Empty (configure locally) | Empty (configure locally) | Empty (configure locally) |
| Unknown (default) | codex → claude-write | codex-sol → claude | codex-sol → claude |

Main session detection: valid `AIW_MAIN=claude|codex|pi|omp` > `OMPCODE=1` > `PI_CODING_AGENT=true` > `CLAUDECODE=1`. Codex still needs `AIW_MAIN=codex`. `main_exclude` defaults to `["codex","codex-sol","claude","claude-write"]` for pi/omp. It skips role candidates, fallback seats and panel lists (including explicit lists); direct assignments are rejected with rc=64 and fully excluded role lists return rc=2 with reasons. Like self_seats, objects merge recursively and lists replace in full; optional seat names need not exist locally.

The installer detects pi/omp by directory or command and writes `AGENTS.harness.md` plus optional `~/.config/aiw/AGENTS.personal.md` to `~/.pi/agent/AGENTS.md` / `~/.omp/agent/AGENTS.md`, preserving content outside the marker block. No dispatch hooks are installed. Set `AIW_NO_PI=1` / `AIW_NO_OMP=1` to skip; doctor checks whether detected integrations have current blocks.

Select the first existing seat whose binary is available (respecting CODEX_BIN/CLAUDE_BIN), which is not disabled and has no active auth/not_installed breaker. If none is available, return rc=2 with each candidate's reason and suggest an agent-use text seat. An empty Codex write list asks the main session to edit files itself. review/draft always run read-only, including replacements with write capability. Local roles objects merge recursively; lists replace in full. review/draft skip CLI candidates without read-only capability and report reasons; doctor also warns. Read-only CLI and agent_use seats (text/workspace) are eligible; role names cannot conflict with seats or built-in commands.

With only Codex, the main session edits files and another Codex session reviews/drafts. With only Claude Code, claude-write edits and claude reviews/drafts. With both installed, review selects the other model family; with one, it opens another context in the same family, which is not a cross-family review.

Both Claude seats pin `--model opus` rather than following the user's default. claude-write requires Claude Code 2.1.259 or later (`--restricted` since 2.1.248, `--permission-prompts` since 2.1.259; see the [CLI reference](https://code.claude.com/docs/en/cli-reference.md)). Write jobs use `--restricted --strict-mcp-config --tools Read,Grep,Glob,Edit,Write,Bash --permission-mode acceptEdits --permission-prompts none` and one settings JSON argument: `{"sandbox":{"enabled":true,"allowUnsandboxedCommands":false,"failIfUnavailable":true}}`. Restricted mode plus the sandbox allows project workspace edits, but no git add/commit; unavailable sandbox support fails the job. Read-only jobs use claude's Read/Grep/Glob allowlist.

**Known claude-write limitation:** By design, the Claude Code sandbox also permits writes to per-user temporary directories outside the project, such as `$TMPDIR` and `/tmp/claude-<uid>`. Writes elsewhere outside the project, such as the home directory, are denied. These behaviors were tested on Claude Code 2.1.295 on 2026-10-13: out-of-project file tools were rejected by `--restricted`; shell `cp` to `~/` failed with `Operation not permitted`; `curl` was rejected with 403.

Default panel seats exclude the main session and unavailable seats, reporting reasons on stderr. Fewer than two available seats returns rc=1 without dispatch and suggests adding an agent-use text seat. Explicit lists also filter unavailable seats and report reasons; a single explicit seat still displays its result with rc=1. map_default is draft and accepts roles; --seat still names a seat.


## What the installer changes

The installer makes these changes:

- `~/.claude/CLAUDE.md`: adds the aiw import, then existing personal rules; keeps other text.
- `~/.claude/settings.json`: adds/refreshes the `UserPromptSubmit` hook; keeps other settings/hooks.
- Adds top-level `"autoCompactWindow": 400000` as an automatic compaction fallback in either hook mode, only when the key and `CLAUDE_CODE_AUTO_COMPACT_WINDOW` environment variable are absent. Existing values are preserved. Set `AIW_NO_AUTOCOMPACT=1` to skip this.
- `~/.local/bin/aiw`: creates or replaces a symlink to this checkout, **without a backup**.
- `~/.zshrc`: not touched. If `~/.local/bin` is not on PATH, it only prints a hint.
- If `~/.codex` exists or `codex` is on PATH: updates the aiw block in `~/.codex/AGENTS.md` and the hook in `~/.codex/hooks.json`. Keeps text outside the block. Set `AIW_NO_CODEX=1` to skip this.
- Creates `state/`, makes entry scripts executable, checks routing and runs doctor; warnings may not fail installation.

`--minimal` creates only `~/.claude/aiw` and the `~/.local/bin/aiw` symlink and writes a `state/minimal` marker; it changes none of the files above and skips the JSON preflight. `aiw doctor` reads the marker and skips the rule and hook checks. Rerun without the flag to connect everything and clear the marker.

Changed existing files get `.bak-YYYYMMDD-HHMMSS` backups; new/unchanged files do not. JSON is checked before edits. Reinstalling avoids duplicates. `--claude-hook=plugin` removes the settings hook; manage plugin hooks separately. It does not install a plugin.

## How it works

aiw shares work from Claude Code or Codex: code uses write, reviews use review, drafts and summaries to optional cheap/free models. The main session plans/checks results; Codex also edits code. A **seat** is a model or CLI that takes assignments. Fallback tries another seat; circuit breakers temporarily skip unavailable seats. Not a framework or service: rule files and standard-library Python scripts.

`codex`/`codex-sol` need Codex CLI; `claude`/`claude-write` need Claude Code. A CLI runs in a terminal.

- `aiw`: assign work.
- Dispatch hooks: suggest seats inside Claude Code/Codex; do not assign automatically.
- `aiw panel`: independent opinions (a “panel”). Exit code 0 requires at least two successful seats with output (`rc == 0` and `chars > 0`); fewer return 1. Default lists with fewer than two available seats do not dispatch. An explicit single seat still runs and its result is displayed, but is not a panel.
- `aiw goal`: a task list with executable checks.
- An improvement buffer: observations for later root-cause review.
- `aiw doctor`: installation/configuration checks.

```text
Main session → dispatch hook suggestion → main session runs aiw → seat
                                                               ↓ failure
                                               next configured fallback
                                               / temporary circuit breaker
Result → main session checks files, evidence and tests
```

All output is a **draft for review**. Check the actual seat and changes in the main session. Attachment handling and execution records are covered in [Advanced details](#advanced-details).

## Add more models (optional)

One-command setup:

```bash
~/.claude/aiw/install.sh --with-agent-use
```

This clones or fast-forwards the public core and [agent-use-adapters](https://github.com/Jerryx333/agent-use-adapters), and runs the adapter installer. A successful install registers providers such as `opencode` through adapters. To use it as a seat, add it yourself in `~/.config/aiw/seats.local.json` (or `AIW_CONFIG`); see the examples below. Adapter clone or install failures (such as registry conflicts) warn while the overall install continues and the existing registry is preserved. Install and log in to the CLI separately as required. Combine this option with `--claude-hook=plugin` if needed.

The core defaults to `~/.local/share/agent-use`, retaining an existing `~/code/ai/agent-use` when the new path is absent (`AGENT_USE_HOME` overrides it); adapters default to the resolved core directory’s sibling `agent-use-adapters` (`AIW_ADAPTERS_HOME` overrides it). `aiw sync` also fast-forwards both installed Git repositories; their failures only warn. `free_only` labels the seat in doctor; `free_guard` disables a seat or model on evidence of charges. Free model selection is handled by the adapter; OpenCode model-level disable records currently do not filter dispatcher default/fallback models.

For manual setup, follow these steps:

Use the public [agent-use core](https://github.com/Jerryx333/agent-use). Set `AGENT_USE_HOME` to its checkout if it is outside these default locations.

1. Register a **provider** (a named adapter that calls a model/tool) in `~/.config/agent-use/dispatcher.json`. A simple starting point is agent-use's `examples/cli_adapter.py`, to wrap a CLI. Follow its registry example: task text goes to stdin, stdout becomes the response. Install/login separately.
2. Add a seat to `~/.config/aiw/seats.local.json`, replace provider/model placeholders:

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

   If you want to use the `opencode` provider registered by adapters, add this text seat yourself (an empty `model` lets the adapter select the model):

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

3. Optionally route drafting tasks there using `router-rules.local.json`; see [the seat example](../seats.local.example.json) and [routing example](../router-rules.local.example.json).
4. Run `aiw doctor`. It warns if an enabled seat's provider is missing or disabled in the active registry; these warnings do not change its exit code.

`kind=agent_use` seats accept the boolean `workspace`, default false (text assignments without `--workdir/--access`; this does not hard-disable provider tools, and only `require_no_tools: true` adds `--require-no-tools`). With true, the provider must declare `tool_policy=workspace`; the seat can be a write candidate and cannot also set `require_no_tools: true`. Without `--text`, assignments pass `--workdir <absolute path> --access ro|rw`: `--ro`, `ro_only` and review use ro; other jobs use rw. ro allows only file reads, search and directory listing, with no edits or commands. rw enables all built-in tools and bypasses confirmation, without `sandbox-exec`; changes land directly in the working directory for the caller to check with diff. Neither mode loads MCP. Attachments are optional; direct workspace assignments receive a file manifest and read files themselves, while panels retain the same inline snapshot for all seats. `--net` prints a notice and has no effect; the provider decides network access. Write jobs share CLI baselines, change counts, ledger fields and reports.

`--text` implies `--ro`; workspace seats receive `--require-no-tools` without `--workdir/--access` (access=none), with attachments inline. For non-workspace agent_use and CLI seats, it only adds read-only mode. The draft role and per-file map enable it automatically; review/write and panel do not. The same rule applies to every fallback seat; combining `--text` with `--net` returns 64.

## Customize

Local files live under `~/.config/aiw/`:

| File | Controls |
| --- | --- |
| `seats.local.json` | Seats, models, fallback chains, panel lists and other local settings. |
| `router-rules.local.json` | Which task descriptions suggest which seats. |
| `CLAUDE.personal.md` | Personal instructions imported after public Claude rules. |
| `AGENTS.personal.md` | Personal instructions copied after public Codex rules during installation. |

Personal rules override conflicts. Rerun `install.sh` after adding personal files or changing Codex personal rules. Project routing goes in `.claude/router-rules.json`: project > local > public. Seat configuration recursively merges objects; lists replace whole lists.

Codex/Claude seats use your own accounts and their billing. `free_only` labels a seat as free-only and declares that it should not switch to paid models; automatic disabling requires `free_guard`, which disables a provider/model when reported charging is detected. These fields, along with `ro_only`, `require_no_tools` and `expects_credits`, must be booleans when present. This is detection after a call, not a guarantee of zero cost.

For everyday use, adjust role candidates, fallback chains and panel lists; start with the [recommended configuration](best-practices.md#recommended-configuration). Custom CLI adapter and argv requirements are in [Advanced details](#advanced-details).

## Multiple machines

Clone the same public repository on each machine. Keep settings/rules in your own private repository; symlink into `~/.config/aiw/`. Compare `aiw doctor --fingerprint` on both; login and runtime state stay local.

## Uninstall

Run the uninstall script (add `--dry-run` to preview):

```bash
~/.claude/aiw/uninstall.sh
```

It removes aiw imports, assignment hooks, AGENTS blocks and the PATH entry an older installer may have added, and deletes only symlinks pointing to this checkout. Changes get `.bak-*` backups; invalid JSON stops all changes. It is safe to run again. The checkout, `state/`, `~/.config/aiw/`, agent-use / agent-use-adapters and plugin hooks are preserved. If you installed the aiw plugin, disable it in Claude Code.

The uninstaller preserves `autoCompactWindow` in `~/.claude/settings.json`; you can delete it yourself if you no longer need the automatic compaction fallback.

You can also undo these changes manually; keep unrelated settings:

- Remove the aiw import and its personal-rule import from `~/.claude/CLAUDE.md`.
- Remove only hooks whose command contains `aiw/route.py` from `~/.claude/settings.json` and, if installed, `~/.codex/hooks.json`; keep valid JSON. Remove the `aiw:begin` through `aiw:end` block from `~/.codex/AGENTS.md`, `~/.pi/agent/AGENTS.md`, and `~/.omp/agent/AGENTS.md` when present.
- Remove the `~/.local/bin/aiw` symlink and the `# aiw: on PATH` block from `~/.zshrc` if an older installer added it.
- Stop any separately installed plugin hook. Delete the checkout (including local `state/`) and local aiw settings only when no longer needed. Restore `.bak-*` files only if no later changes would be lost; the symlink has no backup.

## Advanced details

### Attachments and execution records

All output is a **draft for review**. On success, stderr reports the actual seat and the actual `provider` file path. The default location is the run directory; setting `AIW_PROVIDER_FILE` writes it to the specified path. Write jobs share a lock across the Git repository root and its subdirectories. Git write jobs save `baseline-status.txt` before execution and report pre-existing changes separately from files with new changes during the job. Defaults contain `codex`, `codex-sol`, `claude`, and `claude-write`, with empty fallback chains. Add local chains to enable fallback. Text seats need `--attach file` to see code. For direct jobs, attachments go to the actual seat: CLI or workspace seats receive only a validated file list (relative path, byte count, first 12 SHA256 hex digits) and read files in the workdir; other seats receive inline text. Both modes retain the 200KB cap and safety checks. Above 30KB, direct jobs to text seats trigger one warning without rejection. Panel reads attachments once during preflight and appends inline text to the shared `prompt.md` snapshot; every seat, including CLI seats, receives the same full contents. Panel warns once when total attachments exceed 30KB, including panels with only CLI seats. Attachment contents are sent as-is to the model service behind the seat. Safety checks block credential-like files, out-of-tree paths, binaries and similar cases, but do not detect passwords, keys or private data inside file text; check before sending.

### Custom CLI adapters

CLI seats accept an optional `adapter`. Omitted or `null` means an ordinary CLI; the only allowed string is `"codex"`, restricted to `kind=cli`. A local `"adapter": null` clears the inherited value; partial overrides retain it. When upgrading, local overrides inherit the public configuration's adapter: switching `codex`/`codex-sol` to a non-Codex CLI or `agent_use` requires an explicit `"adapter": null`; when continuing to use Codex, keep `argv_rw`/`argv_ro` in exec form. Both `argv_rw` and `argv_ro` must have `"{bin}"` as `argv[0]`, `"exec"` as their second item and exactly one `"--sandbox"` followed by `"read-only"` for `argv_ro`, or `"read-only"`/`"workspace-write"` for `argv_rw`. The Codex adapter enables sandbox/task reminders, inner shell nesting protection, linked worktree writable roots, write-job `--net`, isolated CODEX_HOME and token ledger fields, based on the actual seat. To add an alias such as `codex-high`, copy the complete public `codex` seat (including argv) into local `seats`, keep `"adapter": "codex"`, then adjust fields such as `effort`. The name alone enables no adapter behavior. Doctor warns when a command resembles Codex but has no adapter.
