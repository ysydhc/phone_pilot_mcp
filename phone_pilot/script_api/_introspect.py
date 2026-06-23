"""script_api 自省 / Introspection for the script_api public surface.

提供 list_public_api()：遍历 ``script_api.__all__`` 中的公开函数，
用 inspect 提取签名与 docstring 首行。CLI(``--list-script-api``）与文档
从同一数据源生成，避免手写清单随代码漂移而过时。
Single source of truth for the public function list so the CLI and docs never
drift from the code.
"""

from __future__ import annotations

import inspect
from typing import Any, Callable


def _first_doc_line(fn: Callable[..., Any]) -> str:
    """Return the first non-empty line of a function's docstring."""
    doc = inspect.getdoc(fn) or ""
    for line in doc.splitlines():
        line = line.strip()
        if line:
            return line
    return ""


def list_public_api() -> list[dict[str, str]]:
    """列出 script_api 的公开函数及其签名与简介。
    List public script_api functions with signature and one-line summary.

    Returns:
        list of {"name", "signature", "summary"}, sorted by name.
        仅包含可调用且非下划线开头的公开名（排除类与异常）。
    """
    from phone_pilot import script_api as api

    items: list[dict[str, str]] = []
    for name in getattr(api, "__all__", []):
        if name.startswith("_"):
            continue
        obj = getattr(api, name, None)
        if not callable(obj):
            continue
        # Skip classes/exceptions — list functions only.
        if inspect.isclass(obj):
            continue
        try:
            sig = str(inspect.signature(obj))
        except (TypeError, ValueError):
            sig = "(...)"
        items.append(
            {
                "name": name,
                "signature": f"{name}{sig}",
                "summary": _first_doc_line(obj),
            }
        )
    items.sort(key=lambda d: d["name"])
    return items


def format_public_api(items: list[dict[str, str]] | None = None) -> str:
    """Render list_public_api() as a readable text block (for CLI output)."""
    if items is None:
        items = list_public_api()
    lines: list[str] = []
    lines.append(f"script_api public functions ({len(items)}):")
    lines.append("=" * 64)
    for it in items:
        lines.append(it["signature"])
        if it["summary"]:
            lines.append(f"    {it['summary']}")
        lines.append("")
    return "\n".join(lines)
