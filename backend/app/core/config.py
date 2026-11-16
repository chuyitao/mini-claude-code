"""全局配置：环境变量、工作目录、模型客户端与各类阈值。

所有模块都从这里读取配置，避免在各处重复 load_dotenv / 创建 client。
"""
import os
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parents[2]      # .../backend
PROJECT_DIR = BACKEND_DIR.parent                       # 项目根目录

load_dotenv(BACKEND_DIR / ".env", override=True)
# 使用自定义 base_url（如 DeepSeek 的 Anthropic 兼容端点）时，
# 移除 ANTHROPIC_AUTH_TOKEN，避免两种认证方式冲突。
if os.getenv("ANTHROPIC_BASE_URL"):
    os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

# -- 工作目录：Agent 只应操作这个目录内的文件 --
WORKDIR = Path(os.getenv("AGENT_WORKDIR") or PROJECT_DIR / "workspace").resolve()
WORKDIR.mkdir(parents=True, exist_ok=True)

# -- 模型 --
MOCK_LLM = os.getenv("MOCK_LLM", "").lower() in ("1", "true", "yes")
if MOCK_LLM:
    # 离线演示模式：不需要 API Key，用脚本化的假模型驱动完整链路。
    from .mock_llm import MockAnthropic
    client = MockAnthropic()
    MODEL = os.getenv("MODEL_ID", "mock-model")
else:
    from anthropic import Anthropic
    if not os.getenv("MODEL_ID"):
        raise RuntimeError(
            "缺少 MODEL_ID：请复制 backend/.env.example 为 backend/.env 并填写，"
            "或设置 MOCK_LLM=1 使用离线演示模式。")
    client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))
    MODEL = os.environ["MODEL_ID"]
PRIMARY_MODEL = MODEL
FALLBACK_MODEL = os.getenv("FALLBACK_MODEL_ID")
GOAL_EVALUATOR_MODEL = os.getenv("GOAL_EVALUATOR_MODEL_ID") or MODEL

# -- 落盘位置 --
SKILLS_DIR = Path(os.getenv("SKILLS_DIR") or BACKEND_DIR / "skills").resolve()
TRANSCRIPT_DIR = WORKDIR / ".transcripts"
TOOL_RESULTS_DIR = WORKDIR / ".task_outputs" / "tool-results"
MEMORY_DIR = WORKDIR / ".memory"
MEMORY_INDEX = MEMORY_DIR / "MEMORY.md"

# -- 输出长度与错误恢复 --
DEFAULT_MAX_TOKENS = 8000
ESCALATED_MAX_TOKENS = 16000
MAX_RETRIES = 3
MAX_CONSECUTIVE_529 = 2
MAX_RECOVERY_RETRIES = 2
BASE_DELAY_MS = 500
CONTINUATION_PROMPT = "Continue from the previous response. Do not repeat completed work."

# -- 上下文压缩 --
CONTEXT_LIMIT = 50000
KEEP_RECENT_TOOL_RESULTS = 3
PERSIST_THRESHOLD = 30000

# -- 服务 --
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8000"))
PERMISSION_TIMEOUT = int(os.getenv("PERMISSION_TIMEOUT", "300"))   # 等待用户审批的秒数
MAX_TURN_STEPS = int(os.getenv("MAX_TURN_STEPS", "200"))           # 单轮最多循环多少步
