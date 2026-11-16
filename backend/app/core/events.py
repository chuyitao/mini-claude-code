"""事件总线：把 Agent 运行过程变成前端可订阅的事件流。

原课程代码用 print() 把过程打到终端；这里统一改为 emit()/log()，
由接口层通过 SSE 推给前端。核心模块只管"发生了什么"，不关心谁在看。
"""
import itertools
import queue
import re
import threading
import time
from contextlib import contextmanager

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\r")
_seq = itertools.count(1)
_lock = threading.RLock()
_subscribers: set[queue.Queue] = set()
_history: list[dict] = []          # 可回放事件（不含流式增量）
HISTORY_LIMIT = 5000
TRANSIENT = {"text_delta", "log"}  # 只实时推送、不进回放历史的事件

_ctx = threading.local()           # 每个线程自己的身份：会话、Agent 名、父工具


def current_session() -> str | None:
    return getattr(_ctx, "session_id", None)


def current_agent() -> str:
    return getattr(_ctx, "agent", "lead")


def current_parent() -> str | None:
    return getattr(_ctx, "parent_tool", None)


def bind(session_id: str | None = None, agent: str | None = None) -> None:
    """给当前线程绑定身份；之后该线程 emit 的事件都会带上它。"""
    if session_id is not None:
        _ctx.session_id = session_id
    if agent is not None:
        _ctx.agent = agent


def snapshot() -> dict:
    """取出当前线程身份，用于传给新开的线程（见 inherit）。"""
    return {"session_id": current_session(), "agent": current_agent(),
            "parent_tool": current_parent()}


def inherit(state: dict, agent: str | None = None) -> None:
    _ctx.session_id = state.get("session_id")
    _ctx.agent = agent or state.get("agent", "lead")
    _ctx.parent_tool = None if agent else state.get("parent_tool")


@contextmanager
def tool_scope(tool_id: str):
    """工具执行期间，把它记为"父工具"：子 Agent 的工具调用会挂在它下面。"""
    previous = current_parent()
    _ctx.parent_tool = tool_id
    try:
        yield
    finally:
        _ctx.parent_tool = previous


def emit(event_type: str, **data) -> dict:
    """发出一个事件：补上序号、时间、会话与 Agent 身份，然后分发给所有订阅者。"""
    session_id = data.pop("session_id", None) or current_session()
    event = {
        "seq": next(_seq),
        "ts": time.time(),
        "type": event_type,
        "session_id": None,

        "agent": data.pop("agent", None) or current_agent(),
        **data,
    }
    event["session_id"] = None if session_id == "*" else session_id   # "*" 表示全局事件
    with _lock:
        if event_type not in TRANSIENT:
            _history.append(event)
            if len(_history) > HISTORY_LIMIT:
                del _history[: len(_history) - HISTORY_LIMIT]
        targets = list(_subscribers)
    for target in targets:
        try:
            target.put_nowait(event)
        except queue.Full:      # 订阅者太慢就丢弃，不能反过来卡住 Agent
            pass
    return event


def log(*parts, sep: str = " ", **_ignored) -> None:
    """print() 的替身：去掉终端颜色码后作为 log 事件发出，同时保留控制台输出。"""
    text = _ANSI.sub("", sep.join(str(part) for part in parts)).strip()
    if not text:
        return
    try:
        print(f"[{current_agent()}] {text}", flush=True)
    except (UnicodeEncodeError, OSError):   # 某些终端编码打不出的字符不应影响 Agent
        pass
    emit("log", text=text)


def subscribe() -> queue.Queue:
    subscriber: queue.Queue = queue.Queue(maxsize=10000)
    with _lock:
        _subscribers.add(subscriber)
    return subscriber


def unsubscribe(subscriber: queue.Queue) -> None:
    with _lock:
        _subscribers.discard(subscriber)


def subscriber_count() -> int:
    with _lock:
        return len(_subscribers)


def history(session_id: str | None = None, since: int = 0) -> list[dict]:
    """回放历史事件。session_id 为空时返回全部；否则返回该会话及全局事件。"""
    with _lock:
        return [event for event in _history
                if event["seq"] > since
                and (session_id is None
                     or event["session_id"] in (None, session_id))]


def forget_session(session_id: str) -> None:
    with _lock:
        _history[:] = [e for e in _history if e["session_id"] != session_id]
