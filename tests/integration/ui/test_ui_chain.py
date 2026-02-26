from phone_pilot.core.ui.chain import chain
from phone_pilot.core.ui.element import UIElement
from phone_pilot.script_api import ScriptContext


def test_chain_short_circuit(monkeypatch) -> None:
    ctx = ScriptContext(device_serial="dummy")

    def _find_text(*_args, **_kwargs):
        return None

    monkeypatch.setattr("phone_pilot.script_api.find_text", _find_text)
    res = chain(ctx).find_text("missing").tap().done()
    assert res["ok"] is False
    assert res["error"]["code"] == "no_match"


def test_chain_save_get_tap(monkeypatch) -> None:
    ctx = ScriptContext(device_serial="dummy")

    elem = UIElement(x=10, y=10, width=10, height=10)
    elem._ops = {"tap_xy": lambda x, y: {"ok": True}}

    monkeypatch.setattr("phone_pilot.script_api.find_text", lambda *_a, **_k: elem)
    res = chain(ctx).find_text("ok").save("btn").get("btn").tap().done()
    assert res["ok"] is True


def test_chain_repeat_count(monkeypatch) -> None:
    ctx = ScriptContext(device_serial="dummy")
    calls = {"count": 0}

    def _find_text(*_a, **_k):
        calls["count"] += 1
        return UIElement(x=1, y=2, width=3, height=4)

    monkeypatch.setattr("phone_pilot.script_api.find_text", _find_text)
    ch = chain(ctx).find_text("ok").repeat(3).done()
    assert ch["ok"] is True
    assert calls["count"] == 3


def test_chain_repeat_step(monkeypatch) -> None:
    from phone_pilot.core.ui.chain import find_text as step_find_text

    ctx = ScriptContext(device_serial="dummy")
    monkeypatch.setattr("phone_pilot.script_api.find_text", lambda *_a, **_k: UIElement(x=1, y=1, width=2, height=2))
    res = chain(ctx).repeat(step_find_text("ok"), max=2).done()
    assert res["ok"] is True
