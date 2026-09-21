"""类别名称与类别目录的安全约束。"""

from __future__ import annotations

import re
from pathlib import Path

_INVALID_CATEGORY_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def validate_category_name(name: str) -> str:
    """校验并返回可作为目录名的类别名称。"""
    value = str(name or "").strip()
    if not value or value in {".", ".."}:
        raise ValueError("类别名称不能为空")
    if len(value) > 100 or _INVALID_CATEGORY_CHARS.search(value):
        raise ValueError("类别名称包含无效字符")
    if value[-1] in {".", " "}:
        raise ValueError("类别名称不能以点或空格结尾")
    if value.split(".", 1)[0].upper() in _WINDOWS_RESERVED:
        raise ValueError("类别名称不能使用系统保留名称")
    return value


def category_dir(cache_dir, parts) -> Path:
    """返回类别目录；parts 必须来自已校验的数据库类别名称。"""
    result = Path(cache_dir)
    for part in parts or ():
        result /= validate_category_name(part)
    return result


def remove_empty_parents(path, root) -> None:
    """清理移动原件后留下的空类别目录，不删除存储根目录。"""
    current = Path(path)
    root = Path(root)
    try:
        current = current.resolve()
        root = root.resolve()
    except OSError:
        return
    while current != root and root in current.parents:
        try:
            current.rmdir()
        except OSError:
            break
        current = current.parent
