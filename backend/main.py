"""后端启动入口：python main.py"""
import sys

import uvicorn

from app.core.config import HOST, MOCK_LLM, MODEL, PORT, WORKDIR

if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):        # Windows 终端默认编码打不出部分字符
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    print(f"mini-claude-code backend  http://{HOST}:{PORT}")
    print(f"  model     : {MODEL}{'  (offline demo)' if MOCK_LLM else ''}")
    print(f"  workspace : {WORKDIR}")
    uvicorn.run("app.api.server:app", host=HOST, port=PORT, log_level="warning")
