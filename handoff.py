#!/usr/bin/env python3
"""校验交接文件并打印接续命令，仅使用标准库。"""
from __future__ import annotations

import argparse
import datetime as dt
import os
from env_compat import getenv
from pathlib import Path
import re
import shlex
import sys

HERE = Path(__file__).resolve().parent
STATE = Path(getenv("AIW_STATE", HERE / "state"))
SECTIONS = {
    "目标": "说明任务目标和验收条件。",
    "现状": "记录已完成的工作、验证结果和当前进度。",
    "已定决策": "列出已确认的方案、关键取舍和约束。",
    "下一步": "列出接下来可执行的步骤和待解决的问题。",
    "相关文件": "列出相关文件路径及用途。",
    "注意事项": "说明风险、边界和容易遗漏的信息（可选）。",
}


class UsageParser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(64, f"参数错误：{message}\n")


def main() -> int:
    if os.environ.get("AIW_NESTED") == "1":
        print("执行方不得开会话（aiw handoff）", file=sys.stderr)
        return 64
    parser = UsageParser(description=__doc__, allow_abbrev=False)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--template", action="store_true")
    mode.add_argument("--path", action="store_true")
    parser.add_argument("file", nargs="?")
    parser.add_argument("--workdir", default=os.getcwd())
    parser.add_argument("--dry-run", action="store_true", help="已无作用，仅为兼容旧脚本")
    args = parser.parse_args()
    if (args.template or args.path) and (args.file or args.dry_run):
        parser.error("--template / --path 不能与交接文件或 --dry-run 同用")
    if not (args.template or args.path or args.file):
        parser.error("请提供交接文件、--template 或 --path")
    workdir = Path(args.workdir).expanduser().resolve()
    if not workdir.is_dir():
        print(f"工作目录不存在或不是目录：{workdir}", file=sys.stderr)
        return 1
    if args.template:
        print("\n\n".join(f"## {name}\n{description}" for name, description in SECTIONS.items()))
        return 0
    if args.path:
        project = re.sub(r"[^A-Za-z0-9]", "-", workdir.name) or "project"
        try:
            directory = STATE / "handoffs"
            directory.mkdir(parents=True, exist_ok=True)
            print(directory / f"{dt.datetime.now():%Y%m%d-%H%M%S}-{project}.md")
            return 0
        except OSError as error:
            print(f"无法创建交接目录：{error}", file=sys.stderr)
            return 1
    file = Path(args.file).expanduser().resolve()
    try:
        content = file.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        print(f"无法读取交接文件：{error}", file=sys.stderr)
        return 1
    if not content.strip():
        print("交接文件为空", file=sys.stderr)
        return 1
    headings = set()
    in_fence = False
    for line in content.splitlines():
        if line.startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        if not in_fence:
            match = re.fullmatch(r"## (.+?)[ \t]*", line)
            if match:
                headings.add(match.group(1))
    missing = [name for name in list(SECTIONS)[:5] if name not in headings]
    if missing:
        print("交接文件缺少小节：" + "、".join("## " + name for name in missing), file=sys.stderr)
        return 1
    print(file)
    print("请在新会话中读取此文件，核对现状后按「下一步」继续。")
    prompt = f"读交接文件 {file}，先核对现状，再按「下一步」继续。"
    command = "claude " + shlex.quote(prompt)
    print(f"cd {shlex.quote(str(workdir))} && {command}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
