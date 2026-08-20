"""递归文本分块：优先按段落/换行/中文句读边界切分，避免截断条款或表格行。

实现采用轻量级递归切分：先按大分隔符聚合，超长片段再降级到下一级
分隔符；最后加入少量重叠内容，保留相邻块之间的上下文。
"""
from __future__ import annotations

DEFAULT_SEPARATORS = ("\n\n", "\n", "。", "；", "，", " ", "")


def _hard_split(text: str, chunk_size: int) -> list[str]:
    """没有任何可用分隔符时的兜底切分：按固定长度硬切。"""
    return [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]


def _recursive_split(
    text: str,
    separators: tuple[str, ...],
    chunk_size: int,
) -> list[str]:
    """递归切分单段文本，返回不超过 chunk_size 的文本块列表。"""
    if len(text) <= chunk_size:
        return [text] if text.strip() else []
    if not separators or separators[0] == "":
        return _hard_split(text, chunk_size)

    separator = separators[0]
    parts = text.split(separator)
    groups: list[str] = []
    current = ""
    for part in parts:
        candidate = current + separator + part if current else part
        if len(candidate) <= chunk_size:
            current = candidate
        else:
            if current:
                groups.append(current)
            current = part
    if current:
        groups.append(current)

    result: list[str] = []
    for group in groups:
        if len(group) <= chunk_size:
            result.append(group)
        else:
            result.extend(_recursive_split(group, separators[1:], chunk_size))
    return result


def _apply_overlap(chunks: list[str], chunk_overlap: int) -> list[str]:
    """给相邻块追加前一块尾部，保持主题连续；首块不加重叠。"""
    if chunk_overlap <= 0:
        return chunks
    merged: list[str] = []
    for index, chunk in enumerate(chunks):
        if index > 0 and merged:
            merged.append(merged[-1][-chunk_overlap:] + chunk)
        else:
            merged.append(chunk)
    return merged


def split_text(
    text: str,
    chunk_size: int = 512,
    chunk_overlap: int = 64,
) -> list[str]:
    """把长文本切成可检索的知识块。

    Args:
        text: 原始文本，可能包含 Markdown/表格/换行结构。
        chunk_size: 单块目标最大字符数。
        chunk_overlap: 相邻块重叠字符数，默认 64。

    Returns:
        非空文本块列表；空文本返回空列表。
    """
    raw = (text or "").strip()
    if not raw:
        return []
    separators = DEFAULT_SEPARATORS if chunk_size >= 20 else (" ", "")
    chunks = _recursive_split(raw, separators, max(20, chunk_size))
    return _apply_overlap(chunks, chunk_overlap)
