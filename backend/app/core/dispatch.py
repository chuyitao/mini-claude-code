"""工具执行管道：PreToolUse 钩子 -> 处理函数 -> PostToolUse 钩子。

主 Agent、子 Agent、队友都走这一条管道，所以权限检查只写一次，
前端也能用同一种事件（tool_start / tool_end）展示所有工具调用。
"""
import time

from .config import WORKDIR
from .events import current_parent, emit, log, tool_scope
from .hooks import register_hook, trigger_hooks
from .permission import permission_hook
from .tools_base import call_tool_handler

OUTPUT_PREVIEW_CHARS = 20000     # 推给前端的工具输出上限（完整内容仍在对话历史里）


def tool_status(output: str) -> str:
    text = str(output).lstrip()
    if text.startswith("Permission denied") or text.startswith("Blocked:"):
        return "denied"
    if text.startswith("Error") or text.startswith("Unknown tool"):
        return "error"
    return "ok"


def tool_started(block, agent: str | None = None) -> float:
    emit("tool_start", id=block.id, name=block.name, input=dict(block.input or {}),
         parent=current_parent(), agent=agent)
    return time.time()


def tool_finished(block, output, started_at: float, agent: str | None = None,
                  status: str | None = None) -> None:
    text = str(output)
    emit("tool_end", id=block.id, name=block.name,
         output=text[:OUTPUT_PREVIEW_CHARS],
         truncated=len(text) > OUTPUT_PREVIEW_CHARS,
         status=status or tool_status(text),
         duration_ms=int((time.time() - started_at) * 1000), agent=agent)


def run_tool(block, handlers: dict, agent: str | None = None) -> str:
    """执行一次工具调用并返回结果文本；被钩子拦截时返回拒绝原因。"""
    started_at = tool_started(block, agent)
    blocked = trigger_hooks("PreToolUse", block)
    if blocked is not None:
        output = str(blocked)
    else:
        with tool_scope(block.id):      # 期间派生的子 Agent 工具会挂在这次调用下面
            output = call_tool_handler(handlers.get(block.name), block.input, block.name)
        trigger_hooks("PostToolUse", block, output)
    tool_finished(block, output, started_at, agent)
    return str(output)


# -- 内置钩子 ---------------------------------------------------------------

def large_output_hook(block, output):
    if len(str(output)) > 100000:
        log(f"[hook] large output from {block.name}: {len(str(output))} chars")
    return None


def user_prompt_hook(query: str):
    log(f"[hook] UserPromptSubmit: {WORKDIR}")
    return None


def stop_hook(messages: list):
    tool_count = sum(
        1 for message in messages if isinstance(message.get("content"), list)
        for item in message["content"]
        if isinstance(item, dict) and item.get("type") == "tool_result")
    log(f"[hook] Stop: {tool_count} tool result(s)")
    return None


register_hook("UserPromptSubmit", user_prompt_hook)
register_hook("PreToolUse", permission_hook)
register_hook("PostToolUse", large_output_hook)
register_hook("Stop", stop_hook)
