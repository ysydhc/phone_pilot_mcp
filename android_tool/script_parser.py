#!/usr/bin/env python3
"""
简易脚本格式解析器

将人类可读的 .script 格式转换为 JSON 工作流，或将 JSON 转换为可读格式。

简易格式示例:
```
# 工作流: 测试名称
# 说明: 这是一个测试

---

1. 清空后台进程

2. 从桌面启动应用 "微信"
   - 等待 2秒

3. 点击文字 "发现"

4. 点击图片 @templates/icon.png
   - 相似度: 80%

5. 等待 1.5秒

6. 向上滑动页面

7. 截图 "result"

8. 重复 3次:
    - 向下滑动页面
    - 等待 2秒
```
"""

from __future__ import annotations
import json
import re
import os
from pathlib import Path
from typing import Any

# 中文动作映射
ACTION_PATTERNS = {
    # 基础操作
    r"清空后台进程": {"type": "clear_background_processes", "mode": "force_stop_3p", "run_kill_all_first": True},
    r"等待\s*([\d.]+)\s*秒": lambda m: {"type": "sleep", "seconds": float(m.group(1))},
    r"截图\s*[\"']?([^\"']*)[\"']?": lambda m: {"type": "screenshot", "name": m.group(1).strip() or "screenshot"},
    
    # 启动应用
    r"从桌面启动应用?\s*[\"']([^\"']+)[\"']": lambda m: {
        "type": "launch_from_home",
        "query": m.group(1),
        "reset_home": True,
        "home_presses": 2,
    },
    r"启动应用?\s*[\"']([^\"']+)[\"']": lambda m: {
        "type": "launch_from_home", 
        "query": m.group(1),
    },
    
    # 点击操作
    r"点击文字\s*[\"']([^\"']+)[\"']": lambda m: {
        "type": "find_and_tap",
        "query": m.group(1),
        "scroll_on_fail": False,
    },
    r"点击\s*[\"']([^\"']+)[\"']": lambda m: {
        "type": "find_and_tap",
        "query": m.group(1),
        "scroll_on_fail": False,
    },
    r"点击图片\s*@?([^\s]+)": lambda m: {
        "type": "find_image_and_tap",
        "template_path": m.group(1),
        "scroll_on_fail": False,
    },
    r"点击坐标\s*\(?\s*(\d+)\s*,\s*(\d+)\s*\)?": lambda m: {
        "type": "tap",
        "x": int(m.group(1)),
        "y": int(m.group(2)),
    },
    
    # 滑动操作
    r"向上滑动页面": {"type": "swipe", "x1_pct": 0.5, "y1_pct": 0.75, "x2_pct": 0.5, "y2_pct": 0.35, "duration_ms": 350},
    r"向下滑动页面": {"type": "swipe", "x1_pct": 0.5, "y1_pct": 0.35, "x2_pct": 0.5, "y2_pct": 0.75, "duration_ms": 350},
    r"向左滑动页面": {"type": "swipe", "x1_pct": 0.75, "y1_pct": 0.5, "x2_pct": 0.25, "y2_pct": 0.5, "duration_ms": 350},
    r"向右滑动页面": {"type": "swipe", "x1_pct": 0.25, "y1_pct": 0.5, "x2_pct": 0.75, "y2_pct": 0.5, "duration_ms": 350},
    
    # 输入操作
    r"输入文字\s*[\"']([^\"']+)[\"']": lambda m: {"type": "type_text", "text": m.group(1)},
    r"输入\s*[\"']([^\"']+)[\"']": lambda m: {"type": "type_text", "text": m.group(1)},
    
    # 按键操作
    r"按返回键": {"type": "key", "keycode": "KEYCODE_BACK"},
    r"按主页键": {"type": "key", "keycode": "KEYCODE_HOME"},
    r"按菜单键": {"type": "key", "keycode": "KEYCODE_MENU"},
    r"回到桌面": {"type": "reset_home"},
    
    # 应用操作
    r"重启应用\s*[\"']([^\"']+)[\"']": lambda m: {"type": "restart_app", "package": m.group(1)},
    r"关闭应用\s*[\"']([^\"']+)[\"']": lambda m: {"type": "force_stop", "package": m.group(1)},
    r"确保启动应用?\s*[\"']([^\"']+)[\"']": lambda m: {"type": "ensure_launch", "query": m.group(1), "reset_home": True},
    
    # 验证操作
    r"断言\s*(.+)": lambda m: {"type": "assert", "expr": m.group(1).strip(), "fatal": True},
    r"等待出现\s*[\"']([^\"']+)[\"']": lambda m: {"type": "wait_for_ui", "query": m.group(1)},
    r"等待页面\s*[\"']([^\"']+)[\"']": lambda m: {"type": "wait_for_focus", "activity_contains": m.group(1)},
    
    # 采集操作
    r"采集信息": {"type": "collect_artifacts", "include_focus": True, "include_screenshot": True},
    r"触发GC": {"type": "trigger_gc"},
    r"关闭弹窗": {"type": "dismiss_popups", "max_rounds": 3},
    
    # 设置操作
    r"设置选项\s*(.+)": lambda m: {"type": "set_options", "values": {"fail_fast": True}},
    
    # 内存操作
    r"保存内存快照\s*[\"']?([^\"']*)[\"']?": lambda m: {
        "type": "dump_hprof",
        "name": m.group(1).strip() or "memory",
        "fallback_to_meminfo": True,
    },
    
    # 重复操作 (特殊处理)
    r"重复\s*(\d+)\s*次\s*:?": lambda m: {"type": "repeat", "count": int(m.group(1)), "steps": []},
}

# 选项/属性映射
OPTION_PATTERNS = {
    r"等待\s*([\d.]+)\s*秒": lambda m, s: s.update({"wait_s": float(m.group(1))}),
    r"偏移[:：]\s*向右\s*(\d+)\s*像素?\s*,\s*向下\s*(\d+)\s*像素?": lambda m, s: s.update({
        "tap_offset_x": int(m.group(1)),
        "tap_offset_y": int(m.group(2)),
    }),
    r"偏移[:：]\s*\(?\s*(\d+)\s*,\s*(\d+)\s*\)?": lambda m, s: s.update({
        "tap_offset_x": int(m.group(1)),
        "tap_offset_y": int(m.group(2)),
    }),
    r"相似度[:：]\s*(\d+)\s*%?": lambda m, s: s.update({"threshold": float(m.group(1)) / 100}),
    r"语言[:：]\s*(中文|英文|中英混合)": lambda m, s: s.update({
        "ocr_lang": {"中文": "chi_sim", "英文": "eng", "中英混合": "eng+chi_sim"}.get(m.group(1), "eng+chi_sim")
    }),
    r"搜索区域[:：]\s*屏幕(上半|下半|中上|中下|全屏)部分?": lambda m, s: _set_roi(m.group(1), s),
    r"说明[:：]\s*(.+)": lambda m, s: s.update({"comment": m.group(1).strip()}),
    r"起点[:：]\s*[（(]?\s*([\d.]+)\s*%?\s*,\s*([\d.]+)\s*%?\s*[)）]?": lambda m, s: s.update({
        "x1_pct": float(m.group(1)) / 100 if float(m.group(1)) > 1 else float(m.group(1)),
        "y1_pct": float(m.group(2)) / 100 if float(m.group(2)) > 1 else float(m.group(2)),
    }),
    r"终点[:：]\s*[（(]?\s*([\d.]+)\s*%?\s*,\s*([\d.]+)\s*%?\s*[)）]?": lambda m, s: s.update({
        "x2_pct": float(m.group(1)) / 100 if float(m.group(1)) > 1 else float(m.group(1)),
        "y2_pct": float(m.group(2)) / 100 if float(m.group(2)) > 1 else float(m.group(2)),
    }),
}


def _set_roi(region: str, step: dict):
    """设置搜索区域 ROI"""
    # 假设屏幕 1080x2400
    roi_map = {
        "上半": [0, 0, 1080, 1200],
        "下半": [0, 1200, 1080, 1200],
        "中上": [0, 400, 1080, 1200],
        "中下": [0, 800, 1080, 1600],
        "全屏": None,
    }
    roi = roi_map.get(region)
    if roi:
        step["roi"] = roi


def parse_script(script_text: str, base_dir: str | None = None) -> dict:
    """
    解析简易脚本格式，返回 JSON 工作流结构。
    
    Args:
        script_text: 脚本文本
        base_dir: 基础目录，用于解析相对路径（如 @templates/...）
    """
    lines = script_text.strip().split("\n")
    
    workflow = {
        "name": "workflow",
        "device_serial": None,
        "clear_logcat_first": True,
        "final_collect_artifacts": True,
        "record_screen": False,
        "steps": [],
    }
    
    current_step = None
    repeat_stack = []  # 用于处理嵌套的重复
    indent_level = 0
    in_repeat_block = False
    
    for line in lines:
        line_stripped = line.strip()
        
        # 跳过空行和分隔符
        if not line_stripped or line_stripped == "---" or line_stripped == "# 完成":
            continue
        
        # 解析元数据
        if line_stripped.startswith("# 工作流:") or line_stripped.startswith("# 工作流："):
            workflow["name"] = line_stripped.split(":", 1)[-1].split("：", 1)[-1].strip()
            continue
        
        # 跳过注释和章节标题
        if line_stripped.startswith("#") or line_stripped.startswith("##"):
            continue
        
        # 计算缩进级别
        current_indent = len(line) - len(line.lstrip())
        
        # 检查是否是选项行（以 - 开头）
        if line_stripped.startswith("-"):
            option_text = line_stripped[1:].strip()
            if current_step:
                # 解析选项
                for pattern, handler in OPTION_PATTERNS.items():
                    match = re.match(pattern, option_text, re.IGNORECASE)
                    if match:
                        handler(match, current_step)
                        break
                else:
                    # 可能是重复块内的子步骤
                    if repeat_stack:
                        sub_step = _parse_action(option_text)
                        if sub_step:
                            repeat_stack[-1]["steps"].append(sub_step)
                            current_step = sub_step
            continue
        
        # 解析步骤行（以数字开头）
        step_match = re.match(r"^(\d+)\.\s*(.+)$", line_stripped)
        if step_match:
            step_num, action_text = step_match.groups()
            step = _parse_action(action_text)
            if step:
                # 处理重复步骤
                if step.get("type") == "repeat":
                    repeat_stack.append(step)
                    workflow["steps"].append(step)
                else:
                    if repeat_stack:
                        # 如果在重复块内，添加到重复步骤
                        repeat_stack[-1]["steps"].append(step)
                    else:
                        workflow["steps"].append(step)
                current_step = step
            continue
        
        # 处理重复块结束（通过缩进判断）
        if current_indent == 0 and repeat_stack:
            repeat_stack.clear()
    
    return workflow


def _parse_action(text: str) -> dict | None:
    """解析单个动作文本"""
    for pattern, result in ACTION_PATTERNS.items():
        match = re.match(pattern, text, re.IGNORECASE)
        if match:
            if callable(result):
                return result(match)
            else:
                return dict(result)
    return None


def workflow_to_script(workflow: dict) -> str:
    """
    将 JSON 工作流转换为简易脚本格式。
    """
    lines = []
    
    # 元数据
    name = workflow.get("name", "workflow")
    lines.append(f"# 工作流: {name}")
    lines.append("")
    lines.append("---")
    lines.append("")
    
    step_num = 1
    steps = workflow.get("steps", [])
    
    for step in steps:
        step_text, options = _step_to_text(step)
        if step_text:
            lines.append(f"{step_num}. {step_text}")
            for opt in options:
                lines.append(f"   - {opt}")
            lines.append("")
            step_num += 1
    
    lines.append("---")
    lines.append("# 完成")
    
    return "\n".join(lines)


def _step_to_text(step: dict) -> tuple[str, list[str]]:
    """将单个步骤转换为文本"""
    step_type = step.get("type", "")
    options = []
    
    # 添加注释
    if step.get("comment"):
        options.append(f"说明: {step['comment']}")
    
    if step_type == "clear_background_processes":
        return "清空后台进程", options
    
    elif step_type == "sleep":
        return f"等待 {step.get('seconds', 1)} 秒", options
    
    elif step_type == "screenshot":
        name = step.get("name", "screenshot")
        return f'截图 "{name}"', options
    
    elif step_type == "launch_from_home":
        query = step.get("query", "")
        if step.get("wait_s"):
            options.append(f"等待 {step['wait_s']}秒")
        return f'从桌面启动应用 "{query}"', options
    
    elif step_type == "find_and_tap":
        query = step.get("query", "")
        if step.get("tap_offset_x") or step.get("tap_offset_y"):
            ox, oy = step.get("tap_offset_x", 0), step.get("tap_offset_y", 0)
            options.append(f"偏移: 向右{ox}像素, 向下{oy}像素")
        if step.get("ocr_lang"):
            lang_map = {"chi_sim": "中文", "eng": "英文", "eng+chi_sim": "中英混合"}
            options.append(f"语言: {lang_map.get(step['ocr_lang'], step['ocr_lang'])}")
        return f'点击文字 "{query}"', options
    
    elif step_type == "find_image_and_tap":
        path = step.get("template_path", "")
        # 简化路径
        if "/" in path:
            path = "@" + "/".join(path.split("/")[-2:])
        if step.get("threshold"):
            options.append(f"相似度: {int(step['threshold'] * 100)}%")
        if step.get("roi"):
            options.append(f"搜索区域: 自定义")
        return f"点击图片 {path}", options
    
    elif step_type == "swipe":
        y1, y2 = step.get("y1_pct", 0.5), step.get("y2_pct", 0.5)
        x1, x2 = step.get("x1_pct", 0.5), step.get("x2_pct", 0.5)
        if y1 > y2:
            direction = "向上滑动页面"
        elif y1 < y2:
            direction = "向下滑动页面"
        elif x1 > x2:
            direction = "向左滑动页面"
        else:
            direction = "向右滑动页面"
        options.append(f"起点: ({int(x1*100)}%, {int(y1*100)}%)")
        options.append(f"终点: ({int(x2*100)}%, {int(y2*100)}%)")
        return direction, options
    
    elif step_type == "type_text":
        text = step.get("text", "")
        return f'输入文字 "{text}"', options
    
    elif step_type == "key":
        keycode = step.get("keycode", "")
        key_map = {
            "KEYCODE_BACK": "按返回键",
            "KEYCODE_HOME": "按主页键",
            "KEYCODE_MENU": "按菜单键",
        }
        return key_map.get(keycode, f"按键 {keycode}"), options
    
    elif step_type == "dump_hprof":
        name = step.get("name", "memory")
        return f'保存内存快照 "{name}"', options
    
    elif step_type == "repeat":
        count = step.get("count", 1)
        sub_steps = step.get("steps", [])
        sub_texts = []
        for sub in sub_steps:
            sub_text, sub_opts = _step_to_text(sub)
            if sub_text:
                sub_texts.append(f"    - {sub_text}")
                for opt in sub_opts:
                    sub_texts.append(f"      - {opt}")
        options.extend(sub_texts)
        return f"重复 {count}次:", options
    
    elif step_type == "tap":
        x, y = step.get("x", 0), step.get("y", 0)
        return f"点击坐标 ({x}, {y})", options
    
    elif step_type == "reset_home":
        return "回到桌面", options
    
    elif step_type == "restart_app":
        package = step.get("package", "")
        return f'重启应用 "{package}"', options
    
    elif step_type == "force_stop":
        package = step.get("package", "")
        return f'关闭应用 "{package}"', options
    
    elif step_type == "ensure_launch":
        query = step.get("query", step.get("package", ""))
        if step.get("wait_s"):
            options.append(f"等待 {step['wait_s']}秒")
        return f'确保启动应用 "{query}"', options
    
    elif step_type == "assert":
        expr = step.get("expr", "")
        if step.get("message"):
            options.append(f"错误提示: {step['message']}")
        return f"断言 {expr}", options
    
    elif step_type == "wait_for_ui":
        query = step.get("query", "")
        if step.get("timeout_s"):
            options.append(f"超时: {step['timeout_s']}秒")
        return f'等待出现 "{query}"', options
    
    elif step_type == "wait_for_focus":
        activity = step.get("activity_contains", "")
        return f'等待页面 "{activity}"', options
    
    elif step_type == "collect_artifacts":
        return "采集信息", options
    
    elif step_type == "trigger_gc":
        return "触发GC", options
    
    elif step_type == "dismiss_popups":
        if step.get("max_rounds"):
            options.append(f"最多 {step['max_rounds']} 轮")
        return "关闭弹窗", options
    
    elif step_type == "set_options":
        values = step.get("values", {})
        opts_str = ", ".join(f"{k}={v}" for k, v in values.items())
        return f"设置选项 {opts_str}", options
    
    elif step_type == "keyevent":
        keycode = step.get("keycode", "")
        return f"按键 {keycode}", options
    
    elif step_type == "input_text":
        text = step.get("text", "")
        return f'输入文字 "{text}"', options
    
    elif step_type == "diff_hprof":
        return "对比内存快照", options
    
    elif step_type == "analyze_hprof":
        return "分析内存快照", options
    
    elif step_type == "analyze_bitmaps":
        return "分析位图", options
    
    elif step_type == "capture_meminfo":
        return "采集内存信息", options
    
    elif step_type == "find_multi_stage":
        return "多阶段查找", options
    
    elif step_type == "find_on_screen":
        query = step.get("query", "")
        return f'查找 "{query}"', options
    
    return f"[未知步骤: {step_type}]", options


def convert_script_to_json(script_path: str, output_path: str | None = None) -> str:
    """将 .script 文件转换为 .json 文件"""
    script_path = Path(script_path)
    if not script_path.exists():
        raise FileNotFoundError(f"Script file not found: {script_path}")
    
    script_text = script_path.read_text(encoding="utf-8")
    workflow = parse_script(script_text)
    
    if output_path is None:
        output_path = script_path.with_suffix(".json")
    
    output_path = Path(output_path)
    output_path.write_text(json.dumps(workflow, ensure_ascii=False, indent=2), encoding="utf-8")
    
    return str(output_path)


def convert_json_to_script(json_path: str, output_path: str | None = None) -> str:
    """将 .json 文件转换为 .script 文件"""
    json_path = Path(json_path)
    if not json_path.exists():
        raise FileNotFoundError(f"JSON file not found: {json_path}")
    
    workflow = json.loads(json_path.read_text(encoding="utf-8"))
    script_text = workflow_to_script(workflow)
    
    if output_path is None:
        output_path = json_path.with_suffix(".script")
    
    output_path = Path(output_path)
    output_path.write_text(script_text, encoding="utf-8")
    
    return str(output_path)


def parse_markdown_workflow(md_text: str) -> dict:
    """
    从 Markdown 文件中提取工作流。
    支持两种格式：
    1. JSON 代码块（```json ... ```）
    2. 简易脚本代码块（```script ... ``` 或 ```markdown ... ```）
    """
    # 尝试提取 JSON 代码块
    json_match = re.search(r"```json\s*\n(.*?)\n```", md_text, re.DOTALL)
    if json_match:
        json_str = json_match.group(1)
        return json.loads(json_str)
    
    # 尝试提取简易脚本代码块
    script_match = re.search(r"```(?:script|简易脚本)\s*\n(.*?)\n```", md_text, re.DOTALL)
    if script_match:
        script_text = script_match.group(1)
        return parse_script(script_text)
    
    # 如果没有代码块，尝试直接解析整个内容（跳过标题行）
    lines = md_text.strip().split("\n")
    # 跳过第一行如果是 # 开头的标题
    if lines and lines[0].startswith("# "):
        # 检查第二行是否是描述
        start_idx = 1
        if len(lines) > 1 and not lines[1].startswith("#") and not lines[1].startswith("-") and not re.match(r"^\d+\.", lines[1]):
            start_idx = 2
        script_lines = lines[start_idx:]
        script_text = "\n".join(script_lines)
        return parse_script(script_text)
    
    return parse_script(md_text)


def _resolve_template_paths(workflow: dict, base_dir: Path) -> dict:
    """解析工作流中的模板路径，将相对路径转为绝对路径"""
    
    def resolve_path(path: str) -> str:
        if not path:
            return path
        # 处理 @templates/xxx 格式
        if path.startswith("@"):
            path = path[1:]
        # 处理 templates/xxx 格式
        if path.startswith("templates/"):
            # 尝试在 recordings/templates 目录下查找
            full_path = base_dir / "recordings" / path
            if full_path.exists():
                return str(full_path)
            # 尝试在项目根目录下查找
            full_path = base_dir / path
            if full_path.exists():
                return str(full_path)
        # 如果是相对路径，转为绝对路径
        if not os.path.isabs(path):
            full_path = base_dir / path
            if full_path.exists():
                return str(full_path)
        return path
    
    def process_step(step: dict) -> dict:
        if isinstance(step.get("template_path"), str):
            step["template_path"] = resolve_path(step["template_path"])
        if isinstance(step.get("steps"), list):
            step["steps"] = [process_step(s) for s in step["steps"]]
        return step
    
    if "steps" in workflow:
        workflow["steps"] = [process_step(s) for s in workflow["steps"]]
    
    return workflow


def load_workflow_from_file(file_path: str) -> dict:
    """
    从文件加载工作流，自动识别格式。
    支持: .json, .script, .md
    """
    file_path = Path(file_path)
    if not file_path.exists():
        raise FileNotFoundError(f"Workflow file not found: {file_path}")
    
    content = file_path.read_text(encoding="utf-8")
    suffix = file_path.suffix.lower()
    
    # 确定基础目录（用于解析相对路径）
    base_dir = file_path.parent
    # 尝试找到项目根目录（包含 recordings 目录的目录）
    for parent in [file_path.parent] + list(file_path.parents):
        if (parent / "recordings").exists():
            base_dir = parent
            break
    
    if suffix == ".json":
        workflow = json.loads(content)
    elif suffix == ".script":
        workflow = parse_script(content, str(base_dir))
    elif suffix == ".md":
        workflow = parse_markdown_workflow(content)
    else:
        # 尝试自动检测格式
        content_stripped = content.strip()
        if content_stripped.startswith("{"):
            workflow = json.loads(content)
        elif "```json" in content or "```script" in content:
            workflow = parse_markdown_workflow(content)
        else:
            workflow = parse_script(content, str(base_dir))
    
    # 解析模板路径
    workflow = _resolve_template_paths(workflow, base_dir)
    
    return workflow


def convert_md_to_simple_script(md_path: str, output_path: str | None = None) -> str:
    """
    将包含 JSON 的 Markdown 文件转换为简易脚本格式的 Markdown。
    """
    md_path = Path(md_path)
    if not md_path.exists():
        raise FileNotFoundError(f"Markdown file not found: {md_path}")
    
    content = md_path.read_text(encoding="utf-8")
    
    # 提取标题和描述
    lines = content.strip().split("\n")
    title = ""
    description = ""
    
    if lines and lines[0].startswith("# "):
        title = lines[0][2:].strip()
        if len(lines) > 1 and not lines[1].startswith("#") and not lines[1].startswith("```"):
            description = lines[1].strip()
    
    # 解析工作流
    workflow = parse_markdown_workflow(content)
    
    # 生成简易脚本
    script_text = workflow_to_script(workflow)
    
    # 组合新的 Markdown
    new_md_lines = [
        f"# {title or workflow.get('name', 'workflow')}",
        "",
        description if description else f"工作流描述：{workflow.get('name', '')}",
        "",
        "```script",
        script_text,
        "```",
    ]
    
    new_content = "\n".join(new_md_lines)
    
    if output_path is None:
        output_path = md_path
    
    output_path = Path(output_path)
    output_path.write_text(new_content, encoding="utf-8")
    
    return str(output_path)


def main():
    """CLI 入口"""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="简易脚本格式转换工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 将 .script 转换为 .json
  python script_parser.py convert my_workflow.script
  
  # 将 .json 转换为 .script
  python script_parser.py convert my_workflow.json
  
  # 指定输出路径
  python script_parser.py convert input.script -o output.json
        """
    )
    
    subparsers = parser.add_subparsers(dest="command", help="命令")
    
    # convert 命令
    convert_parser = subparsers.add_parser("convert", help="转换脚本格式")
    convert_parser.add_argument("input", help="输入文件 (.script 或 .json)")
    convert_parser.add_argument("-o", "--output", help="输出文件路径")
    
    args = parser.parse_args()
    
    if args.command == "convert":
        input_path = Path(args.input)
        if input_path.suffix == ".script":
            output = convert_script_to_json(args.input, args.output)
            print(f"✅ 已转换为 JSON: {output}")
        elif input_path.suffix == ".json":
            output = convert_json_to_script(args.input, args.output)
            print(f"✅ 已转换为简易脚本: {output}")
        else:
            print(f"❌ 不支持的文件格式: {input_path.suffix}")
            print("   支持的格式: .script, .json")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
