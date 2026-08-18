"""本地文件版会话工作区：每个 session 一个隔离目录。

每个会话（session）在根目录下拥有独立的子目录，子任务结果、
中间产物和最终答案都写入该目录，供其他 Agent 复用与追溯。
"""
from __future__ import annotations

import asyncio
from pathlib import Path


class LocalWorkspace:
    """基于本地文件系统的会话工作区实现。

    所有读写操作都限定在会话专属目录内，并通过 asyncio.to_thread
    把阻塞的文件 IO 放到线程池执行，避免阻塞事件循环。
    """

    def __init__(self, root: Path, session_id: str) -> None:
        """初始化工作区。

        Args:
            root: 工作区根目录（所有会话目录的父目录）。
            session_id: 会话标识，用于生成隔离的会话子目录。
        """
        self._session_id = session_id
        # 会话目录 = root / session_id，resolve 归一化路径（解析 .. 与符号链接）。
        self._root = (root / session_id).resolve()
        # 目录不存在时递归创建。
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def session_id(self) -> str:
        """返回会话标识。"""
        return self._session_id

    @property
    def root(self) -> Path:
        """返回会话工作区根目录路径。"""
        return self._root

    # 校验路径位于工作区根目录内，阻止 ../ 逃逸。
    def _resolve(self, path: str) -> Path:
        """把相对路径解析为工作区内的绝对路径，并做安全校验。

        Args:
            path: 工作区内的相对路径。

        Returns:
            归一化后的绝对路径。

        Raises:
            ValueError: 路径解析后超出工作区根目录（如包含 ../ 逃逸）时抛出。
        """
        target = (self._root / path).resolve()
        # 安全校验：解析后的路径必须仍位于工作区根目录内，防止目录穿越。
        if not target.is_relative_to(self._root):
            raise ValueError(f"path escapes workspace: {path}")
        return target

    async def read(self, path: str) -> str | None:
        """读取工作区内的文件内容。

        Args:
            path: 工作区内的相对路径。

        Returns:
            文件内容字符串；文件不存在时返回 None。
        """
        target = self._resolve(path)
        if not target.exists():
            return None
        # 文件 IO 放到线程池执行，避免阻塞事件循环。
        return await asyncio.to_thread(target.read_text, encoding="utf-8")

    # 子任务或最终答案统一写入工作区，供其他 Agent 复用。
    async def write(self, path: str, content: str) -> None:
        """写入文件到工作区（自动创建父目录）。

        Args:
            path: 工作区内的相对路径。
            content: 要写入的文件内容。
        """
        target = self._resolve(path)
        # 自动创建父目录，支持写入嵌套路径（如 subtasks/t1.md）。
        target.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(target.write_text, content, encoding="utf-8")

    async def list_files(self) -> list[str]:
        """列出工作区内所有文件（递归，按路径排序）。

        Returns:
            相对路径列表，使用 POSIX 风格分隔符（如 "subtasks/t1.md"）。
        """

        def _list() -> list[str]:
            # 递归遍历工作区，只保留文件，并转成相对根目录的 POSIX 路径。
            return sorted(
                p.relative_to(self._root).as_posix()
                for p in self._root.rglob("*")
                if p.is_file()
            )

        return await asyncio.to_thread(_list)