"""Agent 主循环（s01）与集成运行时（s15）。

循环本身始终是同一个结构：
    调用模型 -> 响应里有 tool_use 吗？ -> 执行工具 -> 把 tool_result 追加回 messages -> 再来一轮
其余机制都只是挂在这个循环的某个位置上：

    LLM 调用前   cron 注入 / 后台任务通知 / todo 提醒 / 上下文压缩 / 组装 system prompt
    LLM 调用     流式输出 + 错误恢复（重试、max_tokens 升级、被动压缩）
    工具执行     PreToolUse 钩子（权限）-> 分发 -> PostToolUse 钩子
    准备停止时   Goal 评估（s17）-> Stop 钩子 -> 记忆提取
"""
import asyncio
import os
import secrets
import threading

from .background import (
    collect_background_results,
    has_pending_background,
    should_run_background,
    start_background_task,
)
from .compact import (
    compact_history,
    estimate_size,
    extract_text,
    fit_tool_results,
    has_tool_use,
    micro_compact,
    reactive_compact,
    snip_compact,
    tool_result_budget,
)
from .config import (
    CONTEXT_LIMIT,
    CONTINUATION_PROMPT,
    DEFAULT_MAX_TOKENS,
    ESCALATED_MAX_TOKENS,
    MAX_RECOVERY_RETRIES,
    MAX_TURN_STEPS,
    client,
)
from .context import remember_after_turn, update_context
from .cron import (
    CronJob,
    acknowledge_cron_jobs,
    consume_cron_queue,
    cron_scheduler_loop,
    load_durable_jobs,
    restore_cron_jobs,
)
from .dispatch import tool_finished, tool_started
from .events import emit, log, tool_scope
from .goal import GOAL
from .hooks import trigger_hooks
from .mcp import mcp_clients
from .permission import PERMISSIONS
from .prompt import assemble_system_prompt
from .teams import active_teammates
from .recovery import RecoveryState, is_prompt_too_long_error, with_retry
from .tool_registry import assemble_tool_pool
from .tools_base import call_tool_handler
from .worktree import release_completed_assignment

STREAMING = os.getenv("STREAMING", "1").lower() not in ("0", "false", "no")

rounds_since_todo = 0
agent_lock = threading.Lock()       # 同一时刻只允许一个主 Agent 轮次运行
_cancel = threading.Event()


class TurnCancelled(Exception):
    """用户点击了"停止"。"""


def request_cancel() -> None:
    """请求中断当前轮次：循环会在下一个检查点停下，等待中的审批按拒绝处理。"""
    _cancel.set()
    PERMISSIONS.cancel_all()


def _check_cancel() -> None:
    if _cancel.is_set():
        raise TurnCancelled()


# -- 运行时服务 ---------------------------------------------------------------

_runtime_services_started = False
_runtime_services_lock = threading.Lock()


def start_runtime_services() -> None:
    """启动一次定时调度线程，并恢复落盘的 durable 任务。"""
    global _runtime_services_started
    with _runtime_services_lock:
        if _runtime_services_started:
            return
        load_durable_jobs()
        threading.Thread(target=cron_scheduler_loop, daemon=True).start()
        _runtime_services_started = True


# -- LLM 调用前：上下文预算 ------------------------------------------------------

def prepare_context(messages: list, active_request: str) -> list:
    """每次调用模型前都走同一条压缩管线：能轻则轻，实在不行才摘要。"""
    before = estimate_size(messages)
    messages[:] = tool_result_budget(messages)
    messages[:] = snip_compact(messages)
    kind = None
    if estimate_size(messages) > CONTEXT_LIMIT:
        target = int(CONTEXT_LIMIT * 0.8)
        messages[:] = micro_compact(messages, target)
        kind = "micro"
        if estimate_size(messages) > CONTEXT_LIMIT:
            messages[:] = fit_tool_results(messages, target)
            kind = "fit"
    if estimate_size(messages) > CONTEXT_LIMIT:
        messages[:] = compact_history(messages, active_request)
        kind = "summary"
    after = estimate_size(messages)
    if kind or after < before:
        emit("compact", kind=kind or "snip", before=before, after=after)
    emit("context", size=after, limit=CONTEXT_LIMIT, messages=len(messages))
    return messages


def build_user_content(results: list[dict]) -> list[dict]:
    """工具结果与已完成的后台任务通知，都以 user 身份回传给模型。"""
    content = list(results)
    for note in collect_background_results():
        emit("notification", kind="background", text=note)
        content.append({"type": "text", "text": note})
    return content


def inject_background_notifications(messages: list) -> None:
    notes = collect_background_results()
    for note in notes:
        emit("notification", kind="background", text=note)
    if notes:
        messages.append({"role": "user", "content": [
            {"type": "text", "text": note} for note in notes]})


# -- LLM 调用 ------------------------------------------------------------------

def _usage(response) -> dict:
    usage = getattr(response, "usage", None)
    return {"input": int(getattr(usage, "input_tokens", 0) or 0),
            "output": int(getattr(usage, "output_tokens", 0) or 0)}


def _thinking_text(content) -> str:
    return "\n".join(str(getattr(block, "thinking", "") or "")
                     for block in content
                     if getattr(block, "type", None) == "thinking").strip()


def call_llm(messages: list, context: dict, tools: list,
             state: RecoveryState, max_tokens: int):
    """调用一次模型。流式输出时把文本增量实时推给前端；失败由 with_retry 决定是否重试。"""
    system = assemble_system_prompt(context)

    def attempt():
        message_id = f"msg_{secrets.token_hex(4)}"
        request = dict(model=state.current_model, system=system, messages=messages,
                       tools=tools, max_tokens=max_tokens)
        emit("assistant_start", id=message_id, model=state.current_model)
        try:
            if STREAMING:
                with client.messages.stream(**request) as stream:
                    for text in stream.text_stream:
                        _check_cancel()
                        emit("text_delta", id=message_id, text=text)
                    response = stream.get_final_message()
            else:
                response = client.messages.create(**request)
        except BaseException:
            emit("assistant_abort", id=message_id)      # 这次尝试作废，前端撤掉半截输出
            raise
        emit("assistant_end", id=message_id, text=extract_text(response.content),
             thinking=_thinking_text(response.content),
             stop_reason=response.stop_reason, usage=_usage(response),
             has_tools=has_tool_use(response.content))
        return response

    return with_retry(attempt, state)


# -- 工具执行 ------------------------------------------------------------------

def _execute_tool(block, handlers: dict) -> tuple[str, bool]:
    """执行主 Agent 的一次工具调用。返回 (结果文本, 是否请求了压缩)。"""
    global rounds_since_todo
    started_at = tool_started(block)

    if block.name == "compact":
        output = "[Compaction requested. This completed turn will be summarized.]"
        tool_finished(block, output, started_at)
        return output, True

    blocked = trigger_hooks("PreToolUse", block)
    if blocked:
        tool_finished(block, blocked, started_at)
        return str(blocked), False

    if should_run_background(block.name, block.input):
        try:
            bg_id = start_background_task(block, handlers)
            output = (f"[Background task {bg_id} started] "
                      "Result will arrive as a task_notification.")
        except Exception as exc:
            output = (f"Error: Failed to start background task: "
                      f"{type(exc).__name__}: {exc}")
        tool_finished(block, output, started_at)
        return output, False

    with tool_scope(block.id):
        output = call_tool_handler(handlers.get(block.name), block.input, block.name)
    trigger_hooks("PostToolUse", block, output)
    tool_finished(block, output, started_at)

    if block.name == "todo_write":
        rounds_since_todo = 0
    else:
        rounds_since_todo += 1
    return output, False


# -- 准备停止时：Goal 评估 --------------------------------------------------------

def _goal_gate(messages: list) -> bool:
    """模型想停下时询问 Goal 评估器。返回 True 表示目标未达成，需要继续干活。"""
    if GOAL.active is None:
        return False
    condition = GOAL.active.condition
    decision = asyncio.run(GOAL.evaluate_after_turn(
        messages, background_running=has_pending_background()))
    emit("goal", action=decision.action, reason=decision.reason,
         condition=condition, active=GOAL.active is not None)
    if decision.action != "block":
        return False
    messages.append({"role": "user", "content": (
        "[Goal still active]\n"
        f"Condition: {condition}\n"
        f"Evaluator: {decision.reason}\n"
        "Continue working and surface the missing evidence.")})
    return True


# -- 主循环 --------------------------------------------------------------------

def agent_loop(messages: list, context: dict, active_request: str) -> str:
    """跑完一轮（直到模型不再调用工具）。返回结束原因：done / cancelled / error / limit。"""
    global rounds_since_todo
    _cancel.clear()
    GOAL.begin_query()
    state = RecoveryState()
    max_tokens = DEFAULT_MAX_TOKENS
    unacknowledged_cron_jobs: list[CronJob] = []
    steps = 0

    try:
        while True:
            _check_cancel()
            steps += 1
            if steps > MAX_TURN_STEPS:
                emit("error", message=f"单轮超过 {MAX_TURN_STEPS} 步，已停止。")
                return "limit"

            # 1) 注入定时任务、后台任务通知、todo 提醒
            fired = consume_cron_queue()
            unacknowledged_cron_jobs.extend(fired)
            for job in fired:
                messages.append({"role": "user",
                                 "content": f"[Scheduled] {job.prompt}"})
                emit("cron", event="inject", id=job.id, prompt=job.prompt)
            if fired:
                scheduled_requests = "\n".join(
                    f"Run scheduled task: {job.prompt}" for job in fired)
                active_request = f"{active_request}\n{scheduled_requests}".strip()

            inject_background_notifications(messages)

            if rounds_since_todo >= 3:
                messages.append({"role": "user",
                                 "content": "<reminder>Update your todos.</reminder>"})
                rounds_since_todo = 0

            # 2) 上下文压缩 + 组装本轮上下文与工具池
            prepare_context(messages, active_request)
            if steps == 1:
                # 记忆召回要额外调用一次模型，同一轮内用户请求不变，所以每轮只做一次
                context = update_context(context, messages)
            else:
                context = {**context, "connected_mcp": list(mcp_clients),
                           "active_teammates": list(active_teammates)}
            tools, handlers = assemble_tool_pool()

            # 3) 调用模型（带错误恢复）
            try:
                response = call_llm(messages, context, tools, state, max_tokens)
            except TurnCancelled:
                raise
            except Exception as e:
                if is_prompt_too_long_error(e) and not state.has_attempted_reactive_compact:
                    before = estimate_size(messages)
                    messages[:] = reactive_compact(messages, active_request)
                    emit("compact", kind="reactive", before=before,
                         after=estimate_size(messages))
                    state.has_attempted_reactive_compact = True
                    continue
                restore_cron_jobs(unacknowledged_cron_jobs)
                error_text = f"[Error] {type(e).__name__}: {e}"
                messages.append({"role": "assistant", "content": [
                    {"type": "text", "text": error_text}]})
                emit("error", message=error_text)
                return "error"

            acknowledge_cron_jobs(unacknowledged_cron_jobs)
            unacknowledged_cron_jobs.clear()

            if response.stop_reason == "max_tokens":
                if not state.has_escalated:
                    max_tokens = ESCALATED_MAX_TOKENS
                    state.has_escalated = True
                    log(f"[max_tokens] retry with {max_tokens}")
                    continue
                messages.append({"role": "assistant", "content": response.content})
                if state.recovery_count < MAX_RECOVERY_RETRIES:
                    messages.append({"role": "user", "content": CONTINUATION_PROMPT})
                    state.recovery_count += 1
                    continue
                return "done"

            max_tokens = DEFAULT_MAX_TOKENS
            state.has_escalated = False
            messages.append({"role": "assistant", "content": response.content})

            # 4) 没有 tool_use：模型想停下
            if not has_tool_use(response.content):
                if _goal_gate(messages):
                    continue
                trigger_hooks("Stop", messages)
                remember_after_turn(messages)
                return "done"

            # 5) 执行工具，每个 tool_use 都必须有对应的 tool_result
            results = []
            compact_requested = False
            cancelled = False
            for block in response.content:
                if block.type != "tool_use":
                    continue
                if cancelled or _cancel.is_set():
                    cancelled = True
                    output = "Cancelled by user before this tool ran."
                else:
                    output, wants_compact = _execute_tool(block, handlers)
                    compact_requested = compact_requested or wants_compact
                results.append({"type": "tool_result",
                                "tool_use_id": block.id, "content": output})

            messages.append({"role": "user", "content": build_user_content(results)})
            if cancelled:
                raise TurnCancelled()
            if compact_requested:
                before = estimate_size(messages)
                messages[:] = compact_history(messages, active_request)
                emit("compact", kind="manual", before=before,
                     after=estimate_size(messages))
    except TurnCancelled:
        restore_cron_jobs(unacknowledged_cron_jobs)
        # 历史必须以完整的一问一答收尾，否则下一轮请求会因结构非法被 API 拒绝
        if messages and messages[-1].get("role") == "user":
            messages.append({"role": "assistant", "content": [
                {"type": "text", "text": "[已被用户中断]"}]})
        return "cancelled"
    finally:
        release_completed_assignment("agent")
