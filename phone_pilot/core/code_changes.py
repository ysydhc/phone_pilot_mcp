"""代码变更捕获与摘要生成。

在验证/脚本执行时自动捕获 git 仓库的变更信息：
- ``git diff HEAD``（未提交的变更）
- ``git diff --staged``（已暂存未提交）
- ``git log -1``（最近一次 commit）
- 变更文件列表 + 统计
- 可选的手动描述

用法::

    from phone_pilot.core.code_changes import capture_code_changes
    info = capture_code_changes(repo_path="/path/to/app")
"""

from __future__ import annotations

import pathlib
import subprocess
from typing import Optional


def _run_git(args: list[str], cwd: str) -> str:
    """运行 git 命令并返回 stdout（失败返回空字符串）。"""
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=15,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except Exception:
        return ""


def _parse_diff_stat(diff_stat: str) -> dict:
    """解析 ``git diff --stat`` 输出为结构化信息。"""
    lines = diff_stat.strip().splitlines()
    files: list[str] = []
    insertions = 0
    deletions = 0

    for line in lines:
        line = line.strip()
        if not line:
            continue
        # 最后一行通常是汇总：" 3 files changed, 25 insertions(+), 8 deletions(-)"
        if "file" in line and "changed" in line:
            import re

            m_ins = re.search(r"(\d+)\s+insertion", line)
            m_del = re.search(r"(\d+)\s+deletion", line)
            if m_ins:
                insertions = int(m_ins.group(1))
            if m_del:
                deletions = int(m_del.group(1))
        elif "|" in line:
            # 文件行：" file.py | 5 ++---"
            parts = line.split("|")
            if parts:
                fname = parts[0].strip()
                if fname:
                    files.append(fname)

    return {"files_changed": files, "insertions": insertions, "deletions": deletions}


def _generate_diff_summary(diff_stat: str, diff_text: str) -> str:
    """从 diff 内容自动生成简短的变更摘要。"""
    stat = _parse_diff_stat(diff_stat)
    files = stat["files_changed"]

    if not files:
        return "无代码变更"

    n = len(files)
    ins = stat["insertions"]
    dele = stat["deletions"]

    # 简单摘要
    file_list = ", ".join(files[:5])
    if n > 5:
        file_list += f" 等 {n} 个文件"

    parts = []
    if ins > 0:
        parts.append(f"+{ins}")
    if dele > 0:
        parts.append(f"-{dele}")
    stat_str = "/".join(parts) if parts else ""

    return f"修改 {file_list}" + (f"（{stat_str} 行）" if stat_str else "")


def capture_code_changes(
    repo_path: str = ".",
    description: str = "",
    save_diff_to: Optional[str] = None,
) -> dict:
    """捕获代码变更信息。

    Parameters
    ----------
    repo_path : str
        Git 仓库路径。默认当前工作目录。
    description : str
        Agent 手动附加的变更说明（可选）。为空时从 diff 自动生成。
    save_diff_to : str | None
        将完整 diff 保存到指定路径。为 ``None`` 时不保存文件。

    Returns
    -------
    dict
        ``{last_commit, uncommitted_changes, description}``
    """
    cwd = str(pathlib.Path(repo_path).expanduser().resolve())

    # 检查是否是 git 仓库
    is_git = _run_git(["rev-parse", "--is-inside-work-tree"], cwd)
    if is_git != "true":
        return {
            "ok": False,
            "error": "not_a_git_repo",
            "repo_path": cwd,
        }

    # 最近一次 commit
    log_format = "%H%n%s%n%an%n%aI"
    log_out = _run_git(["log", "-1", f"--format={log_format}"], cwd)
    log_lines = log_out.splitlines()
    last_commit = {}
    if len(log_lines) >= 4:
        last_commit = {
            "hash": log_lines[0],
            "message": log_lines[1],
            "author": log_lines[2],
            "time": log_lines[3],
        }

    # 未提交变更
    diff_text = _run_git(["diff", "HEAD"], cwd)
    diff_staged = _run_git(["diff", "--staged"], cwd)
    diff_stat = _run_git(["diff", "--stat", "HEAD"], cwd)

    # 文件列表 + 统计
    stat_info = _parse_diff_stat(diff_stat)

    # 自动摘要
    if not description:
        description = _generate_diff_summary(diff_stat, diff_text)

    # 保存完整 diff 文件
    diff_path = None
    if save_diff_to and diff_text:
        p = pathlib.Path(save_diff_to)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(diff_text, encoding="utf-8")
        diff_path = str(p)

    return {
        "ok": True,
        "last_commit": last_commit,
        "uncommitted_changes": {
            "files_changed": stat_info["files_changed"],
            "insertions": stat_info["insertions"],
            "deletions": stat_info["deletions"],
            "has_staged": bool(diff_staged),
            "diff_summary": description,
            "diff_path": diff_path,
        },
        "description": description,
    }
