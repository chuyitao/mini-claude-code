"""前端静态文件服务器：python serve.py [端口]

和 `python -m http.server` 的区别：显式指定 .js / .css 的 MIME 类型
（部分 Windows 机器的注册表会把 .js 映射成 text/plain，导致浏览器拒绝加载 ES Module），
并关闭缓存，改完前端代码刷新即可生效。
"""
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 5173


class Handler(SimpleHTTPRequestHandler):
    extensions_map = {
        **SimpleHTTPRequestHandler.extensions_map,
        ".js": "text/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".html": "text/html; charset=utf-8",
    }

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, *_args):
        pass


if __name__ == "__main__":
    print(f"mini-claude-code frontend  http://127.0.0.1:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), partial(Handler, directory=str(ROOT))).serve_forever()
