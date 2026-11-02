"""Mobile commercialization ad-format domain actions and monotonicity checker.

Provides full ad-format lifecycle verification (Splash, Banner, Feed/Native,
Interstitial, RewardVideo), log stage monotonicity verification, and UTS field
integrity checking.
"""

from __future__ import annotations

from enum import Enum
import json
import re
from typing import Any, Callable, Sequence

from phone_pilot.core.log import _log


class AdFormat(str, Enum):
    """Supported mobile commercialization ad formats."""

    SPLASH = "splash"
    BANNER = "banner"
    FEED = "feed"
    INTERSTITIAL = "interstitial"
    REWARD_VIDEO = "reward_video"

    @classmethod
    def from_str(cls, value: str | "AdFormat") -> "AdFormat":
        if isinstance(value, cls):
            return value
        v = str(value).strip().lower()
        aliases = {
            "splash": cls.SPLASH,
            "banner": cls.BANNER,
            "feed": cls.FEED,
            "native": cls.FEED,
            "native_feed": cls.FEED,
            "interstitial": cls.INTERSTITIAL,
            "int": cls.INTERSTITIAL,
            "reward_video": cls.REWARD_VIDEO,
            "rewarded_video": cls.REWARD_VIDEO,
            "reward": cls.REWARD_VIDEO,
            "rewarded": cls.REWARD_VIDEO,
        }
        if v in aliases:
            return aliases[v]
        raise ValueError(f"Unsupported ad_format: {value!r}")


_STAGE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "INIT",
        re.compile(
            r"(?:\b(?:AD_INIT|INIT_SUCCESS|SDK_INIT)\b|(?:event|stage)\s*[=:]\s*[\"']?INIT\b)",
            re.IGNORECASE,
        ),
    ),
    (
        "LOAD",
        re.compile(
            r"(?:\b(?:AD_LOAD|LOAD_START|LOAD_AD|REQUEST_START|loadAd)\b|(?:event|stage)\s*[=:]\s*[\"']?LOAD\b)",
            re.IGNORECASE,
        ),
    ),
    (
        "READY",
        re.compile(
            r"(?:\b(?:AD_READY|LOAD_SUCCESS|ON_AD_LOADED|onAdLoaded|lifecycle_stage\s+READY)\b|(?:event|stage)\s*[=:]\s*[\"']?READY\b)",
            re.IGNORECASE,
        ),
    ),
    (
        "RENDER",
        re.compile(
            r"(?:\b(?:AD_RENDER|RENDER_SUCCESS|ON_RENDER|SHOW_OK|onAdRenderSuccess|lifecycle_stage\s+SHOW_ATTEMPT|lifecycle_stage\s+RENDER)\b|(?:event|stage)\s*[=:]\s*[\"']?RENDER\b)",
            re.IGNORECASE,
        ),
    ),
    (
        "IMPRESSION",
        re.compile(
            r"(?:\b(?:AD_IMPRESSION|trackImpression|onAdImpression|AD_SHOW|ON_AD_SHOW|onAdShow|lifecycle_stage\s+IMPRESSION)\b|(?:event|stage)\s*[=:]\s*[\"']?IMPRESSION\b)",
            re.IGNORECASE,
        ),
    ),
    (
        "REWARD",
        re.compile(
            r"(?:\b(?:AD_REWARD|REWARD_VERIFIED|REWARD_VERIFY|onReward|onUserEarnedReward|onRewardVerify)\b|(?:event|stage)\s*[=:]\s*[\"']?REWARD\b)",
            re.IGNORECASE,
        ),
    ),
    (
        "CLICK",
        re.compile(
            r"(?:\b(?:AD_CLICK|ON_AD_CLICK|CLICK_OK|trackClick|onAdClick|lifecycle_stage\s+CLICK)\b|(?:event|stage)\s*[=:]\s*[\"']?CLICK\b)",
            re.IGNORECASE,
        ),
    ),
    (
        "CLOSE",
        re.compile(
            r"(?:\b(?:AD_CLOSE|AD_DISMISS|ON_AD_CLOSE|onAdClose|onAdDismiss|lifecycle_stage\s+CLOSE)\b|(?:event|stage)\s*[=:]\s*[\"']?CLOSE\b)",
            re.IGNORECASE,
        ),
    ),
)

_BARE_STAGE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("INIT", re.compile(r"\bINIT\b")),
    ("LOAD", re.compile(r"\bLOAD\b")),
    ("READY", re.compile(r"\bREADY\b")),
    ("RENDER", re.compile(r"\bRENDER\b")),
    ("IMPRESSION", re.compile(r"\bIMPRESSION\b")),
    ("REWARD", re.compile(r"\bREWARD\b")),
    ("CLICK", re.compile(r"\bCLICK\b")),
    ("CLOSE", re.compile(r"\bCLOSE\b")),
)

_SYSTEM_NOISE_TAGS = (
    "AudioPolicyManager",
    "MiuiDecoration",
    "LocalImageResolver",
    "MultiTasking",
    "WindowManager",
    "ActivityTaskManager",
    "InputDispatcher",
    "SurfaceFlinger",
    "DisplayManager",
    "SoScStageCoordinator",
    "MulWinSwitchTransition",
    "TransitionRequestInfo",
)


def _extract_json_payload(line: str) -> dict[str, Any] | None:
    """Extract the first valid JSON object embedded in a log line, if any."""
    start = line.find("{")
    end = line.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    candidate = line[start : end + 1]
    try:
        parsed = json.loads(candidate)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        return None
    return None


def _detect_stage(line: str, payload: dict[str, Any] | None) -> str | None:
    """Detect canonical stage name from JSON payload or raw log text."""
    if payload:
        raw_stage = str(payload.get("stage") or payload.get("event") or "").strip()
        if raw_stage:
            upper_stage = raw_stage.upper()
            if upper_stage in MonotonicityChecker.STAGE_ORDER:
                return upper_stage
            for canonical, pattern in _STAGE_PATTERNS:
                if pattern.search(raw_stage):
                    return canonical
            for canonical, pattern in _BARE_STAGE_PATTERNS:
                if pattern.search(upper_stage):
                    return canonical

    if any(noise in line for noise in _SYSTEM_NOISE_TAGS):
        return None

    for canonical, pattern in _STAGE_PATTERNS:
        if pattern.search(line):
            return canonical

    # Allow uppercase bare stage tokens (e.g. "[AdSDK] LOAD placement_id=101")
    for canonical, pattern in _BARE_STAGE_PATTERNS:
        if pattern.search(line):
            return canonical
    return None


class MonotonicityChecker:
    """Verifies ad lifecycle stage ordering and UTS telemetry field completeness."""

    STAGE_ORDER: tuple[str, ...] = (
        "INIT",
        "LOAD",
        "READY",
        "RENDER",
        "IMPRESSION",
        "REWARD",
        "CLICK",
        "CLOSE",
    )
    DEFAULT_UTS_FIELDS: tuple[str, ...] = (
        "event",
        "ad_format",
        "placement_id",
        "request_id",
    )

    def check_logs(
        self,
        log_lines: str | Sequence[str],
        *,
        required_stages: Sequence[str] | None = None,
        required_uts_fields: Sequence[str] | None = None,
        placement_id: str = "",
    ) -> dict[str, Any]:
        if isinstance(log_lines, str):
            lines = [ln for ln in log_lines.splitlines() if ln.strip()]
        else:
            lines = [str(ln) for ln in log_lines if str(ln).strip()]

        stage_rank_map = {stage: idx for idx, stage in enumerate(self.STAGE_ORDER)}
        max_rank_seen = -1
        max_stage_seen = ""
        out_of_order = 0
        out_of_order_details: list[dict[str, Any]] = []
        stages_found: list[str] = []
        missing_fields_set: list[str] = []
        events: list[dict[str, Any]] = []

        uts_fields = list(required_uts_fields) if required_uts_fields is not None else []
        target_placement = placement_id.strip()

        for line_idx, line in enumerate(lines):
            payload = _extract_json_payload(line)
            if target_placement:
                if payload and "placement_id" in payload:
                    if str(payload.get("placement_id") or "").strip() != target_placement:
                        continue
                elif target_placement not in line:
                    continue

            stage = _detect_stage(line, payload)
            if stage is None:
                continue

            rank = stage_rank_map.get(stage, -1)
            # Deduplicate repeated consecutive emissions of the exact same stage or earlier lifecycle telemetry
            # if required_stages is specified and this stage is not in required_stages and already passed,
            # or handle monotonic progression across distinct lifecycle stages.
            if rank < max_rank_seen:
                # If the stage was already recorded in stages_found (e.g. async duplicate telemetry callback
                # for READY/IMPRESSION emitted after SHOW/RENDER), do not penalize as a stage inversion
                # unless it's a genuinely new stage appearing out of order!
                if stage in stages_found:
                    continue
                out_of_order += 1
                out_of_order_details.append(
                    {
                        "line_index": line_idx,
                        "stage": stage,
                        "after_stage": max_stage_seen,
                        "line": line,
                    }
                )
            else:
                max_rank_seen = rank
                max_stage_seen = stage

            if stage not in stages_found:
                stages_found.append(stage)

            if uts_fields:
                if payload is None:
                    for field in uts_fields:
                        if field not in missing_fields_set:
                            missing_fields_set.append(field)
                else:
                    for field in uts_fields:
                        val = payload.get(field)
                        if val is None or (isinstance(val, str) and not val.strip()):
                            if field not in missing_fields_set:
                                missing_fields_set.append(field)

            events.append(
                {
                    "line_index": line_idx,
                    "stage": stage,
                    "rank": rank,
                    "payload": payload,
                    "raw": line,
                }
            )

        req_stages = [s.upper() for s in (required_stages or ())]
        missing_stages = [s for s in req_stages if s not in stages_found]

        ok = (
            len(missing_stages) == 0
            and out_of_order == 0
            and len(missing_fields_set) == 0
            and len(events) > 0
        )

        return {
            "ok": ok,
            "matched_events": len(events),
            "stages_found": stages_found,
            "missing_stages": missing_stages,
            "out_of_order": out_of_order,
            "out_of_order_details": out_of_order_details,
            "missing_fields": missing_fields_set,
            "events": events,
        }


class AdDomainActions:
    """Domain action suite for verifying all 5 mobile ad formats."""

    SPLASH_STAGES: tuple[str, ...] = ("INIT", "LOAD", "READY", "RENDER", "IMPRESSION", "CLOSE")
    BANNER_STAGES: tuple[str, ...] = ("INIT", "LOAD", "READY", "RENDER", "IMPRESSION")
    FEED_STAGES: tuple[str, ...] = ("INIT", "LOAD", "READY", "RENDER", "IMPRESSION")
    INTERSTITIAL_STAGES: tuple[str, ...] = (
        "INIT",
        "LOAD",
        "READY",
        "RENDER",
        "IMPRESSION",
        "CLOSE",
    )
    REWARD_VIDEO_STAGES: tuple[str, ...] = (
        "INIT",
        "LOAD",
        "READY",
        "RENDER",
        "IMPRESSION",
        "REWARD",
        "CLOSE",
    )

    def __init__(
        self,
        *,
        checker: MonotonicityChecker | None = None,
        log_fetcher: Callable[[], str | Sequence[str]] | None = None,
        driver: Any | None = None,
    ) -> None:
        self.checker = checker or MonotonicityChecker()
        self.log_fetcher = log_fetcher
        self.driver = driver

    def _resolve_logs(
        self,
        log_lines: str | Sequence[str] | None,
        *,
        driver: Any | None = None,
    ) -> str | Sequence[str]:
        if log_lines is not None and log_lines != "":
            return log_lines
        target_driver = driver if driver is not None else self.driver
        if target_driver is not None:
            # Unwrap ScriptContext if passed directly
            drv = getattr(target_driver, "driver", target_driver)
            if hasattr(drv, "read_session_log"):
                return drv.read_session_log(include_current=True, current_lines=2500)
            if hasattr(drv, "read_log"):
                try:
                    return drv.read_log(lines=2500)
                except TypeError:
                    return drv.read_log()
        if self.log_fetcher is not None:
            return self.log_fetcher()
        return ()

    def _format_result(
        self,
        fmt: AdFormat,
        placement_id: str,
        check_res: dict[str, Any],
    ) -> dict[str, Any]:
        ok = bool(check_res["ok"])
        reasons: list[str] = []
        if not ok:
            if check_res["matched_events"] == 0:
                reasons.append("no_matching_events")
            if check_res["missing_stages"]:
                reasons.append(f"missing_stages={','.join(check_res['missing_stages'])}")
            if check_res["out_of_order"] > 0:
                reasons.append(f"out_of_order={check_res['out_of_order']}")
            if check_res["missing_fields"]:
                reasons.append(f"missing_fields={','.join(check_res['missing_fields'])}")
        reason = "verified" if ok else "; ".join(reasons) or "verification_failed"
        if not ok:
            _log(f"[AdDomainActions] {fmt.value} verification failed: {reason}")
        return {
            "ok": ok,
            "ad_format": fmt.value,
            "placement_id": placement_id,
            "matched_events": int(check_res["matched_events"]),
            "stages_found": list(check_res["stages_found"]),
            "missing_stages": list(check_res["missing_stages"]),
            "out_of_order": int(check_res["out_of_order"]),
            "missing_fields": list(check_res["missing_fields"]),
            "reward_verified": "REWARD" in check_res["stages_found"],
            "reason": reason,
            "details": check_res,
        }

    def verify_splash_flow(
        self,
        log_lines: str | Sequence[str] | None = None,
        *,
        placement_id: str = "",
        require_close: bool = True,
        required_stages: Sequence[str] | None = None,
        required_uts_fields: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        stages = (
            required_stages
            if required_stages is not None
            else (self.SPLASH_STAGES if require_close else self.SPLASH_STAGES[:-1])
        )
        check_res = self.checker.check_logs(
            self._resolve_logs(log_lines),
            required_stages=stages,
            required_uts_fields=required_uts_fields,
            placement_id=placement_id,
        )
        return self._format_result(AdFormat.SPLASH, placement_id, check_res)

    def verify_banner_flow(
        self,
        log_lines: str | Sequence[str] | None = None,
        *,
        placement_id: str = "",
        required_stages: Sequence[str] | None = None,
        required_uts_fields: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        stages = required_stages if required_stages is not None else self.BANNER_STAGES
        check_res = self.checker.check_logs(
            self._resolve_logs(log_lines),
            required_stages=stages,
            required_uts_fields=required_uts_fields,
            placement_id=placement_id,
        )
        return self._format_result(AdFormat.BANNER, placement_id, check_res)

    def verify_feed_flow(
        self,
        log_lines: str | Sequence[str] | None = None,
        *,
        placement_id: str = "",
        require_click: bool = False,
        required_stages: Sequence[str] | None = None,
        required_uts_fields: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        if required_stages is not None:
            stages: Sequence[str] = required_stages
        elif require_click:
            stages = (*self.FEED_STAGES, "CLICK")
        else:
            stages = self.FEED_STAGES
        check_res = self.checker.check_logs(
            self._resolve_logs(log_lines),
            required_stages=stages,
            required_uts_fields=required_uts_fields,
            placement_id=placement_id,
        )
        return self._format_result(AdFormat.FEED, placement_id, check_res)

    def verify_interstitial_flow(
        self,
        log_lines: str | Sequence[str] | None = None,
        *,
        placement_id: str = "",
        require_close: bool = True,
        required_stages: Sequence[str] | None = None,
        required_uts_fields: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        stages = (
            required_stages
            if required_stages is not None
            else (self.INTERSTITIAL_STAGES if require_close else self.INTERSTITIAL_STAGES[:-1])
        )
        check_res = self.checker.check_logs(
            self._resolve_logs(log_lines),
            required_stages=stages,
            required_uts_fields=required_uts_fields,
            placement_id=placement_id,
        )
        return self._format_result(AdFormat.INTERSTITIAL, placement_id, check_res)

    def verify_reward_video_flow(
        self,
        log_lines: str | Sequence[str] | None = None,
        *,
        placement_id: str = "",
        require_close: bool = True,
        required_stages: Sequence[str] | None = None,
        required_uts_fields: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        stages = (
            required_stages
            if required_stages is not None
            else (self.REWARD_VIDEO_STAGES if require_close else self.REWARD_VIDEO_STAGES[:-1])
        )
        check_res = self.checker.check_logs(
            self._resolve_logs(log_lines),
            required_stages=stages,
            required_uts_fields=required_uts_fields,
            placement_id=placement_id,
        )
        return self._format_result(AdFormat.REWARD_VIDEO, placement_id, check_res)

    def verify_ad_format(
        self,
        ad_format: str | AdFormat,
        log_lines: str | Sequence[str] | None = None,
        *,
        placement_id: str = "",
        required_uts_fields: Sequence[str] | None = None,
        driver: Any | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        fmt = AdFormat.from_str(ad_format)
        resolved = self._resolve_logs(log_lines, driver=driver)
        if fmt is AdFormat.SPLASH:
            return self.verify_splash_flow(
                resolved,
                placement_id=placement_id,
                required_uts_fields=required_uts_fields,
                **kwargs,
            )
        if fmt is AdFormat.BANNER:
            return self.verify_banner_flow(
                resolved,
                placement_id=placement_id,
                required_uts_fields=required_uts_fields,
                **kwargs,
            )
        if fmt is AdFormat.FEED:
            return self.verify_feed_flow(
                resolved,
                placement_id=placement_id,
                required_uts_fields=required_uts_fields,
                **kwargs,
            )
        if fmt is AdFormat.INTERSTITIAL:
            return self.verify_interstitial_flow(
                resolved,
                placement_id=placement_id,
                required_uts_fields=required_uts_fields,
                **kwargs,
            )
        return self.verify_reward_video_flow(
            resolved,
            placement_id=placement_id,
            required_uts_fields=required_uts_fields,
            **kwargs,
        )


def verify_ad_format(
    ad_format: str | AdFormat,
    log_lines: str | Sequence[str] | None = None,
    *,
    log_text: str | Sequence[str] | None = None,
    driver: Any | None = None,
    placement_id: str = "",
    required_uts_fields: Sequence[str] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Module-level convenience helper to verify any of the 5 ad formats."""
    effective_logs = log_lines if log_lines is not None else log_text
    return AdDomainActions(driver=driver).verify_ad_format(
        ad_format,
        effective_logs,
        placement_id=placement_id,
        required_uts_fields=required_uts_fields,
        driver=driver,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Countdown Text Parsing & Smart Ad Close Disambiguation
# ---------------------------------------------------------------------------

# Delayed countdown phrases: "15秒后可关闭", "5s后跳过", "5秒后可领取奖励", "Skip in 5s", "Close in 10 seconds"
_RE_DELAYED_COUNTDOWN = re.compile(
    r"(?:(\d{1,3})\s*(?:秒|s|sec|seconds?)\s*(?:后|之后)\s*(?:可)?(?:关闭|跳过|领取|得奖励|关闭广告|跳过广告)?|"
    r"(?:skip|close|reward|dismiss)\s+(?:in|after)\s+(\d{1,3})\s*(?:s|sec|seconds?)?)",
    re.IGNORECASE,
)

# Mixed countdown + immediate skip: "5s | 跳过", "5秒 | 跳过", "跳过 5s", "跳过 | 5", "Skip | 5s"
_RE_COUNTDOWN_WITH_SKIP = re.compile(
    r"(?:(\d{1,3})\s*(?:秒|s|sec)?\s*[|｜/·•\s]\s*(?:跳过广告|跳过|关闭广告|关闭|skip(?:\s+ad)?|close)|"
    r"(?:跳过广告|跳过|关闭广告|关闭|skip(?:\s+ad)?|close)\s*[|｜/·•\s]?\s*(\d{1,3})\s*(?:秒|s|sec)?)",
    re.IGNORECASE,
)

# Pure countdown: "15s", "15 s", "15秒", "倒计时 15s", "剩余5秒", "00:15"
_RE_PURE_COUNTDOWN = re.compile(
    r"^(?:(?:倒计时|剩余|还剩)\s*)?(\d{1,3})\s*(?:秒|s|sec|seconds?)$|"
    r"^00:(\d{2})$",
    re.IGNORECASE,
)

# Bare integer in a close/countdown widget: "1" .. "120"
_RE_BARE_INT_COUNTDOWN = re.compile(r"^(\d{1,3})$")

# Reward earned / countdown completed phrases
_RE_REWARD_EARNED = re.compile(
    r"(?:已获得奖励|恭喜获得奖励|奖励已发放|已领取奖励|可关闭广告|现在可关闭|reward\s+granted|reward\s+earned)",
    re.IGNORECASE,
)

# Actionable close / skip button texts
_RE_IMMEDIATE_CLOSE = re.compile(
    r"^(?:跳过广告|跳过|关闭广告|关闭|×|✕|✖|╳|X|x|close(?:\s+ad)?|skip(?:\s+ad)?|dismiss|close_button|ad_close)$",
    re.IGNORECASE,
)

_CLOSE_WIDGET_HINTS = (
    "adclosebuttonview",
    "close",
    "skip",
    "count_down",
    "countdown",
    "tt_video_ad_close",
    "tt_reward_full_count_down",
    "ksad_auto_close",
    "gdt_ic_close",
    "btn_close",
    "iv_close",
)

_COUNTDOWN_PROGRESS_WIDGET_HINTS = (
    "countdownprogressbar",
    "countdown_progress",
    "countdownview",
)

_MUTE_WIDGET_HINTS = (
    "mute",
    "sound",
    "volume",
    "audio",
    "voice",
    "speaker",
    "静音",
    "声音",
    "音量",
)


def parse_countdown_text(
    text: str,
    *,
    in_close_widget: bool = False,
    allow_early_skip: bool = True,
) -> dict[str, Any]:
    """Parse an ad UI node's text/content_desc for countdown timer and close/skip status.

    Returns:
        dict with keys:
        - raw: original string
        - has_countdown: bool
        - seconds: int | None
        - is_close_text: bool
        - ready_to_close: bool
        - kind: "delayed_countdown" | "countdown_with_skip" | "pure_countdown" |
                "reward_earned" | "immediate_close" | "none"
    """
    raw = str(text or "").strip()
    if not raw:
        return {
            "raw": "",
            "has_countdown": False,
            "seconds": None,
            "is_close_text": False,
            "ready_to_close": False,
            "kind": "none",
        }

    # 1. Delayed countdown ("15秒后可关闭", "5s后跳过", "Skip in 5s")
    m_delayed = _RE_DELAYED_COUNTDOWN.search(raw)
    if m_delayed:
        sec_str = m_delayed.group(1) or m_delayed.group(2)
        secs = int(sec_str)
        return {
            "raw": raw,
            "has_countdown": True,
            "seconds": secs,
            "is_close_text": True,
            "ready_to_close": secs <= 0,
            "kind": "delayed_countdown",
        }

    # 2. Mixed countdown + immediate skip ("5s | 跳过", "跳过 5s")
    m_mix = _RE_COUNTDOWN_WITH_SKIP.fullmatch(raw)
    if m_mix:
        sec_str = m_mix.group(1) or m_mix.group(2)
        secs = int(sec_str)
        ready = True if allow_early_skip else (secs <= 0)
        return {
            "raw": raw,
            "has_countdown": True,
            "seconds": secs,
            "is_close_text": True,
            "ready_to_close": ready,
            "kind": "countdown_with_skip",
        }

    # 3. Pure countdown ("15s", "5秒", "倒计时 5s", "00:15")
    m_pure = _RE_PURE_COUNTDOWN.fullmatch(raw)
    if m_pure:
        sec_str = m_pure.group(1) or m_pure.group(2)
        secs = int(sec_str)
        return {
            "raw": raw,
            "has_countdown": True,
            "seconds": secs,
            "is_close_text": False,
            "ready_to_close": secs <= 0,
            "kind": "pure_countdown",
        }

    # 4. Reward earned ("已获得奖励", "恭喜获得奖励")
    if _RE_REWARD_EARNED.search(raw):
        return {
            "raw": raw,
            "has_countdown": False,
            "seconds": 0,
            "is_close_text": True,
            "ready_to_close": True,
            "kind": "reward_earned",
        }

    # 5. Immediate close / skip ("跳过", "跳过广告", "关闭", "×", "Skip")
    if _RE_IMMEDIATE_CLOSE.fullmatch(raw):
        return {
            "raw": raw,
            "has_countdown": False,
            "seconds": 0,
            "is_close_text": True,
            "ready_to_close": True,
            "kind": "immediate_close",
        }

    # 6. Bare integer ("15") inside a known close/countdown top-bar widget
    if in_close_widget:
        m_int = _RE_BARE_INT_COUNTDOWN.fullmatch(raw)
        if m_int:
            secs = int(m_int.group(1))
            if 0 <= secs <= 180:
                return {
                    "raw": raw,
                    "has_countdown": True,
                    "seconds": secs,
                    "is_close_text": False,
                    "ready_to_close": secs <= 0,
                    "kind": "pure_countdown",
                }

    return {
        "raw": raw,
        "has_countdown": False,
        "seconds": None,
        "is_close_text": False,
        "ready_to_close": False,
        "kind": "none",
    }


def _extract_node_geometry(node: dict[str, Any]) -> tuple[int, int, int, int, int, int] | None:
    """Return (cx, cy, x1, y1, x2, y2) from a node dict if available."""
    bounds_rect = node.get("bounds_rect")
    if isinstance(bounds_rect, (list, tuple)) and len(bounds_rect) == 4:
        x1, y1, x2, y2 = (int(bounds_rect[0]), int(bounds_rect[1]), int(bounds_rect[2]), int(bounds_rect[3]))
        if x2 > x1 and y2 > y1:
            return ((x1 + x2) // 2, (y1 + y2) // 2, x1, y1, x2, y2)

    bounds = str(node.get("bounds") or "")
    m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds)
    if m:
        x1, y1, x2, y2 = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
        if x2 > x1 and y2 > y1:
            return ((x1 + x2) // 2, (y1 + y2) // 2, x1, y1, x2, y2)

    center = node.get("center")
    if isinstance(center, (list, tuple)) and len(center) == 2:
        cx, cy = int(center[0]), int(center[1])
        return (cx, cy, cx - 20, cy - 20, cx + 20, cy + 20)
    return None


def _run_dumpsys_activity(drv: Any) -> str:
    """Run `dumpsys activity <pkg>` (or `dumpsys activity top`) via driver or ADB."""
    pkg = ""
    try:
        ui = getattr(drv, "ui", None)
        if ui is not None and hasattr(ui, "get_current_activity"):
            act_info = ui.get_current_activity() or {}
            if isinstance(act_info, dict):
                pkg = str(act_info.get("package") or "").strip()
    except Exception:
        pkg = ""

    if hasattr(drv, "shell"):
        try:
            if pkg:
                res = drv.shell(f"dumpsys activity {pkg}")
                out = str((res or {}).get("stdout") or "")
                if "View Hierarchy:" in out:
                    return out
            res = drv.shell("dumpsys activity top")
            return str((res or {}).get("stdout") or "")
        except Exception:
            return ""

    serial = getattr(drv, "device_serial", None)
    if not serial and hasattr(drv, "_device_serial"):
        serial = getattr(drv, "_device_serial", None)
    if not serial and getattr(drv, "platform", "") != "android":
        return ""

    try:
        from phone_pilot.android.adb.runner import CommandRunner
        from phone_pilot.android.adb.utils import adb_prefix

        target = pkg if pkg else "top"
        proc = CommandRunner.run(
            adb_prefix(serial) + ["shell", f"dumpsys activity {target}"],
            check=False,
            delay_s=0.0,
            log_output=False,
            timeout_s=5.0,
        )
        out = str(proc.stdout or "")
        if "View Hierarchy:" not in out and target != "top":
            proc = CommandRunner.run(
                adb_prefix(serial) + ["shell", "dumpsys activity top"],
                check=False,
                delay_s=0.0,
                log_output=False,
                timeout_s=5.0,
            )
            out = str(proc.stdout or "")
        return out
    except Exception:
        return ""


def _extract_close_nodes_from_dumpsys(drv: Any) -> list[dict[str, Any]]:
    """Fast-path (~80ms) extraction of visible close/countdown views from `dumpsys activity`.

    During video ad playback, UIAutomator dump may omit custom Views (such as
    `AdCloseButtonView`) or time out due to continuous SurfaceView frame updates.
    """
    out = _run_dumpsys_activity(drv)
    if not out or "View Hierarchy:" not in out:
        return []

    blocks = re.findall(
        r"(^\s*ACTIVITY\s+(\S+)\s+[^\n]*\n(?:(?!^\s*ACTIVITY\s+\S+).)*)",
        out,
        flags=re.M | re.S,
    )
    target_block = out
    if blocks:
        for block_text, _comp in reversed(blocks):
            if "mResumed=true" in block_text:
                target_block = block_text
                break
        else:
            target_block = blocks[-1][0]

    view_re = re.compile(
        r"^(\s*)(\S+)\{[^}]*\s([VIG][A-Z.]*)\s+\S+\s+(-?\d+),(-?\d+)-(-?\d+),(-?\d+)"
        r"\s*(?:#[0-9a-fA-F]+\s+(?:app|android):id/([^\s}]+))?"
    )
    stack: list[tuple[int, bool, int, int]] = []
    results: list[dict[str, Any]] = []
    in_hierarchy = False

    for line in target_block.splitlines():
        if "View Hierarchy:" in line:
            in_hierarchy = True
            stack = []
            continue
        if not in_hierarchy:
            continue
        stripped = line.strip()
        if stripped.startswith("Looper ") or stripped.startswith("Local Activity"):
            in_hierarchy = False
            continue
        m = view_re.match(line)
        if not m:
            continue
        indent = len(m.group(1))
        cls = m.group(2)
        vis = m.group(3)
        l, t, r_x, b = int(m.group(4)), int(m.group(5)), int(m.group(6)), int(m.group(7))
        res_id = m.group(8) or ""

        while stack and stack[-1][0] >= indent:
            stack.pop()
        parent_vis = stack[-1][1] if stack else True
        parent_l = stack[-1][2] if stack else 0
        parent_t = stack[-1][3] if stack else 0
        has_area = (r_x > l) and (b > t)
        eff_vis = parent_vis and vis.startswith("V") and has_area
        abs_l = parent_l + l
        abs_t = parent_t + t
        abs_r = parent_l + r_x
        abs_b = parent_t + b
        stack.append((indent, eff_vis, abs_l, abs_t))

        if not parent_vis:
            continue
        combined_lower = f"{cls} {res_id}".lower()
        is_target_widget = any(h in combined_lower for h in _CLOSE_WIDGET_HINTS) or any(
            mh in combined_lower for mh in _MUTE_WIDGET_HINTS
        )
        if not is_target_widget:
            continue
        if not has_area:
            # Keep hidden (GONE/INVISIBLE) SDK close widgets with a minimal placeholder box
            # so inspect_ad_close_and_countdown knows the ad container has a pending close button
            if "adclosebuttonview" in combined_lower:
                results.append(
                    {
                        "class_name": cls,
                        "resource_id": res_id,
                        "bounds_rect": [abs_l, abs_t, abs_l + 10, abs_t + 10],
                        "text": "",
                        "content_desc": "",
                        "visible": False,
                    }
                )
            continue
        results.append(
            {
                "class_name": cls,
                "resource_id": res_id,
                "bounds_rect": [abs_l, abs_t, abs_r, abs_b],
                "text": "",
                "content_desc": "",
                "visible": eff_vis,
            }
        )
    return results


def inspect_ad_close_and_countdown(
    nodes_or_state: Any,
    *,
    screen_width: int = 1080,
    screen_height: int = 2400,
    allow_early_skip: bool = True,
) -> dict[str, Any]:
    """Inspect a fullscreen ad UI state to detect countdown timers and disambiguate close vs mute buttons.

    Accepts:
        - Sequence[dict]: list of UI element dicts
        - dict: page_state dict with "elements" or "nodes"
        - ScriptContext / AndroidDriver / UIDriver: live driver from which UI nodes are queried
    """
    nodes: list[dict[str, Any]] = []
    sw, sh = int(screen_width or 1080), int(screen_height or 2400)

    if isinstance(nodes_or_state, dict):
        raw_elems = nodes_or_state.get("elements") or nodes_or_state.get("nodes") or []
        nodes = [dict(n) for n in raw_elems if isinstance(n, dict)]
    elif isinstance(nodes_or_state, (list, tuple)):
        nodes = [dict(n) for n in nodes_or_state if isinstance(n, dict)]
    elif nodes_or_state is not None:
        drv = getattr(nodes_or_state, "driver", nodes_or_state)
        ui = getattr(drv, "ui", drv)
        try:
            if hasattr(drv, "screen") and hasattr(drv.screen, "get_screen_size"):
                sz = drv.screen.get_screen_size()
                if sz and len(sz) == 2:
                    sw, sh = int(sz[0]), int(sz[1])
        except Exception:
            pass
        # 1. Fast-path View Hierarchy inspection via dumpsys activity (~80ms, works during video playback)
        vh_nodes = _extract_close_nodes_from_dumpsys(drv)
        has_sdk_close_view = any(
            any(
                k in str(n.get("class_name", "")).lower()
                for k in ("adclosebuttonview", "countdownprogressbar")
            )
            for n in vh_nodes
        )
        # 2. UIAutomator node inspection (skip on real device when SDK AdCloseButtonView is already
        # located in View Hierarchy to avoid 10s uiautomator dump hangs during video rendering)
        ui_nodes: list[dict[str, Any]] = []
        if not (has_sdk_close_view and getattr(drv, "device_serial", None)):
            try:
                if hasattr(ui, "find_elements"):
                    ui_nodes = list(ui.find_elements({}) or [])
            except Exception:
                ui_nodes = []

        # Merge vh_nodes and ui_nodes by spatial proximity so identical widgets are not duplicated
        merged_nodes: list[dict[str, Any]] = list(ui_nodes)
        for vnode in vh_nodes:
            vgeom = _extract_node_geometry(vnode)
            if vgeom is None:
                continue
            vcx, vcy = vgeom[0], vgeom[1]
            matched_existing = False
            for unode in ui_nodes:
                ugeom = _extract_node_geometry(unode)
                if ugeom and abs(ugeom[0] - vcx) <= 16 and abs(ugeom[1] - vcy) <= 16:
                    if not unode.get("class_name") and vnode.get("class_name"):
                        unode["class_name"] = vnode["class_name"]
                    if not unode.get("resource_id") and vnode.get("resource_id"):
                        unode["resource_id"] = vnode["resource_id"]
                    matched_existing = True
                    break
            if not matched_existing:
                merged_nodes.append(vnode)
        nodes = merged_nodes

    countdown_nodes: list[dict[str, Any]] = []
    close_candidates: list[dict[str, Any]] = []
    mute_candidates: list[dict[str, Any]] = []

    top_bar_y_limit = int(sh * 0.28)

    for node in nodes:
        geom = _extract_node_geometry(node)
        if geom is None:
            continue
        cx, cy, x1, y1, x2, y2 = geom
        w, h = x2 - x1, y2 - y1

        text = str(node.get("text") or node.get("label") or "").strip()
        desc = str(node.get("content_desc") or node.get("desc") or "").strip()
        res_id = str(node.get("resource_id") or "").strip()
        cls_name = str(node.get("class_name") or node.get("type") or "").strip()
        combined_id = f"{cls_name} {res_id} {desc}".lower()
        is_visible = bool(node.get("visible", True))

        is_mute_hint = any(mh in combined_id or mh in text.lower() for mh in _MUTE_WIDGET_HINTS)
        if is_mute_hint:
            if is_visible:
                mute_candidates.append(
                    {"cx": cx, "cy": cy, "text": text, "desc": desc, "class_name": cls_name, "reason": "mute_keyword"}
                )
            continue

        is_close_widget_hint = any(ch in combined_id for ch in _CLOSE_WIDGET_HINTS)
        if not is_visible:
            if is_close_widget_hint and cy <= top_bar_y_limit:
                countdown_nodes.append(
                    {
                        "cx": cx,
                        "cy": cy,
                        "text": text or desc,
                        "seconds": None,
                        "ready_to_close": False,
                        "kind": "hidden_close_widget_pending_countdown",
                        "class_name": cls_name,
                        "resource_id": res_id,
                    }
                )
            continue
        is_top_small_badge = cy <= top_bar_y_limit and w <= int(sw * 0.45) and h <= int(sh * 0.12)
        in_close_widget = is_close_widget_hint or is_top_small_badge

        parsed_text = parse_countdown_text(
            text, in_close_widget=in_close_widget, allow_early_skip=allow_early_skip
        )
        parsed_desc = (
            parse_countdown_text(
                desc, in_close_widget=in_close_widget, allow_early_skip=allow_early_skip
            )
            if desc
            else {"has_countdown": False, "is_close_text": False, "ready_to_close": False, "seconds": None, "kind": "none"}
        )

        parsed = parsed_text if (parsed_text["has_countdown"] or parsed_text["is_close_text"]) else parsed_desc

        if parsed["has_countdown"]:
            entry = {
                "cx": cx,
                "cy": cy,
                "text": text or desc,
                "seconds": parsed["seconds"],
                "ready_to_close": parsed["ready_to_close"],
                "kind": parsed["kind"],
                "class_name": cls_name,
                "resource_id": res_id,
            }
            countdown_nodes.append(entry)
            if parsed["ready_to_close"] and parsed["is_close_text"]:
                close_candidates.append(
                    {
                        **entry,
                        "priority": 100 if cx >= sw * 0.5 else 85,
                        "source": parsed["kind"],
                    }
                )
            continue

        if parsed["is_close_text"] and parsed["ready_to_close"]:
            close_candidates.append(
                {
                    "cx": cx,
                    "cy": cy,
                    "text": text or desc,
                    "class_name": cls_name,
                    "resource_id": res_id,
                    "priority": 100 if cx >= sw * 0.5 else 90,
                    "source": parsed["kind"],
                }
            )
            continue

        # Unlabeled countdown progress widget (e.g., CountdownProgressbar in dumpsys View Hierarchy)
        is_countdown_progress_widget = any(
            cph in combined_id for cph in _COUNTDOWN_PROGRESS_WIDGET_HINTS
        )
        if is_countdown_progress_widget and cy <= top_bar_y_limit:
            countdown_nodes.append(
                {
                    "cx": cx,
                    "cy": cy,
                    "text": text or desc,
                    "seconds": None,
                    "ready_to_close": False,
                    "kind": "countdown_widget_active",
                    "class_name": cls_name,
                    "resource_id": res_id,
                }
            )
            continue

        # Unlabeled close widget (e.g., AdCloseButtonView or iv_close in top bar)
        if is_close_widget_hint and cy <= top_bar_y_limit:
            if cx >= sw * 0.5:
                close_candidates.append(
                    {
                        "cx": cx,
                        "cy": cy,
                        "text": text or desc,
                        "class_name": cls_name,
                        "resource_id": res_id,
                        "priority": 80,
                        "source": "right_top_close_widget",
                    }
                )
            else:
                # Left-side AdCloseButtonView in AdMaster is typically the mute toggle;
                # keep as low-priority fallback only if no right-side close widget exists,
                # and record in mute_candidates when a right-side candidate is present.
                close_candidates.append(
                    {
                        "cx": cx,
                        "cy": cy,
                        "text": text or desc,
                        "class_name": cls_name,
                        "resource_id": res_id,
                        "priority": 25,
                        "source": "left_top_close_widget",
                    }
                )

    # Disambiguate left-side mute vs right-side close when both exist in top bar
    has_right_side_close = any(c["cx"] >= sw * 0.5 for c in close_candidates)
    if has_right_side_close:
        filtered_close: list[dict[str, Any]] = []
        for c in close_candidates:
            if c["source"] == "left_top_close_widget" and c["cx"] < sw * 0.5:
                mute_candidates.append({**c, "reason": "left_side_ad_close_button_view_is_mute"})
            else:
                filtered_close.append(c)
        close_candidates = filtered_close

    close_candidates.sort(key=lambda item: (item["priority"], item["cx"]), reverse=True)

    # Determine overall countdown and readiness state
    active_blocking_countdowns = [
        c
        for c in countdown_nodes
        if not c["ready_to_close"]
        and ((c["seconds"] or 0) > 0 or c.get("kind") == "countdown_widget_active")
    ]
    all_seconds = [int(c["seconds"]) for c in countdown_nodes if c["seconds"] is not None]
    max_countdown_s = max(all_seconds) if all_seconds else None
    has_countdown = len(countdown_nodes) > 0

    best_close = close_candidates[0] if close_candidates else None
    if active_blocking_countdowns and (
        best_close is None or best_close["source"] in ("right_top_close_widget", "left_top_close_widget")
    ):
        # An active countdown is still running and no explicit early-skip text button is available
        ready_to_close = False
        reason = (
            f"countdown_active_{max_countdown_s}s"
            if max_countdown_s is not None
            else "countdown_widget_active"
        )
    elif best_close is not None:
        ready_to_close = True
        reason = f"ready_via_{best_close['source']}"
    elif has_countdown and max_countdown_s == 0:
        ready_to_close = True
        reason = "countdown_reached_zero"
    elif has_countdown:
        ready_to_close = False
        reason = "close_widget_hidden_pending_countdown"
    else:
        ready_to_close = False
        reason = "no_close_or_countdown_found"

    return {
        "has_countdown": has_countdown,
        "countdown_seconds": max_countdown_s,
        "ready_to_close": ready_to_close,
        "close_xy": (int(best_close["cx"]), int(best_close["cy"])) if best_close else None,
        "close_candidate": best_close,
        "countdown_nodes": countdown_nodes,
        "mute_candidates": mute_candidates,
        "reason": reason,
    }


def wait_and_dismiss_ad_with_countdown(
    ctx_or_driver: Any,
    *,
    timeout_s: float = 25.0,
    poll_interval_s: float = 0.8,
    allow_early_skip: bool = True,
) -> dict[str, Any]:
    """Wait for any active ad countdown to finish (or become skippable) and tap the right-side close button."""
    import time

    drv = getattr(ctx_or_driver, "driver", ctx_or_driver)
    deadline = time.time() + max(timeout_s, 1.0)
    waited_countdown_s = 0.0
    observed_countdowns: list[int] = []
    last_inspection: dict[str, Any] = {}

    while time.time() < deadline:
        last_inspection = inspect_ad_close_and_countdown(
            drv, allow_early_skip=allow_early_skip
        )
        cd_sec = last_inspection.get("countdown_seconds")
        if isinstance(cd_sec, int) and cd_sec not in observed_countdowns:
            observed_countdowns.append(cd_sec)

        if last_inspection.get("ready_to_close") and last_inspection.get("close_xy"):
            cx, cy = last_inspection["close_xy"]
            if hasattr(drv, "input") and hasattr(drv.input, "tap"):
                drv.input.tap(cx, cy)
            elif hasattr(drv, "tap"):
                drv.tap(cx, cy)
            return {
                "ok": True,
                "dismissed": True,
                "close_xy": (cx, cy),
                "waited_countdown_s": round(waited_countdown_s, 2),
                "observed_countdowns": observed_countdowns,
                "inspection": last_inspection,
            }

        sleep_dur = min(poll_interval_s, max(0.2, deadline - time.time()))
        time.sleep(sleep_dur)
        waited_countdown_s += sleep_dur

    return {
        "ok": False,
        "dismissed": False,
        "close_xy": last_inspection.get("close_xy"),
        "waited_countdown_s": round(waited_countdown_s, 2),
        "observed_countdowns": observed_countdowns,
        "inspection": last_inspection,
    }
