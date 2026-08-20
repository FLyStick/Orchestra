"""RAG 文档索引清单：记录已入库文档的版本、块数与状态。

向量库本身负责 chunk 级数据，ManifestStore 负责文档级管理，
两者配合实现文档列表、重复导入识别与按文档删除。
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from ..contracts.rag import DocumentRecord


class ManifestStore:
    """轻量 JSON 清单存储，使用临时文件 + 原子替换保证不写坏。"""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> list[dict[str, Any]]:
        """读取全部文档清单记录。"""
        with self._lock:
            if not self.path.exists():
                return []
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return []
            return data if isinstance(data, list) else []

    def _save(self, records: list[dict[str, Any]]) -> None:
        """原子写入清单文件，避免进程中断造成半截 JSON。"""
        temp_path = self.path.with_suffix(".tmp")
        temp_path.write_text(
            json.dumps(records, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temp_path, self.path)

    def upsert(self, record: DocumentRecord) -> None:
        """新增或按 document_id 覆盖文档记录。"""
        records = self.load()
        records = [item for item in records if item.get("document_id") != record.document_id]
        records.append(record.to_dict())
        with self._lock:
            self._save(records)

    def get(self, document_id: str) -> dict[str, Any] | None:
        """按文档 ID 查询清单记录。"""
        return next((item for item in self.load() if item.get("document_id") == document_id), None)

    def remove(self, document_id: str) -> bool:
        """删除文档清单记录，返回是否真的删除了记录。"""
        records = self.load()
        remaining = [item for item in records if item.get("document_id") != document_id]
        if len(remaining) == len(records):
            return False
        with self._lock:
            self._save(remaining)
        return True

    def list(self, department: str | None = None) -> list[dict[str, Any]]:
        """按部门筛选文档清单记录。"""
        records = self.load()
        if department:
            return [item for item in records if item.get("department") == department]
        return records
