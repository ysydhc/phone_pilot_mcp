"""验证引擎单元测试。"""

from unittest.mock import MagicMock, PropertyMock

from phone_pilot.core.verify import run_assertions, _text_matches, _text_contains


# ---------------------------------------------------------------------------
# 文本匹配工具
# ---------------------------------------------------------------------------


class TestTextMatching:
    def test_exact_match(self):
        assert _text_matches("hello", "hello")
        assert not _text_matches("hello", "world")

    def test_regex_match(self):
        assert _text_matches(r"re:^\d+K$", "123K")
        assert not _text_matches(r"re:^\d+K$", "abcK")

    def test_regex_search(self):
        assert _text_matches(r"re:init.*ok", "app_init_ok_done")

    def test_contains_exact(self):
        assert _text_contains("init", "app_init_ok")
        assert not _text_contains("missing", "app_init_ok")

    def test_contains_regex(self):
        assert _text_contains(r"re:\d+", "version 123")
        assert not _text_contains(r"re:^\d+$", "version 123")


# ---------------------------------------------------------------------------
# Mock driver 工厂
# ---------------------------------------------------------------------------


def _make_driver(*, texts=None, activity=None, log_content="", elements=None):
    """创建一个 mock DeviceDriver。"""
    driver = MagicMock()

    # UI driver
    ui = MagicMock()
    ui.collect_all_texts.return_value = texts or []
    ui.get_current_activity.return_value = activity or {"package": "", "activity": ""}
    ui.find_elements.return_value = elements or []
    type(driver).ui = PropertyMock(return_value=ui)

    # Screen driver
    screen = MagicMock()
    screen.screenshot.return_value = b"\x89PNG\r\n"  # minimal fake PNG
    type(driver).screen = PropertyMock(return_value=screen)

    # Log
    driver.read_log.return_value = log_content

    # Memory
    driver.dump_memory_profile.return_value = {"ok": True, "total_pss_mb": 100}

    return driver


# ---------------------------------------------------------------------------
# 断言测试
# ---------------------------------------------------------------------------


class TestElementExists:
    def test_found_by_text(self):
        driver = _make_driver(elements=[{"text": "关注", "center_x": 100, "center_y": 200}])
        result = run_assertions(driver, [{"type": "element_exists", "text": "关注"}])
        assert result["ok"] is True
        assert result["passed"] == 1

    def test_not_found(self):
        driver = _make_driver(elements=[])
        result = run_assertions(driver, [{"type": "element_exists", "text": "不存在"}])
        assert result["ok"] is False
        assert result["failed"] == 1

    def test_regex_text(self):
        driver = _make_driver(texts=["123K", "フォロー"])
        result = run_assertions(driver, [{"type": "element_exists", "text": r"re:^\d+K$"}])
        assert result["ok"] is True

    def test_element_not_exists(self):
        driver = _make_driver(elements=[])
        result = run_assertions(driver, [{"type": "element_not_exists", "text": "消失了"}])
        assert result["ok"] is True


class TestScreenContainsText:
    def test_found(self):
        driver = _make_driver(texts=["版本 2.0.1", "设置", "关于"])
        result = run_assertions(driver, [{"type": "screen_contains_text", "text": "版本"}])
        assert result["ok"] is True

    def test_regex_found(self):
        driver = _make_driver(texts=["版本 2.0.1", "设置"])
        result = run_assertions(driver, [
            {"type": "screen_contains_text", "text": r"re:版本\s+\d+\.\d+"}
        ])
        assert result["ok"] is True

    def test_not_found(self):
        driver = _make_driver(texts=["设置", "关于"])
        result = run_assertions(driver, [{"type": "screen_contains_text", "text": "不存在"}])
        assert result["ok"] is False


class TestActivityEquals:
    def test_match_activity(self):
        driver = _make_driver(activity={"package": "com.example", "activity": ".MainActivity"})
        result = run_assertions(driver, [
            {"type": "activity_equals", "activity": ".MainActivity"}
        ])
        assert result["ok"] is True

    def test_match_package(self):
        driver = _make_driver(activity={"package": "com.example", "activity": ".Main"})
        result = run_assertions(driver, [
            {"type": "activity_equals", "activity": "com.example"}
        ])
        assert result["ok"] is True

    def test_regex_match(self):
        driver = _make_driver(activity={"package": "com.example.app", "activity": ".Main"})
        result = run_assertions(driver, [
            {"type": "activity_equals", "activity": r"re:com\.example\..*"}
        ])
        assert result["ok"] is True


class TestLogcatAssertions:
    def test_logcat_contains(self):
        driver = _make_driver(log_content="INFO init_success\nWARN something\n")
        result = run_assertions(driver, [
            {"type": "logcat_contains", "pattern": "init_success"}
        ])
        assert result["ok"] is True
        assert result["results"][0]["detail"]["count"] == 1

    def test_logcat_contains_regex(self):
        driver = _make_driver(log_content="INFO init took 150ms\nERROR timeout\n")
        result = run_assertions(driver, [
            {"type": "logcat_contains", "pattern": r"re:init\s+took\s+\d+ms"}
        ])
        assert result["ok"] is True

    def test_logcat_not_contains(self):
        driver = _make_driver(log_content="INFO all good\n")
        result = run_assertions(driver, [
            {"type": "logcat_not_contains", "pattern": "ERROR"}
        ])
        assert result["ok"] is True

    def test_logcat_not_contains_fail(self):
        driver = _make_driver(log_content="ERROR NullPointer\n")
        result = run_assertions(driver, [
            {"type": "logcat_not_contains", "pattern": "ERROR"}
        ])
        assert result["ok"] is False


class TestElapsedUnder:
    def test_within_threshold(self):
        driver = _make_driver()
        result = run_assertions(driver, [
            {"type": "elapsed_under", "elapsed_ms": 500, "max_ms": 1000}
        ])
        assert result["ok"] is True

    def test_exceeds_threshold(self):
        driver = _make_driver()
        result = run_assertions(driver, [
            {"type": "elapsed_under", "elapsed_ms": 1500, "max_ms": 1000}
        ])
        assert result["ok"] is False


class TestBatchAssertions:
    def test_mixed_pass_fail(self):
        driver = _make_driver(
            texts=["关注", "设置"],
            log_content="INFO init_ok\n",
        )
        result = run_assertions(driver, [
            {"type": "screen_contains_text", "text": "关注"},
            {"type": "screen_contains_text", "text": "不存在的文字"},
            {"type": "logcat_contains", "pattern": "init_ok"},
        ])
        assert result["passed"] == 2
        assert result["failed"] == 1
        assert result["total"] == 3
        assert result["ok"] is False

    def test_unknown_type(self):
        driver = _make_driver()
        result = run_assertions(driver, [{"type": "nonexistent_type"}])
        assert result["ok"] is False
        assert "unknown" in str(result["results"][0]["detail"])

    def test_empty_assertions(self):
        driver = _make_driver()
        result = run_assertions(driver, [])
        assert result["ok"] is True
        assert result["total"] == 0
