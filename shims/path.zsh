# 编译准入：agent 会话（Claude Code、aiw 执行方、Codex）把本目录挪到 PATH 最前；主人自己的终端不动。
# 由 ~/.zshenv 和 ~/.zprofile 各引用一次：登录 shell 的 /etc/zprofile（path_helper）会把系统路径重新排到前面。
if [[ -n "${CLAUDECODE-}${AIW_NESTED-}${CODEX_THREAD_ID-}${CODEX_SANDBOX-}" ]]; then
  # AIW_SHIMS 给会自己改 PATH 的脚本用（如 Heya 的 update-local.sh），改完再把它放回最前。
  export AIW_SHIMS="${${(%):-%x}:A:h}"
  path=("$AIW_SHIMS" ${path:#$AIW_SHIMS})
  # 沙箱里（如 Codex）读不到核数，在外面算好传下去。
  [[ -z "${AIW_BUILD_JOBS-}" ]] && (( $(sysctl -n hw.ncpu 2>/dev/null || echo 0) >= 2 )) && export AIW_BUILD_JOBS=$(( $(sysctl -n hw.ncpu) / 2 ))
  # 绕过本目录直接调到 go 的（如 PATH 前面又加了 /opt/homebrew/bin）也限并行；命令行的 -p 优先。
  [[ -n "${AIW_BUILD_JOBS-}" && " ${GOFLAGS-} " != *" -p="* ]] && export GOFLAGS="${GOFLAGS:+$GOFLAGS }-p=$AIW_BUILD_JOBS"
fi
