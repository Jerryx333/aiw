#!/usr/bin/env python3
"""Isolated harness tests: all cases run in parallel; named cases run in order.

    python3 tests/test_harness.py [-j N] [--durations] [--slow SECONDS] [case ...]
    python3 tests/test_harness.py [--changed [BASE]] [-j N] [--slow SECONDS]
"""
from __future__ import annotations

import argparse
from contextlib import nullcontext
from concurrent.futures import ThreadPoolExecutor
import importlib
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from support import fake_agent_use, fake_bins, write_legacy_local
from select_cases import changed_files, select_from_repo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import slot


CASE_MODULES = (
    "roles",
    "forward",
    "workspace",
    "worker",
    "worker_2",
    "worktree_gc",
    "route_panel_map",
    "dispatch_switch",
    "install_doctor",
    "track_snapshot",
    "slot",
    "goal",
    "misc",
    "free_guard",
    "weekly_tokens",
    "config_errands",
    "findings",
    "triage",
    "codex_home",
    "tui",
    "select_cases",
)
CASES = {}
MODULE_CASES = {}
for module_name in CASE_MODULES:
    module = importlib.import_module(f"cases.{module_name}")
    duplicates = CASES.keys() & module.CASES.keys()
    if duplicates:
        raise ValueError(f"Duplicate cases: {sorted(duplicates)}")
    CASES.update(module.CASES)
    MODULE_CASES[module_name] = tuple(module.CASES)

# This guard checked the complete registry in the original single-file suite.
# Bind its module global after aggregation without changing the guard assertion.
sys.modules["cases.misc"].CASES = CASES

# Audit: fixture writes use TemporaryDirectory / fake HOME; no fixed ports,
# shared /tmp files, or mutable repository files. Add discovered conflicts here.
SERIAL_CASES = frozenset()


def default_jobs():
    return max(1, (os.cpu_count() or 1) // 4)


def clamp_jobs(jobs):
    limit = max(1, (os.cpu_count() or 1) // 2)
    if jobs > limit:
        print(f"Concurrency clamped: -j {jobs} -> {limit} (half the CPU count)",
              file=sys.stderr)
    return min(jobs, limit)


def lower_priority():
    slot.lower_to()


def run_named(names, durations):
    failed = 0
    for n in names:
        started = time.monotonic()
        try:
            with tempfile.TemporaryDirectory() as isolated:
                saved_env = dict(os.environ)
                try:
                    for key in ("CODEX_THREAD_ID", "AIW_TUI_ID", "TMUX", "CLAUDE_CODE_SESSION_ID", "CODEX_HOME", "AIW_CODEX_SHARED_HOME", "XDG_CONFIG_HOME", "AGENT_USE_CONFIG", "AIW_CONFIG", "AIW_FORWARD", "AIW_NO_FORWARD", "AIW_FORWARD_MODE", "AIW_FORWARD_RUN_DIR", "AIW_ROUTER_RULES", "AGENT_USE_ERRAND_HOME", "CODEX_BIN", "CLAUDE_BIN", "AIW_PROVIDER_FILE", "OMPCODE", "PI_CODING_AGENT", "AIW_NO_PI", "AIW_NO_OMP", "AIW_MAIN", "AIW_NESTED", "CLAUDECODE", "AIW_NO_CODEX", "AIW_PANEL_GRACE", "DISCORD_TOKEN", "AIW_DISCORD_USE_BIN", "AIW_DISCORD_CHANNEL", "AIW_ZEN_MODELS_URL", "AIW_LAUNCH_AGENTS_DIR"):
                        os.environ.pop(key, None)
                    root = Path(isolated)
                    os.environ.update(HOME=str(root), AIW_STATE=str(root / "state"),
                                      AIW_SLOT_DIR=str(root / "slots"),
                                      PATH=f"{fake_bins(root)}:{os.environ['PATH']}",
                                      AGENT_USE_HOME=str(fake_agent_use(root)))
                    local = root / "test-seats.json"
                    write_legacy_local(local, {})
                    if n not in {"local_config_contract", "local_config_empty_defaults", "public_config_contract"}:
                        os.environ["AIW_CONFIG"] = str(local)
                    CASES[n]()
                finally:
                    os.environ.clear()
                    os.environ.update(saved_env)
            print(f"PASS {n}")
        except Exception as e:
            failed += 1
            print(f"FAIL {n}: {e}")
        durations.append((n, time.monotonic() - started))
    return 1 if failed else 0


def run_child(name):
    started = time.monotonic()
    try:
        command = [sys.executable, str(Path(__file__).resolve()), name]
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            preexec_fn=lower_priority,
        )
        return name, result.returncode, result.stdout, time.monotonic() - started
    except Exception as exc:
        return name, 1, f"{type(exc).__name__}: {exc}\n", time.monotonic() - started


def run_parallel(jobs, durations, names=None):
    names = sorted(CASES if names is None else names)
    parallel = [name for name in names if name not in SERIAL_CASES]
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        results = list(pool.map(run_child, parallel))
    # Conflicting cases run only after every parallel child has exited.
    results.extend(run_child(name) for name in names if name in SERIAL_CASES)
    failed = 0
    for name, returncode, output, elapsed in sorted(results):
        durations.append((name, elapsed))
        if returncode:
            failed += 1
            print(f"FAIL {name}: child exited {returncode}", flush=True)
            print(output, end="" if output.endswith("\n") else "\n", flush=True)
        else:
            print(f"PASS {name}", flush=True)
    return 1 if failed else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-j", type=int, default=default_jobs(),
                        help="parallel children (default: quarter of CPUs; capped at half)")
    parser.add_argument("--durations", action="store_true", help="show slowest 15 cases")
    parser.add_argument("--changed", nargs="?", const="", metavar="BASE",
                        help="run affected case modules (default base: origin/main, then main)")
    parser.add_argument("--slow", type=float, default=5,
                        help="report up to 10 cases exceeding this many seconds (default: 5)")
    parser.add_argument("cases", nargs="*", help="named cases run serially in the given order")
    args = parser.parse_args(argv)
    if args.changed is not None and args.cases:
        print("--changed 不能与用例名同时使用", file=sys.stderr)
        return 64
    # An optional BASE consumes the first positional token; recognize registered
    # case names here too, so '--changed case_name' cannot launch the full suite.
    if args.changed in CASES:
        print("--changed 不能与用例名同时使用", file=sys.stderr)
        return 64
    if args.j < 1:
        parser.error("-j must be at least 1")
    if not math.isfinite(args.slow) or args.slow < 0:
        parser.error("--slow must be a finite nonnegative number")
    args.j = clamp_jobs(args.j)
    selected_names = None
    needs_slot = not args.cases
    if args.changed is not None:
        root = Path(__file__).resolve().parents[1]
        changed, fallback = changed_files(root, args.changed or None)
        selection = select_from_repo(root, changed, CASE_MODULES) if changed and not fallback else None
        fallback = fallback or (selection.fallback if selection else None)
        if fallback:
            modules = list(CASE_MODULES)
        else:
            modules = list(selection.reasons) if selection else []
        selected_names = [name for module in modules for name in MODULE_CASES[module]]
        print(f"改动文件数：{len(changed)}", file=sys.stderr)
        if fallback:
            print(f"退回全量：{fallback}", file=sys.stderr)
        for module in modules:
            triggers = selection.reasons.get(module, changed) if selection else changed
            print(f"  {module} ← {', '.join(triggers[:3]) or '全量兜底'}", file=sys.stderr)
        print(f"用例数：{len(selected_names)} / {len(CASES)}", file=sys.stderr)
        if not changed and not fallback:
            print("无改动，不运行", file=sys.stderr)
            return 0
        if not selected_names:
            print("无受影响用例，不运行", file=sys.stderr)
            return 0
        if fallback:
            selected_names = None  # Preserve the original full-suite path.
        needs_slot = fallback is not None or len(selected_names) * 2 >= len(CASES)
    if needs_slot:
        try:
            os.environ.update(slot.thread_environment())
        except ValueError as error:
            slot.warn(str(error))
            return 64
    durations = []
    names = args.cases or selected_names
    with suite_reservation([] if needs_slot else names):
        if not needs_slot:
            lower_priority()
        else:
            slot.qos(current=True)
        if args.cases or args.j == 1:
            status = run_named(names or list(CASES), durations)
        else:
            if selected_names is None:
                status = run_parallel(args.j, durations)
            else:
                status = run_parallel(args.j, durations, selected_names)
    if args.durations:
        print("Slowest 15 cases (seconds):")
        for name, elapsed in sorted(durations, key=lambda row: (-row[1], row[0]))[:15]:
            print(f"{elapsed:8.3f}s {name}")
    slow = sorted((row for row in durations if row[1] > args.slow),
                  key=lambda row: (-row[1], row[0]))[:10]
    print(f"Slow cases (> {args.slow:g}s; up to 10):")
    for name, elapsed in slow:
        print(f"{elapsed:8.3f}s {name}")
    return status


def suite_reservation(names):
    if names or os.environ.get("AIW_SLOT_HELD") == "1":
        return nullcontext()
    return slot.reservation([sys.executable, str(Path(__file__).resolve())],
                            label="aiw test_harness")


if __name__ == "__main__":
    sys.exit(main())
