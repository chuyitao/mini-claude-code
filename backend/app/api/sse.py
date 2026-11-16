"""Server-Sent Events：把 service 的事件队列变成一条长连接的事件流。"""
import asyncio
import json
import queue

from fastapi import Request

from ..service import service

KEEPALIVE_SECONDS = 15


def _format(event: dict) -> str:
    return f"id: {event['seq']}\ndata: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"


async def event_stream(request: Request, session_id: str | None, since: int):
    """先补发断线期间错过的事件，再持续推送新事件；空闲时发心跳保持连接。"""
    stream = service.open_event_stream()        # 先订阅再回放，两者之间不会漏事件
    try:
        last = since
        if session_id:
            try:
                for event in service.replay_events(session_id, since):
                    last = max(last, event["seq"])
                    yield _format(event)
            except KeyError:
                pass
        idle = 0.0
        while not await request.is_disconnected():
            try:
                event = await asyncio.to_thread(stream.get, True, 1.0)
            except queue.Empty:
                idle += 1.0
                if idle >= KEEPALIVE_SECONDS:
                    idle = 0.0
                    yield ": keepalive\n\n"
                continue
            idle = 0.0
            if event["seq"] <= last and event["type"] not in ("text_delta", "log"):
                continue                        # 回放里已经发过
            yield _format(event)
    finally:
        service.close_event_stream(stream)
