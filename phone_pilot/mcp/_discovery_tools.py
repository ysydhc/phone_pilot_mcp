"""Meta-tool for discovering and loading extended MCP tools on demand.

Registers ``phone_discover_tools`` which allows agents to browse available
tool categories and dynamically activate them at runtime.
"""

from __future__ import annotations


from mcp.server.fastmcp import Context

from phone_pilot.mcp.server import mcp
from phone_pilot.mcp._tool_registry import (
    EXTENDED_CATEGORIES,
    activate_by_keyword,
    activate_category,
    get_category_summary,
    get_hidden_tool_info,
)


@mcp.tool()
async def phone_discover_tools(
    category: str = "",
    keyword: str = "",
    ctx: Context | None = None,
) -> dict:
    """发现并加载扩展工具。当你需要的功能不在默认工具列表中时调用此工具。
    Discover and dynamically load extended tools by category or keyword.

    Parameters / 参数:
        category: 工具类别。可选值: device, input, ui_find, app, recording, logcat, advanced。
                  留空则列出所有类别概览。
                  Tool category. Options: device, input, ui_find, app, recording, logcat, advanced.
                  Leave empty to list all categories.
        keyword: 按关键词搜索工具名称和描述 / Search tool names and descriptions by keyword.

    Returns / 返回值:
        - 无参数: 返回所有类别概览 / No args: return category overview
        - category: 加载该类别工具并返回列表 / Load tools in category and return list
        - keyword: 搜索并加载匹配的工具 / Search and load matching tools

    使用场景 / When to use:
        - 需要录屏: phone_discover_tools(category="recording")
        - 需要日志: phone_discover_tools(category="logcat")
        - 需要安装应用: phone_discover_tools(category="app")
        - 不确定哪个类别: phone_discover_tools() 查看全部
    """
    if category:
        return await _activate_category(category, ctx)
    if keyword:
        return await _search_keyword(keyword, ctx)
    return _list_categories()


def _list_categories() -> dict:
    """Return overview of all extended tool categories."""
    summary = get_category_summary()
    return {
        "ok": True,
        "message": "Extended tool categories. Use category param to load tools.",
        "categories": summary,
        "total_hidden": sum(s["pending_count"] for s in summary),
    }


async def _activate_category(category: str, ctx: Context | None) -> dict:
    """Activate a tool category and notify the client."""
    if category not in EXTENDED_CATEGORIES:
        available = list(EXTENDED_CATEGORIES.keys())
        return {
            "ok": False,
            "error": f"Unknown category: '{category}'. Available: {available}",
        }

    activated = activate_category(mcp, category)

    # Notify client that tool list has changed
    if activated and ctx is not None:
        try:
            await ctx.session.send_tool_list_changed()
        except Exception:
            pass  # Client may not support notifications

    return {
        "ok": True,
        "category": category,
        "activated_tools": activated,
        "activated_count": len(activated),
        "message": f"Loaded {len(activated)} tools from '{category}'. They are now available for use."
        if activated
        else f"Category '{category}' was already loaded.",
    }


async def _search_keyword(keyword: str, ctx: Context | None) -> dict:
    """Search hidden tools by keyword and activate matches."""
    activated = activate_by_keyword(mcp, keyword)

    if activated and ctx is not None:
        try:
            await ctx.session.send_tool_list_changed()
        except Exception:
            pass

    if not activated:
        # Show what's hidden to help user refine search
        hidden = get_hidden_tool_info()
        return {
            "ok": True,
            "keyword": keyword,
            "activated_tools": [],
            "activated_count": 0,
            "message": f"No hidden tools matched '{keyword}'.",
            "hint": f"{len(hidden)} tools are still hidden. Try a different keyword or use category param.",
        }

    return {
        "ok": True,
        "keyword": keyword,
        "activated_tools": activated,
        "activated_count": len(activated),
        "message": f"Found and loaded {len(activated)} tools matching '{keyword}'.",
    }
