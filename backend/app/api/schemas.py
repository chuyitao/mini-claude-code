"""请求体模型（Pydantic）。响应直接返回 service 给出的字典。"""
from typing import Literal

from pydantic import BaseModel, Field


class CreateSessionRequest(BaseModel):
    title: str | None = Field(default=None, max_length=60)


class SendMessageRequest(BaseModel):
    text: str = Field(min_length=1, max_length=20000)


class PermissionAnswerRequest(BaseModel):
    allow: bool
    always: bool = False      # 本次运行期间始终允许该工具


class PermissionModeRequest(BaseModel):
    mode: Literal["ask", "auto"]


class GoalRequest(BaseModel):
    condition: str = Field(min_length=1, max_length=4000)
    session_id: str | None = None     # 把"已设定目标"的提示显示在哪个会话里
