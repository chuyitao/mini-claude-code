"""离线演示用的假模型（MOCK_LLM=1 时启用）。

它不做任何推理，只按固定剧本返回文本和 tool_use，目的是在没有 API Key 的情况下
也能把整条链路（循环、工具、权限、任务、团队、压缩、记忆、前端展示）跑起来。
接口形状与 Anthropic SDK 保持一致：messages.create() 与 messages.stream()。
"""
import itertools
import json
import os
import re
import time

_ids = itertools.count(1)
DELAY = float(os.getenv("MOCK_DELAY", "0.012"))      # 模拟打字的间隔（秒）


class Block:
    """模仿 SDK 的内容块：用属性访问 type / text / id / name / input。"""

    def __init__(self, **fields):
        self.__dict__.update(fields)

    def __repr__(self):
        return json.dumps(self.__dict__, ensure_ascii=False, default=str)


class Usage:
    def __init__(self, input_tokens: int, output_tokens: int):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class Response:
    def __init__(self, content: list, prompt_chars: int):
        self.content = content
        self.stop_reason = ("tool_use" if any(b.type == "tool_use" for b in content)
                            else "end_turn")
        output_chars = sum(len(getattr(b, "text", "") or "") for b in content)
        self.usage = Usage(prompt_chars // 4, max(1, output_chars // 4))


def text(value: str) -> Block:
    return Block(type="text", text=value)


def tool(tool_name: str, /, **tool_input) -> Block:
    return Block(type="tool_use", id=f"toolu_mock_{next(_ids):04d}",
                 name=tool_name, input=tool_input)


def _task_ids(messages: list) -> list[str]:
    """从对话里找出已创建任务的 id（按出现顺序去重），供剧本后续步骤引用。"""
    found = re.findall(r"task_[0-9a-f]{8}", json.dumps(messages, default=str))
    return list(dict.fromkeys(found))


def _block_type(block):
    return block.get("type") if isinstance(block, dict) else getattr(block, "type", None)


def _plain_text(content) -> str:
    if isinstance(content, str):
        return content
    parts = []
    for block in content or []:
        if _block_type(block) == "text":
            parts.append(block["text"] if isinstance(block, dict) else block.text)
    return "\n".join(parts)


def _is_tool_result_message(message: dict) -> bool:
    content = message.get("content")
    return (message.get("role") == "user" and isinstance(content, list)
            and any(_block_type(block) == "tool_result" for block in content))


def _turn_state(messages: list) -> tuple[str, int]:
    """返回 (本轮用户原始请求, 本轮已经完成了几批工具调用)。"""
    rounds = 0
    for message in reversed(messages):
        if _is_tool_result_message(message):
            rounds += 1
        elif message.get("role") == "user":
            request = _plain_text(message.get("content"))
            if request.startswith("<reminder>"):
                continue
            return request, rounds
    return "", rounds


HELLO = '''"""由 mini-claude-code 演示剧本生成。"""


def greet(name: str) -> str:
    return f"Hello, {name}!"


if __name__ == "__main__":
    print(greet("mini-claude-code"))
'''

# 每个剧本是一个列表：第 N 批工具调用结束后，返回第 N 项
SCRIPTS = {
    "default": [
        lambda m: [text("我先列个计划，再看看工作区里有什么。"),
                 tool("todo_write", todos=[
                     {"content": "查看工作区文件", "status": "in_progress"},
                     {"content": "创建 hello.py", "status": "pending"},
                     {"content": "运行并验证输出", "status": "pending"}]),
                 tool("glob", pattern="*")],
        lambda m: [text("工作区是空的，我来创建一个示例脚本。"),
                 tool("write_file", path="hello.py", content=HELLO)],
        lambda m: [tool("todo_write", todos=[
                     {"content": "查看工作区文件", "status": "completed"},
                     {"content": "创建 hello.py", "status": "completed"},
                     {"content": "运行并验证输出", "status": "in_progress"}]),
                 tool("bash", command="python hello.py")],
        lambda m: [text("把问候语改成中文，再确认一次文件内容。"),
                 tool("edit_file", path="hello.py",
                      old_text='return f"Hello, {name}!"',
                      new_text='return f"你好，{name}！"'),
                 tool("read_file", path="hello.py", limit=12)],
        lambda m: [tool("todo_write", todos=[
                     {"content": "查看工作区文件", "status": "completed"},
                     {"content": "创建 hello.py", "status": "completed"},
                     {"content": "运行并验证输出", "status": "completed"}])],
        lambda m: [text(
            "完成了。这一轮我做了三件事：\n\n"
            "1. 用 `glob` 查看了工作区\n"
            "2. 用 `write_file` 创建了 **hello.py**，并用 `edit_file` 把问候语改成中文\n"
            "3. 用 `bash` 运行脚本验证输出\n\n"
            "```python\ndef greet(name: str) -> str:\n    return f\"你好，{name}！\"\n```\n\n"
            "> 这是离线演示模式（MOCK_LLM=1）的固定剧本。配置好 `.env` 后即可换成真实模型。")],
    ],
    "subagent": [
        lambda m: [text("这个调研可以交给子 Agent，主对话只保留结论。"),
                 tool("task", description="列出工作区里的 Python 文件并总结它们的用途")],
        lambda m: [text("子 Agent 已返回结论：工作区目前只有少量示例脚本，结构很简单。")],
    ],
    "team": [
        lambda m: [text("先把工作拆成有依赖关系的任务。"),
                 tool("create_task", subject="整理配置加载", description="统一读取 .env 的入口"),
                 tool("create_task", subject="补充单元测试", description="覆盖配置加载的边界情况")],
        lambda m: [text("测试要等配置整理完才能写，给它加上依赖。"),
                 tool("update_task", task_id=_task_ids(m)[-1], addBlockedBy=_task_ids(m)[:1])],
        lambda m: [text("任务图建好了，接下来派一位队友负责第一个任务。"),
                 tool("spawn_teammate", name="alice", role="backend", task_id=_task_ids(m)[0],
                      prompt="完成分配给你的任务，完成后汇报结果。")],
        lambda m: [text("队友 alice 已开始工作，她的进展会以团队事件的形式回到对话里。")],
    ],
    "background": [
        lambda m: [text("这条命令比较慢，放到后台执行。"),
                 tool("bash", command="python -c \"import time; time.sleep(2); print('build ok')\"",
                      run_in_background=True)],
        lambda m: [text("后台任务已启动，完成后结果会以 task_notification 的形式通知我。")],
    ],
    "cron": [
        lambda m: [tool("schedule_cron", cron="*/30 * * * *", prompt="检查一次测试是否通过",
                      recurring=True, durable=False)],
        lambda m: [tool("list_crons")],
        lambda m: [text("已注册定时任务：每 30 分钟检查一次测试。")],
    ],
    "mcp": [
        lambda m: [text("先连接文档 MCP server，看看它提供了哪些工具。"),
                 tool("connect_mcp", name="docs")],
        lambda m: [tool("mcp__docs__search", query="agent loop")],
        lambda m: [text("MCP 工具已并入工具池，`mcp__docs__search` 返回了 3 条结果。")],
    ],
    "workflow": [
        lambda m: [text("用保存好的 review-changes 工作流并行审查这段改动。"),
                 tool("Workflow", name="review-changes", args={})],
        lambda m: [text("工作流跑完了：四个维度并行审查，再逐条复核，结果已汇总。")],
    ],
    "skill": [
        lambda m: [tool("load_skill", name="code-review")],
        lambda m: [text("已加载 code-review 技能的完整说明，后续会按它的清单来审查代码。")],
    ],
    "compact": [
        lambda m: [tool("compact")],
        lambda m: [text("历史已压缩为摘要，完整记录归档在 .transcripts/ 下。")],
    ],
}
KEYWORDS = [
    ("subagent", ("子任务", "子agent", "子 agent", "subagent", "调研")),
    ("team", ("团队", "队友", "team")),
    ("background", ("后台", "background")),
    ("cron", ("定时", "cron")),
    ("mcp", ("mcp",)),
    ("workflow", ("工作流", "workflow")),
    ("skill", ("技能", "skill")),
    ("compact", ("压缩", "compact")),
]


def _pick_script(request: str) -> list:
    lowered = request.lower()
    for name, words in KEYWORDS:
        if any(word in lowered for word in words):
            return SCRIPTS[name]
    return SCRIPTS["default"]


class _Messages:
    def __init__(self):
        self._goal_checks = 0

    def create(self, **request) -> Response:
        messages = request.get("messages", [])
        system = str(request.get("system", ""))
        tools = request.get("tools") or []
        prompt_chars = len(json.dumps(messages, default=str, ensure_ascii=False)) + len(system)
        last_text = _plain_text(messages[-1].get("content")) if messages else ""

        if "completion evaluator" in system:           # s17 的目标评估模型
            self._goal_checks += 1
            done = self._goal_checks % 2 == 0
            verdict = {"ok": done, "impossible": False,
                       "reason": "对话里已有命令输出作为证据。" if done
                       else "还没有看到验证结果，请运行脚本并给出输出。"}
            return Response([text(json.dumps(verdict, ensure_ascii=False))], prompt_chars)
        if not tools:                                   # 记忆挑选 / 提取 / 整理 / 摘要等辅助调用
            if "JSON" in last_text or "json" in last_text:
                return Response([text("[]")], prompt_chars)
            return Response([text("（演示模式摘要）此前的对话已被压缩。")], prompt_chars)

        request_text, rounds = _turn_state(messages)
        tool_names = {t["name"] for t in tools}
        if "subagent" in system:                        # s06 子 Agent
            steps = [lambda m: [tool("glob", pattern="*.py")],
                     lambda m: [text("工作区里有 hello.py 等示例脚本，用途是演示工具调用。")]]
        elif "todo_write" not in tool_names:            # s13 队友
            steps = [
                lambda m: [text("开始处理分配给我的任务。"),
                           tool("write_file", path="team_notes.md",
                                content="# 进展\n\n- 已梳理配置读取入口\n")],
                lambda m: ([tool("complete_task", task_id=_task_ids(m)[-1])]
                           if _task_ids(m) else [text("没有找到可完成的任务。")]),
                lambda m: [text("任务已完成，改动说明写在 team_notes.md。")],
            ]
        elif request_text.startswith("[Compacted]"):     # 压缩后的历史只剩一条摘要消息
            steps = [lambda m: [text("历史已压缩为摘要，完整记录归档在 .transcripts/ 下。")]]
        elif request_text.startswith("[Goal still active]"):
            steps = [lambda m: [text("评估认为证据不足，我重新运行一次并给出输出。"),
                                tool("bash", command="python hello.py")],
                     lambda m: [text("已补充运行结果：脚本输出了中文问候语。")]]
        elif request_text.startswith(("[Team events]", "[Scheduled]", "<task_notification>")):
            steps = [lambda m: [text("收到，我已记录这条进展，后续有新事件再继续处理。")]]
        else:
            steps = _pick_script(request_text)
        step = steps[min(rounds, len(steps) - 1)]
        return Response(step(messages), prompt_chars)

    def stream(self, **request):
        return _Stream(self.create(**request))


class _Stream:
    def __init__(self, response: Response):
        self._response = response

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    @property
    def text_stream(self):
        for block in self._response.content:
            if block.type != "text":
                continue
            for index in range(0, len(block.text), 3):
                time.sleep(DELAY)
                yield block.text[index:index + 3]

    def get_final_message(self) -> Response:
        return self._response


class MockAnthropic:
    def __init__(self, **_ignored):
        self.messages = _Messages()
