"""
Signature-level conformance: iOSDriver implements all 42 DeviceDriver entries
with matching signatures (design §7 T1).

This deliberately does NOT use isinstance(): @runtime_checkable only checks
method-name presence, not signatures or @property return types — it would give
a false pass. We compare inspect.signature per entry instead.
"""

import inspect

from phone_pilot.core import protocols as P
from phone_pilot.ios.driver import iOSDriver


SERIAL = "00008030-001234560A80802E"


def _proto_methods(proto) -> list[str]:
    return [
        n
        for n, v in vars(proto).items()
        if not n.startswith("_") and inspect.isfunction(v)
    ]


def _sig_params(func, *, drop_self: bool):
    params = list(inspect.signature(func).parameters.values())
    if drop_self:
        params = params[1:]
    return [(p.name, p.default, p.kind) for p in params]


def test_subdriver_entries_match_signatures():
    drv = iOSDriver(SERIAL)
    subs = {
        P.InputDriver: drv.input,
        P.ScreenDriver: drv.screen,
        P.UIDriver: drv.ui,
        P.AppDriver: drv.app,
    }
    total = 0
    for proto, impl in subs.items():
        for m in _proto_methods(proto):
            total += 1
            assert callable(getattr(impl, m, None)), f"{proto.__name__}.{m} missing"
            want = _sig_params(getattr(proto, m), drop_self=True)
            got = _sig_params(getattr(impl, m), drop_self=False)
            assert want == got, f"{proto.__name__}.{m} signature drift: {want} != {got}"
    assert total == 19, f"expected 19 sub-driver methods, found {total}"


def test_device_level_entries_match_signatures():
    drv = iOSDriver(SERIAL)
    methods = _proto_methods(P.DeviceDriver)
    for m in methods:
        assert callable(getattr(drv, m, None)), f"DeviceDriver.{m} missing"
        want = _sig_params(getattr(P.DeviceDriver, m), drop_self=True)
        got = _sig_params(getattr(drv, m), drop_self=False)
        assert want == got, f"DeviceDriver.{m} signature drift: {want} != {got}"
    assert len(methods) == 17, f"expected 17 device-level methods, found {len(methods)}"


def test_accessor_and_identity_properties_present():
    drv = iOSDriver(SERIAL)
    props = [
        n
        for n, v in vars(P.DeviceDriver).items()
        if not n.startswith("_") and isinstance(v, property)
    ]
    assert len(props) == 6, f"expected 6 properties, found {len(props)}"
    for pr in props:
        assert hasattr(drv, pr), f"DeviceDriver.{pr} missing"
    # sub-driver accessors return the right concrete types
    from phone_pilot.ios.driver import (
        iOSInputDriver,
        iOSScreenDriver,
        iOSUIDriver,
        iOSAppDriver,
    )

    assert isinstance(drv.input, iOSInputDriver)
    assert isinstance(drv.screen, iOSScreenDriver)
    assert isinstance(drv.ui, iOSUIDriver)
    assert isinstance(drv.app, iOSAppDriver)


def test_total_is_42():
    """19 sub-driver + 17 device-level + 6 properties = 42 (design counting rule)."""
    sub = sum(
        len(_proto_methods(p))
        for p in (P.InputDriver, P.ScreenDriver, P.UIDriver, P.AppDriver)
    )
    dev = len(_proto_methods(P.DeviceDriver))
    props = len(
        [
            n
            for n, v in vars(P.DeviceDriver).items()
            if not n.startswith("_") and isinstance(v, property)
        ]
    )
    assert sub + dev + props == 42, f"{sub}+{dev}+{props}"
