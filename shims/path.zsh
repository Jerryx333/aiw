# 编译准入：agent 会话（Claude Code、aiw 执行方、Codex）把本目录挪到 PATH 最前；主人自己的终端不动。
# 由 ~/.zshenv 和 ~/.zprofile 各引用一次：登录 shell 的 /etc/zprofile（path_helper）会把系统路径重新排到前面。
if [[ -n "${CLAUDECODE-}${AIW_NESTED-}${CODEX_THREAD_ID-}${CODEX_SANDBOX-}" ]]; then
  path=("${${(%):-%x}:A:h}" ${path:#${${(%):-%x}:A:h}})
  # 沙箱里（如 Codex）读不到核数，在外面算好传下去。
  [[ -z "${AIW_BUILD_JOBS-}" ]] && (( $(sysctl -n hw.ncpu 2>/dev/null || echo 0) >= 2 )) && export AIW_BUILD_JOBS=$(( $(sysctl -n hw.ncpu) / 2 ))
fi
