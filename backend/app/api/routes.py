"""REST 路由。每个处理函数只做三件事：取参数、调用 service 的一个方法、返回结果。"""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..service import BusyError, NotFoundError, service
from .schemas import (
    CreateSessionRequest,
    GoalRequest,
    PermissionAnswerRequest,
    PermissionModeRequest,
    SendMessageRequest,
)
from .sse import event_stream

router = APIRouter(prefix="/api")


def _call(method, *args, **kwargs):
    """把 service 抛出的业务异常翻译成 HTTP 状态码。"""
    try:
        return method(*args, **kwargs)
    except NotFoundError as error:
        raise HTTPException(status_code=404, detail=f"not found: {error.args[0]}")
    except BusyError as error:
        raise HTTPException(status_code=409, detail=str(error))
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))


# -- 基础 ---------------------------------------------------------------------

@router.get("/health")
def health():
    return {"ok": True}


@router.get("/events")
async def events(request: Request, session_id: str | None = None, since: int = 0):
    return StreamingResponse(
        event_stream(request, session_id, since),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/state")
def state(session_id: str | None = None):
    return _call(service.get_state, session_id)


@router.get("/tools")
def tools():
    return _call(service.get_tools)


# -- 会话与对话 -----------------------------------------------------------------

@router.get("/sessions")
def list_sessions():
    return _call(service.list_sessions)


@router.post("/sessions", status_code=201)
def create_session(body: CreateSessionRequest):
    return _call(service.create_session, body.title)


@router.delete("/sessions/{session_id}", status_code=204)
def delete_session(session_id: str):
    _call(service.delete_session, session_id)


@router.get("/sessions/{session_id}/events")
def session_events(session_id: str, since: int = 0):
    return _call(service.replay_events, session_id, since)


@router.post("/sessions/{session_id}/messages", status_code=202)
def send_message(session_id: str, body: SendMessageRequest):
    return _call(service.send_message, session_id, body.text)


@router.post("/stop")
def stop():
    return {"stopped": _call(service.stop)}


# -- 权限 ---------------------------------------------------------------------

@router.post("/permissions/{request_id}")
def answer_permission(request_id: str, body: PermissionAnswerRequest):
    _call(service.answer_permission, request_id, body.allow, body.always)
    return {"ok": True}


@router.put("/settings/permission-mode")
def set_permission_mode(body: PermissionModeRequest):
    return {"mode": _call(service.set_permission_mode, body.mode)}


# -- 目标、定时任务、MCP、记忆、技能 ----------------------------------------------

@router.put("/goal")
def set_goal(body: GoalRequest):
    return _call(service.set_goal, body.condition, body.session_id)


@router.delete("/goal")
def clear_goal(session_id: str | None = None):
    return _call(service.clear_goal, session_id)


@router.delete("/crons/{job_id}")
def cancel_cron(job_id: str):
    return {"message": _call(service.cancel_cron, job_id)}


@router.post("/mcp/{name}/connect")
def connect_mcp(name: str):
    return {"message": _call(service.connect_mcp, name)}


@router.get("/memory/{filename}")
def get_memory(filename: str):
    return _call(service.get_memory, filename)


@router.get("/skills/{name}")
def get_skill(name: str):
    return _call(service.get_skill, name)
