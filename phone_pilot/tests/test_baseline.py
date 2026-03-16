"""B 类验证基准模块测试 / Class B verification baseline module tests.

验证 verification/baseline.py 中的常量和辅助函数行为。
Verify constants and helper functions in verification/baseline.py.
"""

from __future__ import annotations


class TestBaselineConstants:
    """基准常量测试 / Baseline constants tests."""

    def test_android_settings_package(self):
        """ANDROID_SETTINGS_PACKAGE 为 com.android.settings."""
        from phone_pilot.verification.baseline import ANDROID_SETTINGS_PACKAGE

        assert ANDROID_SETTINGS_PACKAGE == "com.android.settings"

    def test_android_settings_home_texts_is_list(self):
        """ANDROID_SETTINGS_HOME_TEXTS 是非空列表。
        ANDROID_SETTINGS_HOME_TEXTS is a non-empty list."""
        from phone_pilot.verification.baseline import ANDROID_SETTINGS_HOME_TEXTS

        assert isinstance(ANDROID_SETTINGS_HOME_TEXTS, list)
        assert len(ANDROID_SETTINGS_HOME_TEXTS) > 0

    def test_settings_subpage_texts_is_list(self):
        """SETTINGS_SUBPAGE_TEXTS 是非空列表。
        SETTINGS_SUBPAGE_TEXTS is a non-empty list."""
        from phone_pilot.verification.baseline import SETTINGS_SUBPAGE_TEXTS

        assert isinstance(SETTINGS_SUBPAGE_TEXTS, list)
        assert len(SETTINGS_SUBPAGE_TEXTS) > 0

    def test_harmony_settings_package_is_none(self):
        """HARMONY_SETTINGS_PACKAGE 当前为 None（预留）。
        HARMONY_SETTINGS_PACKAGE is None (reserved for future)."""
        from phone_pilot.verification.baseline import HARMONY_SETTINGS_PACKAGE

        assert HARMONY_SETTINGS_PACKAGE is None


class TestCurrentActivityIsSettings:
    """current_activity_is_settings 函数测试 / current_activity_is_settings tests."""

    def test_matching_package_returns_true(self):
        """传入匹配的 package 返回 True。Matching package returns True."""
        from phone_pilot.verification.baseline import current_activity_is_settings

        activity = {"package": "com.android.settings", "activity": ".Settings"}
        assert current_activity_is_settings(activity) is True

    def test_different_package_returns_false(self):
        """传入不同 package 返回 False。Different package returns False."""
        from phone_pilot.verification.baseline import current_activity_is_settings

        activity = {"package": "com.example.app", "activity": ".MainActivity"}
        assert current_activity_is_settings(activity) is False

    def test_none_returns_false(self):
        """传入 None 返回 False。None returns False."""
        from phone_pilot.verification.baseline import current_activity_is_settings

        assert current_activity_is_settings(None) is False

    def test_empty_dict_returns_false(self):
        """传入空 dict 返回 False。Empty dict returns False."""
        from phone_pilot.verification.baseline import current_activity_is_settings

        assert current_activity_is_settings({}) is False

    def test_missing_package_key_returns_false(self):
        """传入缺少 package 键的 dict 返回 False。Dict without package key returns False."""
        from phone_pilot.verification.baseline import current_activity_is_settings

        assert current_activity_is_settings({"activity": ".Settings"}) is False

    def test_non_dict_returns_false(self):
        """传入非 dict 类型返回 False。Non-dict type returns False."""
        from phone_pilot.verification.baseline import current_activity_is_settings

        assert current_activity_is_settings("com.android.settings") is False
        assert current_activity_is_settings(42) is False
        assert current_activity_is_settings([]) is False

    def test_settings_subactivity(self):
        """Settings 子页面也应返回 True。Settings subpage should also return True."""
        from phone_pilot.verification.baseline import current_activity_is_settings

        activity = {
            "package": "com.android.settings",
            "activity": ".wifi.WifiSettings",
        }
        assert current_activity_is_settings(activity) is True
