"""后端主链路门面：把 core 里的各项机制编排成"一个可以对话的 Agent"。

这个文件只对外暴露方法（AgentService 的公开方法），不关心 HTTP；
接口层（app/api）只调用这些方法，不直接接触 core 的内部状态。
"""
import queue
import secrets
import threading
import time
from dataclasses import asdict

from ..core import events
from ..core.background import background_tasks, has_pending_background
from ..core.compact import compact_history, estimate_size
from ..core.config import CONTEXT_LIMIT, MOCK_LLM, MODEL, SKILLS_DIR, WORKDIR
from ..core.cron import cancel_job, cron_lock, cron_queue, scheduled_jobs
from ..core.events import emit
from ..core.goal import CLEAR_ALIASES, GOAL, GoalError
from ..core.hooks import trigger_hooks
from ..core.loop import STREAMING, agent_lock, agent_loop, request_cancel, start_runtime_services
from ..core.mcp import MOCK_SERVERS, connect_mcp, mcp_clients
from ..core.memory import list_memory_files
from ..core.permission import PERMISSIONS
from ..core.skills import SKILL_REGISTRY
from ..core.tasks import can_start, list_tasks, teammate_assignments
from ..core.teams import (
    BUS,
    active_teammates,
    consume_lead_inbox,
    format_team_events,
    pending_requests,
    plan_gates,
)
from ..core.todo import get_todos
from ..core.tool_registry import assemble_tool_pool


class BusyError(RuntimeError):
    """Agent 正在执行上一轮，暂时不能接新消息。"""


class NotFoundError(KeyError):
    """会话或资源不存在。"""


class Session:
    def __init__(self, title: str = "新会话"):
        self.id = f"s_{secrets.token_hex(4)}"
        self.title = title
        self.created_at = time.time()
        self.updated_at = self.created_at
        self.history: list = []           # 发给模型的 messages
        self.context: dict = {}           # 记忆、MCP、队友等每轮上下文
        self.active_request = "(no active user request)"

    def summary(self) -> dict:
        return {"id": self.id, "title": self.title,
                "created_at": self.created_at, "updated_at": self.updated_at,
                "messages": len(self.history)}


class AgentService:
    def __init__(self):
        self._sessions: dict[str, Session] = {}
        self._lock = threading.RLock()
        self._running: str | None = None      # 正在执行轮次的会话 id
        self._active: str | None = None       # 最近使用的会话：异步事件投递到这里
        self._started = False

    # ------------------------------------------------------------------ 生命周期

    def start(self) -> None:
        """启动定时调度线程和异步事件线程（幂等）。"""
        with self._lock:
            if self._started:
                return
            self._started = True
        start_runtime_services()
        threading.Thread(target=self._async_event_loop, daemon=True).start()

    # ------------------------------------------------------------------ 事件流

    def open_event_stream(self) -> queue.Queue:
        return events.subscribe()

    def close_event_stream(self, stream: queue.Queue) -> None:
        events.unsubscribe(stream)

    def replay_events(self, session_id: str, since: int = 0) -> list[dict]:
        """某个会话的历史事件，前端刷新页面后靠它重建界面。"""
        self._get(session_id)
        return events.history(session_id, since)

    # ------------------------------------------------------------------ 会话

    def list_sessions(self) -> list[dict]:
        with self._lock:
            sessions = sorted(self._sessions.values(),
                              key=lambda item: item.updated_at, reverse=True)
            return [session.summary() for session in sessions]

    def create_session(self, title: str | None = None) -> dict:
        session = Session(title or "新会话")
        with self._lock:
            self._sessions[session.id] = session
            self._active = session.id
        emit("session", event="created", session=session.summary(), session_id="*")
        return session.summary()

    def delete_session(self, session_id: str) -> None:
        with self._lock:
            if self._running == session_id:
                raise BusyError("该会话正在运行，请先停止。")
            if self._sessions.pop(session_id, None) is None:
                raise NotFoundError(session_id)
            if self._active == session_id:
                self._active = next(iter(self._sessions), None)
        events.forget_session(session_id)
        emit("session", event="deleted", id=session_id, session_id="*")

    # ------------------------------------------------------------------ 对话

    def send_message(self, session_id: str, text: str) -> dict:
        """提交一条用户消息。立即返回；执行过程通过事件流推送。"""
        text = text.strip()
        if not text:
            raise ValueError("消息不能为空")
        session = self._get(session_id)
        if text.startswith("/"):
            handled = self._run_command(session, text)
            if handled is not None:
                return {"accepted": True, "command": True, "message": handled}
        with self._lock:
            if self._running is not None:
                raise BusyError("Agent 正在执行上一轮，请等待完成或先停止。")
            self._running = session.id
            self._active = session.id
        if not session.history:
            session.title = text[:30]
        threading.Thread(target=self._run_user_turn, args=(session, text),
                         daemon=True).start()
        return {"accepted": True, "command": False}

    def stop(self) -> bool:
        """中断正在执行的轮次。"""
        with self._lock:
            running = self._running is not None
        if running:
            request_cancel()
        return running

    # ------------------------------------------------------------------ 权限

    def answer_permission(self, request_id: str, allow: bool, always: bool = False) -> None:
        if not PERMISSIONS.resolve(request_id, allow, always):
            raise NotFoundError(request_id)

    def set_permission_mode(self, mode: str) -> str:
        return PERMISSIONS.set_mode(mode)

    # ------------------------------------------------------------------ 目标（s17）

    def set_goal(self, condition: str, session_id: str | None = None) -> dict:
        try:
            GOAL.set_goal(condition)
        except GoalError as error:
            raise ValueError(str(error))
        emit("goal", action="set", condition=condition, active=True, reason="",
             session_id=session_id or "*")
        return self._goal_state()

    def clear_goal(self, session_id: str | None = None) -> dict:
        message = GOAL.clear()
        emit("goal", action="cleared", condition="", active=False, reason=message,
             session_id=session_id or "*")
        return self._goal_state()

    # ------------------------------------------------------------------ 其他操作

    def cancel_cron(self, job_id: str) -> str:
        return cancel_job(job_id)

    def connect_mcp(self, name: str) -> str:
        return connect_mcp(name)

    # ------------------------------------------------------------------ 只读状态

    def get_state(self, session_id: str | None = None) -> dict:
        """侧栏需要的全部状态，一次取回。"""
        session = self._sessions.get(session_id) if session_id else None
        with cron_lock:
            crons = [asdict(job) for job in scheduled_jobs.values()]
            queued = len(cron_queue)
        tasks = []
        for task in list_tasks():
            item = asdict(task)
            item["ready"] = task.status == "pending" and can_start(task.id)
            tasks.append(item)
        teammates = [{
            "name": name, "status": status,
            "plan": plan_gates.get(name, "not_required"),
            "task_id": (teammate_assignments.get(name) or {}).get("task_id"),
        } for name, status in list(active_teammates.items())]
        return {
            "running": self._running,
            "settings": {
                "model": MODEL, "workdir": str(WORKDIR), "mock": MOCK_LLM,
                "streaming": STREAMING, "permission_mode": PERMISSIONS.mode,
                "always_allow": sorted(PERMISSIONS.always_allow),
            },
            "context": {
                "size": estimate_size(session.history) if session else 0,
                "limit": CONTEXT_LIMIT,
                "messages": len(session.history) if session else 0,
            },
            "permissions": PERMISSIONS.pending(),
            "goal": self._goal_state(),
            "todos": get_todos(),
            "tasks": tasks,
            "team": {
                "teammates": teammates,
                "requests": [asdict(item) for item in list(pending_requests.values())],
            },
            "background": [{"id": bg_id, **info}
                           for bg_id, info in list(background_tasks.items())],
            "crons": {"jobs": crons, "queued": queued},
            "mcp": {
                "available": sorted(MOCK_SERVERS),
                "connected": [{"name": name,
                               "tools": [tool["name"] for tool in client.tools]}
                              for name, client in list(mcp_clients.items())],
            },
            "memory": [{key: record[key] for key in ("filename", "name", "type", "description")}
                       for record in list_memory_files()],
            "skills": [{"name": skill["name"], "description": skill["description"]}
                       for skill in SKILL_REGISTRY.values()],
        }

    def get_tools(self) -> list[dict]:
        """当前工具池（内置工具 + 已连接的 MCP 工具）的说明书。"""
        tools, _ = assemble_tool_pool()
        return [{"name": tool["name"], "description": tool.get("description", ""),
                 "input_schema": tool.get("input_schema", {})} for tool in tools]

    def get_memory(self, filename: str) -> dict:
        for record in list_memory_files():
            if record["filename"] == filename:
                return record
        raise NotFoundError(filename)

    def get_skill(self, name: str) -> dict:
        skill = SKILL_REGISTRY.get(name)
        if skill is None:
            raise NotFoundError(name)
        return dict(skill, directory=str(SKILLS_DIR))

    # ================================================================== 内部实现

    def _get(self, session_id: str) -> Session:
        session = self._sessions.get(session_id)
        if session is None:
            raise NotFoundError(session_id)
        return session

    def _goal_state(self) -> dict:
        active = GOAL.active
        return {
            "active": active is not None,
            "condition": active.condition if active else "",
            "iterations": active.iterations if active else 0,
            "last_reason": active.last_reason if active else None,
            "last_status": GOAL.last_status,
        }

    def _run_command(self, session: Session, text: str) -> str | None:
        """处理斜杠命令。返回提示文本；返回 None 表示不是命令，按普通消息处理。"""
        command, _, argument = text.partition(" ")
        argument = argument.strip()
        events.bind(session_id=session.id, agent="lead")
        if command == "/goal":
            try:
                if not argument or argument == "status":
                    message = GOAL.status()
                elif argument.lower() in CLEAR_ALIASES:
                    self.clear_goal(session.id)     # set / clear 自己会发 goal 事件，不再重复提示
                    return "目标已清除。"
                else:
                    self.set_goal(argument, session.id)
                    return f"目标已设定：{argument}"
            except ValueError as error:
                message = f"目标设置失败：{error}"
        elif command == "/compact":
            if self._running is not None:
                raise BusyError("Agent 运行中，稍后再压缩。")
            if not session.history:
                message = "当前会话还没有可压缩的历史。"
            else:
                before = estimate_size(session.history)
                session.history[:] = compact_history(session.history, session.active_request)
                after = estimate_size(session.history)
                emit("compact", kind="manual", before=before, after=after)
                message = f"已压缩上下文：{before} → {after} 字符。"
        elif command == "/mode":
            try:
                message = f"权限模式：{self.set_permission_mode(argument)}"
            except ValueError:
                message = "用法：/mode ask 或 /mode auto"
        else:
            return None
        emit("notice", text=message, command=text)
        return message

    def _run_user_turn(self, session: Session, text: str) -> None:
        events.bind(session_id=session.id, agent="lead")
        try:
            with agent_lock:
                emit("turn_start", source="user")
                emit("user_message", text=text)
                trigger_hooks("UserPromptSubmit", text)
                session.active_request = text
                session.history.append({"role": "user", "content": text})
                result = self._run_loop(session, text)
                emit("turn_end", result=result, title=session.title)
        finally:
            with self._lock:
                self._running = None

    def _run_loop(self, session: Session, active_request: str) -> str:
        try:
            result = agent_loop(session.history, session.context, active_request)
        except Exception as error:      # 兜底：任何未预料的异常都不能让线程悄悄死掉
            result = "error"
            emit("error", message=f"{type(error).__name__}: {error}")
        session.updated_at = time.time()
        return result

    def _async_event_loop(self) -> None:
        """没有用户输入时也要推进的事情：定时任务到点、队友来信、后台任务完成。"""
        while True:
            time.sleep(1)
            with cron_lock:
                fired = list(cron_queue)
            if not fired and not has_pending_background() and not self._lead_has_mail():
                continue
            with self._lock:
                if self._running is not None:
                    continue                    # 主 Agent 正忙：这些事件会在它的循环里被处理
                session = self._sessions.get(self._active or "")
                if session is None:
                    continue
                self._running = session.id
            try:
                self._run_auto_turn(session)
            finally:
                with self._lock:
                    self._running = None

    @staticmethod
    def _lead_has_mail() -> bool:
        return bool(BUS.peek("lead"))

    def _run_auto_turn(self, session: Session) -> None:
        events.bind(session_id=session.id, agent="lead")
        with agent_lock:
            with cron_lock:
                fired = list(cron_queue)
            inbox = consume_lead_inbox(route_protocol=True)
            if not fired and not inbox and not has_pending_background():
                return
            source = "cron" if fired else "team" if inbox else "background"
            emit("turn_start", source=source)
            if inbox:
                team_text = format_team_events(inbox)
                session.history.append({"role": "user", "content": team_text})
                emit("user_message", text=team_text, source="team")
            scheduled = [f"Run scheduled task: {job.prompt}" for job in fired]
            active_request = "\n".join(scheduled) if scheduled else session.active_request
            result = self._run_loop(session, active_request)
            emit("turn_end", result=result, title=session.title)


service = AgentService()
