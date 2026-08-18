"""内存版工作区，用于关闭磁盘写入的场景。"""
from __future__ import annotations

from typing import Any


# 仅保存在内存中，进程重启后数据不保留。
class MemoryWorkspace:
    def __init__(self, session_id: str) -> None:
        self._session_id = session_id
        self._files: dict[str, str] = {}

    @property
    def session_id(self) -> str:
        return self._session_id

    async def read(self, path: str) -> str | None:
        return self._files.get(path)

    async def write(self, path: str, content: str) -> None:
        self._files[path] = content

    async def list_files(self) -> list[str]:
        return sorted(self._files)