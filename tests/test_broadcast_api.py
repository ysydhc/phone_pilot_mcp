"""Tests for send_broadcast / ensure_app_alive."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from phone_pilot.script_api.broadcast import ensure_app_alive, send_broadcast

MARK = "07-22 10:00:00.000"
HDR = "07-22 10:00:01.000  1234  1234 D AdTest: "


def _ctx(shell_ok=True, log_lines=None, mark=MARK, platform="android"):
    driver = MagicMock()
    driver.shell.return_value = {
        "ok": shell_ok,
        "stdout": "",
        "stderr": "",
        "returncode": 0 if shell_ok else 1,
    }
    driver.logcat_time.return_value = mark
    driver.read_log.return_value = "\n".join(HDR + ln for ln in (log_lines or []))
    return SimpleNamespace(driver=driver, platform=platform)


def _sent_cmd(ctx) -> str:
    return ctx.driver.shell.call_args[0][0]


def test_extras_all_es_and_quoted():
    # B1 + ADR-2: 值含空格/分号→quote；全部 --es
    ctx = _ctx()
    send_broadcast(
        ctx, "com.x.TEST", package="com.x", extras={"cmd": "env", "v": "a b;rm -rf /"}
    )
    cmd = _sent_cmd(ctx)
    assert "--es cmd env" in cmd
    assert "--es v 'a b;rm -rf /'" in cmd
    assert "--ei" not in cmd and "--ez" not in cmd


def test_apid_leading_zero_preserved():
    # B2
    ctx = _ctx()
    send_broadcast(ctx, "com.x.TEST", extras={"apid": "057362412"})
    assert "057362412" in _sent_cmd(ctx)


def test_old_ack_not_matched():
    # B3 / ADR-1: 旧 ACK 在 mark 之前（read_log(since=..) 返回空区间）→ ack_seen=False
    ctx = _ctx(log_lines=[])  # since 区间内无日志
    r = send_broadcast(ctx, "com.x.TEST", wait_ack="event=CMD_ACK", ack_timeout_s=0)
    assert r["ok"] is False and r["ack_seen"] is False
    # read_log 必须带 since 参数（区间读取，不是 lines 模式）
    assert ctx.driver.read_log.call_args.kwargs.get("since") == MARK


def test_ack_seen_in_window():
    ctx = _ctx(log_lines=["event=CMD_ACK cmd=env"])
    r = send_broadcast(ctx, "com.x.TEST", wait_ack="event=CMD_ACK", ack_timeout_s=0)
    assert r["ok"] is True and r["ack_seen"] is True and "CMD_ACK" in r["line"]


def test_ack_timeout_dead_vs_alive():
    # B4 / ADR-7: 超时后按 pidof 结果细分
    ctx = _ctx(log_lines=[])
    ctx.driver.shell.side_effect = [
        {"ok": True, "stdout": "", "stderr": "", "returncode": 0},  # am broadcast
        {"ok": True, "stdout": "12345", "stderr": "", "returncode": 0},  # pidof → alive
    ]
    r = send_broadcast(
        ctx, "com.x.TEST", package="com.x", wait_ack="event=CMD_ACK", ack_timeout_s=0
    )
    assert r["reason"] == "ack_timeout_app_alive"


def test_check_alive_precheck_blocks_send():
    # B5: 预检死 → 不发广播
    ctx = _ctx()
    ctx.driver.shell.side_effect = [
        {"ok": False, "stdout": "", "stderr": "", "returncode": 1},  # pidof 空
        {"ok": False, "stdout": "", "stderr": "", "returncode": 1},  # ps 降级也空
    ]
    r = send_broadcast(ctx, "com.x.TEST", package="com.x", check_alive=True)
    assert r["ok"] is False and r["reason"] == "app_not_alive"
    assert ctx.driver.shell.call_count == 2  # 只有存活探测，没有 am broadcast


def test_invalid_action_fail_fast():
    # B21 / ADR-12
    ctx = _ctx()
    r = send_broadcast(ctx, "com.x.TEST; reboot")
    assert r["ok"] is False and r["reason"] == "invalid_param"
    ctx.driver.shell.assert_not_called()


def test_invalid_flag_fail_fast():
    ctx = _ctx()
    r = send_broadcast(ctx, "com.x.TEST", flags=["--ez x true; am force-stop y"])
    assert r["ok"] is False and r["reason"] == "invalid_param"


def test_invalid_extras_key_fail_fast():
    # B22
    ctx = _ctx()
    r = send_broadcast(ctx, "com.x.TEST", extras={"bad key": "v"})
    assert r["ok"] is False and r["reason"] == "invalid_param"


def test_mark_failed_no_send_when_wait_ack():
    # B24 / ADR-13
    ctx = _ctx(mark=None)
    r = send_broadcast(ctx, "com.x.TEST", wait_ack="ACK")
    assert r["ok"] is False and r["reason"] == "mark_failed"


def test_not_supported_platform():
    # B20
    ctx = _ctx(platform="harmony")
    r = send_broadcast(ctx, "com.x.TEST")
    assert r["ok"] is False and r["error"] == "not_supported"


def test_redact_keys_masks_value():
    # B33: 返回 dict 中命中 key 掩码（落盘日志路径在实现内同串）
    ctx = _ctx()
    r = send_broadcast(
        ctx, "com.x.TEST", extras={"token": "sekret"}, redact_keys=["token"]
    )
    assert "sekret" not in str(r)


def test_alive_no_ready_event_returns_fast():
    # B8: 已存活 + 未给 ready_event → 不拉起
    ctx = _ctx()
    ctx.driver.shell.return_value = {
        "ok": True,
        "stdout": "12345",
        "stderr": "",
        "returncode": 0,
    }
    r = ensure_app_alive(ctx, "com.x")
    assert r["ok"] is True and r["pid"] == 12345 and r["launched"] is False
    ctx.driver.open_deeplink.assert_not_called()


def test_alive_with_ready_event_skips_wait():
    # B25 / R1: 已存活+给 ready_event+非 force_restart → 跳过等待, ready_seen=None
    ctx = _ctx()
    ctx.driver.shell.return_value = {
        "ok": True,
        "stdout": "12345",
        "stderr": "",
        "returncode": 0,
    }
    r = ensure_app_alive(
        ctx, "com.x", scheme_url="s://x", ready_event="event=APP_READY"
    )
    assert r["ok"] is True and r["ready_seen"] is None
    ctx.driver.read_log.assert_not_called()


def test_dead_no_scheme():
    # B26
    ctx = _ctx()
    ctx.driver.shell.return_value = {
        "ok": False,
        "stdout": "",
        "stderr": "",
        "returncode": 1,
    }
    r = ensure_app_alive(ctx, "com.x")
    assert r["ok"] is False and r["reason"] == "dead_no_launch_method"


def test_pidof_fallback_to_ps():
    # B6: pidof 失败 → ps 降级
    ctx = _ctx()
    ctx.driver.shell.side_effect = [
        {"ok": False, "stdout": "", "stderr": "pidof: not found", "returncode": 127},
        {"ok": True, "stdout": "root 999 1 0 S com.x", "stderr": "", "returncode": 0},
    ]
    r = ensure_app_alive(ctx, "com.x")
    assert r["ok"] is True and r["pid"] == 999


def test_pidof_multi_pid_takes_first():
    # B7
    ctx = _ctx()
    ctx.driver.shell.return_value = {
        "ok": True,
        "stdout": "111 222",
        "stderr": "",
        "returncode": 0,
    }
    r = ensure_app_alive(ctx, "com.x")
    assert r["pid"] == 111


def test_cold_launch_then_ready_seen():
    ctx = _ctx(log_lines=["event=APP_READY page=main"])
    ctx.driver.shell.side_effect = [
        {"ok": False, "stdout": "", "stderr": "", "returncode": 1},  # pidof: dead
        {"ok": False, "stdout": "", "stderr": "", "returncode": 1},  # ps: dead
        {"ok": True, "stdout": "12345", "stderr": "", "returncode": 0},  # 最终 pid 复查
    ]
    ctx.driver.open_deeplink.return_value = {"ok": True}
    r = ensure_app_alive(
        ctx,
        "com.x",
        scheme_url="admtest://open",
        ready_event="event=APP_READY",
        timeout_s=0,
    )
    assert r["ok"] is True and r["launched"] is True and r["ready_seen"] is True


def test_ready_timeout_dead_vs_alive():
    # B27 / ADR-7: ready 超时后存活复查细分
    ctx = _ctx(log_lines=[])
    ctx.driver.shell.side_effect = [
        {"ok": False, "stdout": "", "stderr": "", "returncode": 1},  # pidof: dead
        {"ok": False, "stdout": "", "stderr": "", "returncode": 1},  # ps: dead
        {"ok": False, "stdout": "", "stderr": "", "returncode": 1},  # 复查 pidof: dead
        {"ok": False, "stdout": "", "stderr": "", "returncode": 1},  # 复查 ps: dead
    ]
    ctx.driver.open_deeplink.return_value = {"ok": True}
    r = ensure_app_alive(
        ctx, "com.x", scheme_url="s://x", ready_event="event=APP_READY", timeout_s=0
    )
    assert r["ok"] is False and r["reason"] == "ready_timeout_app_dead"
