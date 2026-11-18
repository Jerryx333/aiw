"""只读能力声明；运行 aiw capabilities，不读取本机配置、不启动模型。"""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from seats_config import BUILTIN_COMMANDS

def capabilities():
    return {'version': 1, 'capabilities': ['dispatch-contract-v1', 'named-chain-v1', 'tool-templates-v1', 'agent-runtime-v1', 'background-notify-v1', 'scratch-delivery-v1'],
            'commands': sorted(BUILTIN_COMMANDS),
            'dispatch': {'values': ['--attach', '--effort', '--chain', '--verify', '--verify-timeout', '--deliver'],
                         'flags': ['--ro', '--text', '--worktree', '--net', '--no-net', '--no-fallback', '--no-findings', '--allow-nested', '--require-no-tools',
                                   '--background', '--no-notify']}}

if __name__ == '__main__':
    print(json.dumps(capabilities(), ensure_ascii=False))
