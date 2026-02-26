"""LLM 全局页面分析与修复建议 / LLM global page analysis and fix suggestions.

接收截图 + UI 树 + 错误上下文，返回完整修复建议。
支持 OpenAI Vision / Ollama multimodal / 任何兼容 OpenAI API 的服务。
Receives screenshot + UI tree + error context, returns fix suggestions.
Supports OpenAI Vision / Ollama multimodal / any OpenAI-compatible API.

默认关闭，需要用户配置 API key 并显式启用。
Off by default, requires API key configuration and explicit opt-in.
"""
from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from typing import Any, Optional

from phone_pilot.core.log import _log


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class LLMHealSuggestion:
    """LLM 返回的修复建议 / LLM healing suggestion.

    Attributes / 属性:
        fix_type    — 修复类型 / Fix type:
                      "popup_dismiss" / "param_adjust" / "scroll" /
                      "wait_increase" / "other"
        confidence  — 置信度 0–1 / Confidence score
        reason      — LLM 给出的原因分析 / LLM's reasoning
        fix_detail  — 具体修复参数 / Fix parameters dict
    """

    fix_type: str = "other"
    confidence: float = 0.5
    reason: str = ""
    fix_detail: dict = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.fix_detail is None:
            self.fix_detail = {}


# ---------------------------------------------------------------------------
# Prompt 模板
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
你是一个移动端自动化测试专家。脚本执行步骤失败后，你需要分析截图和 UI 结构，\
给出修复建议。请用 JSON 格式回复。"""

_USER_PROMPT_TEMPLATE = """\
脚本执行到以下步骤时失败了：

- 操作: {action}("{query}")
- 错误: {error_msg}
- 已重试: {retry_count} 次
- 应用: {app_package}
- 页面: {activity}

当前页面 UI 结构（前 50 个元素）：
{ui_summary}

{experience_section}

请分析失败原因并给出修复建议。可能的原因和修复：
1. 页面有弹窗遮挡 → fix_type="popup_dismiss", fix_detail 包含 dismiss_text
2. 目标文本格式变化 → fix_type="param_adjust", fix_detail 包含 use_regex 或 ocr_lang
3. 目标不在可视区域 → fix_type="scroll", fix_detail 包含 direction
4. 页面未加载完成 → fix_type="wait_increase", fix_detail 包含 extra_wait_s
5. 其他原因 → fix_type="other", fix_detail 包含自由建议

请返回严格 JSON（不要 markdown 代码块）：
{{"fix_type": "...", "confidence": 0.0-1.0, "reason": "...", "fix_detail": {{...}}}}"""


# ---------------------------------------------------------------------------
# LLM 调用
# ---------------------------------------------------------------------------

def _get_llm_config() -> dict:
    """从环境变量读取 LLM 配置 / Read LLM config from env vars."""
    return {
        "api_key": os.getenv("OPENAI_API_KEY", ""),
        "base_url": os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        "model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        "ollama_base_url": os.getenv("OLLAMA_BASE_URL", ""),
        "ollama_model": os.getenv("OLLAMA_MODEL", ""),
    }


def _build_ui_summary(ui_nodes: list[dict], max_nodes: int = 50) -> str:
    """从 UI 节点列表构建摘要文本 / Build summary from UI node list."""
    lines: list[str] = []
    for i, node in enumerate(ui_nodes[:max_nodes]):
        text = node.get("text", "") or ""
        desc = node.get("content_desc", "") or ""
        cls = node.get("class_name", "") or ""
        res_id = node.get("resource_id", "") or ""
        clickable = node.get("clickable", False)
        bounds = node.get("bounds", "")

        parts = []
        if text:
            parts.append(f'text="{text}"')
        if desc:
            parts.append(f'desc="{desc}"')
        if cls:
            parts.append(f"class={cls.split('.')[-1]}")
        if res_id:
            parts.append(f"id={res_id}")
        if clickable:
            parts.append("clickable")
        if bounds:
            parts.append(f"bounds={bounds}")

        lines.append(f"  [{i}] {' | '.join(parts)}")

    return "\n".join(lines) if lines else "  (无 UI 元素)"


def analyze_and_suggest(
    screenshot_bytes: Optional[bytes],
    ui_nodes: list[dict],
    error_context: dict,
    past_experiences: Optional[list[dict]] = None,
    *,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
) -> Optional[LLMHealSuggestion]:
    """完整页面分析 → 修复建议 / Full page analysis → fix suggestion.

    Parameters / 参数:
        screenshot_bytes: 当前截图 PNG bytes（可选）/ Current screenshot (optional)
        ui_nodes: UI 节点列表（dict 格式）/ UI nodes as dicts
        error_context: 错误上下文 / Error context:
            - action: 操作类型
            - query: 操作参数
            - error_msg: 错误信息
            - retry_count: 已重试次数
            - app_package: 应用包名
            - activity: Activity 名
        past_experiences: 过往部分匹配经验（供 LLM 参考）/ Past experiences
        api_key: 覆盖环境变量中的 API key
        base_url: 覆盖环境变量中的 base URL
        model: 覆盖环境变量中的模型名

    Returns / 返回值:
        LLMHealSuggestion: 修复建议（或 None）/ Fix suggestion (or None)
    """
    config = _get_llm_config()
    key = api_key or config["api_key"]
    url = base_url or config["base_url"]
    mdl = model or config["model"]

    if not key:
        _log("[self-heal] LLM 分析跳过：未配置 OPENAI_API_KEY")
        return None

    # 构建 prompt
    ui_summary = _build_ui_summary(ui_nodes)
    experience_section = ""
    if past_experiences:
        exp_lines = []
        for exp in past_experiences[:5]:
            exp_lines.append(
                f"  - fix_type={exp.get('fix_type')}, "
                f"fix_detail={json.dumps(exp.get('fix_detail', {}), ensure_ascii=False)}, "
                f"success_rate={exp.get('success_rate', '?')}"
            )
        experience_section = "历史参考经验（同一页面的过往纠正记录）：\n" + "\n".join(exp_lines)

    user_prompt = _USER_PROMPT_TEMPLATE.format(
        action=error_context.get("action", "?"),
        query=error_context.get("query", "?"),
        error_msg=error_context.get("error_msg", "未知错误"),
        retry_count=error_context.get("retry_count", 0),
        app_package=error_context.get("app_package", "?"),
        activity=error_context.get("activity", "?"),
        ui_summary=ui_summary,
        experience_section=experience_section,
    )

    # 构建消息
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _SYSTEM_PROMPT},
    ]

    if screenshot_bytes:
        # Vision API — 发送截图
        b64 = base64.b64encode(screenshot_bytes).decode("ascii")
        messages.append({
            "role": "user",
            "content": [
                {"type": "text", "text": user_prompt},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{b64}", "detail": "low"},
                },
            ],
        })
    else:
        messages.append({"role": "user", "content": user_prompt})

    # 调用 API
    try:
        result_text = _call_openai_compatible(
            messages=messages,
            api_key=key,
            base_url=url.rstrip("/"),
            model=mdl,
        )
    except Exception as exc:
        _log(f"[self-heal] LLM 调用失败: {exc}")
        return None

    if not result_text:
        return None

    # 解析 JSON 响应
    return _parse_llm_response(result_text)


def _call_openai_compatible(
    messages: list[dict],
    api_key: str,
    base_url: str,
    model: str,
    timeout: float = 30.0,
) -> Optional[str]:
    """调用 OpenAI-compatible API / Call OpenAI-compatible API."""
    import urllib.request
    import urllib.error

    url = f"{base_url}/chat/completions"
    payload = json.dumps({
        "model": model,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": 500,
    }).encode("utf-8")

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            choices = data.get("choices", [])
            if choices:
                return choices[0].get("message", {}).get("content", "")
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8")[:200]
        except Exception:
            pass
        _log(f"[self-heal] LLM HTTP {e.code}: {body}")
    except Exception as e:
        _log(f"[self-heal] LLM 请求异常: {e}")

    return None


def _parse_llm_response(text: str) -> Optional[LLMHealSuggestion]:
    """解析 LLM 的 JSON 响应 / Parse LLM JSON response."""
    # 去除 markdown 代码块包裹
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        # Remove first and last ``` lines
        if len(lines) >= 2:
            lines = lines[1:]  # Remove opening ```json or ```
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            cleaned = "\n".join(lines).strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        # 尝试提取 JSON 对象（支持嵌套）
        # 从第一个 { 开始，找到匹配的 }
        start = cleaned.find("{")
        if start >= 0:
            depth = 0
            end = start
            for i in range(start, len(cleaned)):
                if cleaned[i] == "{":
                    depth += 1
                elif cleaned[i] == "}":
                    depth -= 1
                    if depth == 0:
                        end = i + 1
                        break
            try:
                data = json.loads(cleaned[start:end])
            except json.JSONDecodeError:
                _log(f"[self-heal] LLM 响应解析失败: {text[:200]}")
                return None
        else:
            _log(f"[self-heal] LLM 响应无 JSON: {text[:200]}")
            return None

    return LLMHealSuggestion(
        fix_type=data.get("fix_type", "other"),
        confidence=float(data.get("confidence", 0.5)),
        reason=data.get("reason", ""),
        fix_detail=data.get("fix_detail", {}),
    )
