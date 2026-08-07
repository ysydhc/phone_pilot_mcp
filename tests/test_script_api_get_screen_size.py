"""script_api.get_screen_size(ctx) -> dict 公开入口。"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from phone_pilot.script_api import get_screen_size
from phone_pilot.script_api.context import ScriptContext


def test_returns_dict_matching_driver_tuple():
    ctx = MagicMock(spec=ScriptContext)
    ctx.screen_size = None
    ctx.driver.screen.get_screen_size.return_value = (1080, 2400)
    result = get_screen_size(ctx)
    assert result == {"width": 1080, "height": 2400}


def test_propagates_exception_when_device_offline():
    """设备离线/driver 抛异常时，应原样冒泡，不吞异常返回假成功。"""
    ctx = MagicMock(spec=ScriptContext)
    ctx.screen_size = None
    ctx.driver.screen.get_screen_size.side_effect = RuntimeError("Failed to get screen size")
    with pytest.raises(RuntimeError):
        get_screen_size(ctx)
