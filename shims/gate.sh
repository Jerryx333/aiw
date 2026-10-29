#!/bin/bash
# 用途：编译准入。agent 会话把本目录放在 PATH 最前，swift build/test 和 xcodebuild 构建
# 先排 aiw slot 的全机名额（内存压力高时等），并把并行编译数限到核数一半，再交给真正的命令；
# 其余子命令（swift run、xcodebuild -version 等）直接放行。
# 运行：通过 shims/swift、shims/xcodebuild 两个链接调用；AIW_BUILD_GATE=0 关闭，
# AIW_BUILD_JOBS 改并行编译数。已持有名额（AIW_SLOT_HELD=1，如 aiw 验收、aiw slot 里）不再排队。
set -u
here="$(cd "$(dirname "$0")" && pwd -P)"
name="$(basename "$0")"

# 从 PATH 去掉本目录再找真命令，不会调回自己。
path=""
IFS=: read -ra entries <<< "$PATH"
for entry in "${entries[@]}"; do
  [ -n "$entry" ] && [ "$(cd "$entry" 2>/dev/null && pwd -P)" = "$here" ] && continue
  path="${path:+$path:}$entry"
done
export PATH="$path"
real="$(command -v "$name")" || { echo "$name: command not found" >&2; exit 127; }

heavy=0
jobs_flag=""
case "$name" in
  swift)
    case "${1-}" in build|test) heavy=1; jobs_flag="--jobs" ;; esac ;;
  xcodebuild)
    heavy=1; jobs_flag="-jobs"
    for arg in "$@"; do
      case "$arg" in
        -version|-list|-showsdks|-showBuildSettings|-showdestinations|-showTestPlans|-help|-usage|-find-executable|-find-library|-checkFirstLaunchStatus|-runFirstLaunch|-resolvePackageDependencies) heavy=0 ;;
      esac
    done ;;
esac
if [ "$heavy" = 0 ] || [ "${AIW_BUILD_GATE-1}" = 0 ]; then exec "$real" "$@"; fi

args=("$@")
explicit=0
for arg in "$@"; do
  case "$arg" in -j|-j[0-9]*|--jobs|--jobs=*|-jobs) explicit=1 ;; esac
done
if [ "$explicit" = 0 ]; then
  jobs="${AIW_BUILD_JOBS:-$(( $(sysctl -n hw.ncpu 2>/dev/null || echo 8) / 2 ))}"
  [ "$jobs" -ge 1 ] 2>/dev/null || jobs=1
  if [ "$name" = swift ]; then args=("$1" "$jobs_flag" "$jobs" "${@:2}"); else args=("$jobs_flag" "$jobs" "$@"); fi
fi
if [ "${AIW_SLOT_HELD-}" = 1 ]; then exec "$real" "${args[@]}"; fi
exec "${AIW_PYTHON:-python3}" "$here/../slot.py" --label "$name ${1-}" -- "$real" "${args[@]}"
