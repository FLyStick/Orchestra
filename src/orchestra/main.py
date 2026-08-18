"""服务入口：可直接运行 python -m orchestra.main。"""
from __future__ import annotations

import uvicorn

from .api import create_app
from .config import get_settings

app = create_app()


# 开发运行入口：读取环境配置并启动本地服务。
if __name__ == "__main__":
    settings = get_settings()
    uvicorn.run(app, host=settings.host, port=settings.port)