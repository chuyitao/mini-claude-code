"""每轮上下文：记忆目录、召回的记忆、已连接的 MCP、在线队友。"""

from .memory import (
    consolidate_memories,
    extract_memories,
    load_memories,
    read_memory_index,
)
from .mcp import mcp_clients
from .teams import active_teammates


# -- Context --


def update_context(context: dict, messages: list) -> dict:
    return {
        "memory_catalog": read_memory_index(),
        "memories": load_memories(messages),
        "connected_mcp": list(mcp_clients.keys()),
        "active_teammates": list(active_teammates.keys()),
    }


def remember_after_turn(messages: list) -> None:
    if extract_memories(messages):
        consolidate_memories()
