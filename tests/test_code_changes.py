"""代码变更捕获单元测试。"""

import tempfile
import pathlib

from phone_pilot.core.code_changes import (
    capture_code_changes,
    _parse_diff_stat,
    _generate_diff_summary,
)


class TestParseDiffStat:
    def test_typical_stat(self):
        stat = """\
 app/src/main/DActivity.java | 15 +++++++--------
 app/res/layout/d_page.xml   | 10 +++++-----
 2 files changed, 12 insertions(+), 13 deletions(-)"""
        result = _parse_diff_stat(stat)
        assert result["files_changed"] == [
            "app/src/main/DActivity.java",
            "app/res/layout/d_page.xml",
        ]
        assert result["insertions"] == 12
        assert result["deletions"] == 13

    def test_empty_stat(self):
        result = _parse_diff_stat("")
        assert result["files_changed"] == []
        assert result["insertions"] == 0
        assert result["deletions"] == 0

    def test_single_file(self):
        stat = " README.md | 3 +++\n 1 file changed, 3 insertions(+)"
        result = _parse_diff_stat(stat)
        assert result["files_changed"] == ["README.md"]
        assert result["insertions"] == 3
        assert result["deletions"] == 0


class TestGenerateDiffSummary:
    def test_no_changes(self):
        assert _generate_diff_summary("", "") == "无代码变更"

    def test_typical_summary(self):
        stat = " a.py | 5 ++---\n 1 file changed, 2 insertions(+), 3 deletions(-)"
        summary = _generate_diff_summary(stat, "diff content")
        assert "a.py" in summary
        assert "+2" in summary
        assert "-3" in summary


class TestCaptureCodeChanges:
    def test_in_git_repo(self):
        """在当前项目中运行（是 git 仓库）。"""
        result = capture_code_changes(repo_path=".")
        assert result["ok"] is True
        assert "last_commit" in result
        assert result["last_commit"].get("hash")

    def test_not_git_repo(self):
        """在非 git 目录运行。"""
        with tempfile.TemporaryDirectory() as td:
            result = capture_code_changes(repo_path=td)
            assert result["ok"] is False
            assert result["error"] == "not_a_git_repo"

    def test_with_description(self):
        result = capture_code_changes(repo_path=".", description="手动说明")
        assert result["description"] == "手动说明"

    def test_save_diff_file(self):
        with tempfile.TemporaryDirectory() as td:
            diff_path = str(pathlib.Path(td) / "test.diff")
            result = capture_code_changes(repo_path=".", save_diff_to=diff_path)
            # 如果有未提交变更，diff 文件应被创建
            # 如果没有变更，diff_path 为 None
            if result["uncommitted_changes"]["files_changed"]:
                assert pathlib.Path(diff_path).exists()
