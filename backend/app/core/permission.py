"""权限治理（s03）：PreToolUse 钩子 + 面向前端的审批通道。

原实现在终端里用 input() 问"是否允许？[y/N]"；这里改为发出
permission_request 事件并等待前端回应，其余判定规则保持一致：
    黑名单命令        -> 直接拒绝
    bash / 未授权 MCP -> 询问用户（auto 模式下自动放行）
    文件越出工作目录   -> 直接拒绝
"""
import secrets
import threading

from .config import PERMISSION_TIMEOUT, WORKDIR
from .events import emit, subscriber_count
from .mcp import mcp_tool_policies

DENY_LIST = ["rm -rf /", "sudo", "shutdown", "reboot", "mkfs", "dd if="]
PERMISSION_MODES = ("ask", "auto")


class PermissionBroker:
    """把"需要用户确认"变成一次可等待的请求。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._pending: dict[str, dict] = {}
        self.mode = "ask"                       # ask：逐次确认；auto：自动放行
        self.always_allow: set[str] = set()     # 用户点过"始终允许"的工具名

    def set_mode(self, mode: str) -> str:
        if mode not in PERMISSION_MODES:
            raise ValueError(f"unknown permission mode: {mode}")
        self.mode = mode
        emit("settings", permission_mode=mode, session_id="*")
        return mode

    def pending(self) -> list[dict]:
        with self._lock:
            return [dict(item["request"]) for item in self._pending.values()]

    def ask(self, block, title: str, detail: str) -> bool:
        """询问用户是否允许这次工具调用；返回 True 表示放行。"""
        if self.mode == "auto" or block.name in self.always_allow:
            return True
        if subscriber_count() == 0:     # 没有任何前端在线，没人能回答
            return False
        request_id = f"perm_{secrets.token_hex(4)}"
        request = {"id": request_id, "tool_id": block.id, "tool": block.name,
                   "title": title, "detail": detail}
        waiter = {"request": request, "event": threading.Event(), "allow": False}
        with self._lock:
            self._pending[request_id] = waiter
        emit("permission_request", **request)
        answered = waiter["event"].wait(PERMISSION_TIMEOUT)
        with self._lock:
            self._pending.pop(request_id, None)
        if not answered:
            emit("permission_resolved", id=request_id, tool_id=block.id,
                 allow=False, reason="timeout")
        return bool(waiter["allow"])

    def resolve(self, request_id: str, allow: bool, always: bool = False) -> bool:
        with self._lock:
            waiter = self._pending.get(request_id)
        if waiter is None:
            return False
        if allow and always:
            self.always_allow.add(waiter["request"]["tool"])
        waiter["allow"] = allow
        waiter["event"].set()
        emit("permission_resolved", id=request_id,
             tool_id=waiter["request"]["tool_id"], allow=allow, always=always)
        return True

    def cancel_all(self) -> None:
        """中断当前轮次时调用：所有等待中的审批按"拒绝"处理。"""
        with self._lock:
            waiters = list(self._pending.values())
        for waiter in waiters:
            self.resolve(waiter["request"]["id"], False)


PERMISSIONS = PermissionBroker()


def permission_hook(block):
    """PreToolUse 钩子：返回 None 放行；返回字符串表示拦截，内容即拒绝原因。"""
    if block.name == "bash":
        command = block.input.get("command", "")
        if not isinstance(command, str):
            return "Permission denied: shell command must be a string"
        for pattern in DENY_LIST:
            if pattern in command:
                return f"Permission denied: '{pattern}' is on the deny list"
        if not PERMISSIONS.ask(block, "执行 Shell 命令", command):
            return "Permission denied by user"
    if block.name in ("read_file", "write_file", "edit_file"):
        path = block.input.get("path", "")
        if not isinstance(path, str):
            return "Permission denied: path must be a string"
        if not (WORKDIR / path).resolve().is_relative_to(WORKDIR):
            return "Permission denied: path is outside the workspace"
    if (block.name.startswith("mcp__")
            and mcp_tool_policies.get(block.name, "confirm") != "allow"):
        if not PERMISSIONS.ask(block, "调用外部 MCP 工具", block.name):
            return "Permission denied by user"
    return None
