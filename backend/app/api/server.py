"""FastAPI 应用装配：跨域、路由、启动时拉起 Agent 运行时。"""
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ..service import service
from .routes import router


@asynccontextmanager
async def lifespan(_app: FastAPI):
    service.start()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="mini-claude-code", version="0.1.0", lifespan=lifespan)
    # 前后端分离：前端由另一个端口（或静态文件服务器）提供，这里放开本地跨域
    origins = [origin.strip() for origin in
               os.getenv("CORS_ORIGINS", "*").split(",") if origin.strip()]
    app.add_middleware(CORSMiddleware, allow_origins=origins,
                       allow_methods=["*"], allow_headers=["*"])
    app.include_router(router)
    return app


app = create_app()
