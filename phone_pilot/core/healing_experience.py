"""通用错误纠正经验库 / Universal error-correction experience store.

按 action + query + app_package + activity 维度存储纠正经验，
跨脚本共享，越用越智能。LLM 的修复建议写入经验库后，下次相同错误零成本复用。
Stores healing experiences by action+query+app+activity dimensions,
shared across scripts, improving over time. LLM fixes are cached for zero-cost reuse.

存储路径 / Storage path:
    .recordings/healing_experience/{package}/{activity}.json
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from phone_pilot.core.storage import iso_now, recordings_root


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class HealingRecord:
    """一条纠正经验记录 / A single healing experience record.

    匹配维度 / Match dimensions:
        action      — 操作类型 ("find_text" / "find_image" / "tap" / "scroll_to_find")
        query       — 操作参数（查找文本、图片路径等）
        app_package — 应用包名
        activity    — 当前 Activity/Ability 名称

    纠正方案 / Fix info:
        error_type  — 错误类型 ("element_not_found" / "popup_blocked" / "timeout" / …)
        fix_type    — 修复类型 ("popup_dismiss" / "param_adjust" / "scroll" / "wait_increase" / "llm_fix")
        fix_detail  — 修复参数 dict
        source      — 来源 ("structure" / "llm_analysis" / "manual" / "builtin_rule")

    统计 / Stats:
        success     — 该纠正是否曾成功解决问题
        hit_count   — 被成功复用的次数
        fail_count  — 复用后仍失败的次数
    """

    # 匹配维度
    action: str = ""
    query: str = ""
    app_package: str = ""
    activity: str = ""

    # 纠正方案
    error_type: str = ""
    fix_type: str = ""
    fix_detail: dict = field(default_factory=dict)
    source: str = ""

    # 元信息
    record_id: str = ""
    success: bool = True
    created_at: str = ""
    last_hit_at: str = ""
    hit_count: int = 0
    fail_count: int = 0

    def __post_init__(self) -> None:
        if not self.record_id:
            self.record_id = _make_record_id(
                self.action, self.query, self.fix_type, self.error_type
            )
        if not self.created_at:
            self.created_at = iso_now()
        if not self.last_hit_at:
            self.last_hit_at = self.created_at

    @property
    def success_rate(self) -> float:
        """成功率 / Success rate (0.0–1.0)."""
        total = self.hit_count + self.fail_count
        if total == 0:
            return 1.0 if self.success else 0.0
        return self.hit_count / total

    def to_dict(self) -> dict:
        """序列化为 dict / Serialize to dict."""
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "HealingRecord":
        """从 dict 反序列化 / Deserialize from dict."""
        known = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in d.items() if k in known}
        return cls(**filtered)


def _make_record_id(action: str, query: str, fix_type: str, error_type: str) -> str:
    """生成稳定的记录 ID / Generate stable record ID."""
    raw = f"{action}|{query}|{fix_type}|{error_type}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# 经验库
# ---------------------------------------------------------------------------

_DEFAULT_SUBDIR = "healing_experience"


class HealingExperienceStore:
    """通用错误纠正经验库 / Universal error-correction experience store.

    按 action+query+app+activity 维度缓存纠正经验。
    Caches healing experiences by action+query+app+activity dimensions.

    Parameters / 参数:
        store_dir: 经验库根目录，默认 .recordings/healing_experience/
    """

    def __init__(self, store_dir: Optional[str | Path] = None) -> None:
        if store_dir:
            self._root = Path(store_dir).expanduser().resolve()
        else:
            self._root = recordings_root() / _DEFAULT_SUBDIR
        self._root.mkdir(parents=True, exist_ok=True)
        # In-memory cache: {file_path_str: [HealingRecord]}
        self._cache: dict[str, list[HealingRecord]] = {}

    @property
    def store_dir(self) -> Path:
        return self._root

    # ---- 查询 ----

    def lookup(
        self,
        action: str,
        query: str,
        package: str,
        activity: str,
        *,
        min_success_rate: float = 0.3,
    ) -> list[HealingRecord]:
        """查询纠正经验 / Lookup healing experiences.

        先精确匹配 action+query+package+activity，
        再 fallback 到 action+package+activity（同页面通用经验）。
        结果按 (success_rate, hit_count) 降序排序。

        First tries exact match on action+query+package+activity,
        then falls back to action+package+activity (page-level generic).
        Results sorted by (success_rate, hit_count) descending.

        Parameters / 参数:
            action: 操作类型 / Action type
            query: 操作参数 / Action query
            package: 应用包名 / App package
            activity: Activity 名称 / Activity name
            min_success_rate: 最低成功率过滤 / Min success rate filter

        Returns / 返回值:
            list[HealingRecord]: 匹配的经验列表（已排序）/ Matched records (sorted)
        """
        results: list[HealingRecord] = []

        # 1. 精确匹配：activity 级别
        records = self._load_activity(package, activity)
        for rec in records:
            if rec.action == action and rec.query == query:
                if rec.success_rate >= min_success_rate:
                    results.append(rec)

        # 2. Fallback：同 action + 同页面，query 不同（通用经验）
        if not results:
            for rec in records:
                if rec.action == action and rec.query != query:
                    if rec.success_rate >= min_success_rate:
                        results.append(rec)

        # 3. Fallback：全局经验
        global_records = self._load_global()
        for rec in global_records:
            if rec.action == action:
                if rec.success_rate >= min_success_rate:
                    results.append(rec)

        # 排序：成功率 * 命中次数 降序
        results.sort(key=lambda r: (r.success_rate, r.hit_count), reverse=True)
        return results

    # ---- 学习 ----

    def learn(self, record: HealingRecord) -> HealingRecord:
        """写入新经验或合并已有经验 / Write new experience or merge with existing.

        如果已存在相同 record_id 的记录，更新 hit_count 和 last_hit_at；
        否则作为新记录插入。
        If a record with the same record_id exists, updates hit_count and last_hit_at;
        otherwise inserts as new.

        Parameters / 参数:
            record: 经验记录 / Healing record

        Returns / 返回值:
            HealingRecord: 写入/更新后的记录 / The saved/updated record
        """
        records = self._load_activity(record.app_package, record.activity)
        existing = None
        for r in records:
            if r.record_id == record.record_id:
                existing = r
                break

        if existing:
            existing.hit_count += 1
            existing.last_hit_at = iso_now()
            if record.fix_detail:
                existing.fix_detail.update(record.fix_detail)
            existing.success = record.success
        else:
            record.created_at = record.created_at or iso_now()
            record.last_hit_at = iso_now()
            record.hit_count = max(record.hit_count, 1)
            records.append(record)
            existing = record

        self._save_activity(record.app_package, record.activity, records)
        return existing

    def learn_global(self, record: HealingRecord) -> HealingRecord:
        """写入全局经验（跨 app 通用规则）/ Write global experience.

        Parameters / 参数:
            record: 经验记录 / Healing record

        Returns / 返回值:
            HealingRecord: 写入后的记录 / Saved record
        """
        records = self._load_global()
        existing = None
        for r in records:
            if r.record_id == record.record_id:
                existing = r
                break

        if existing:
            existing.hit_count += 1
            existing.last_hit_at = iso_now()
            existing.success = record.success
        else:
            record.created_at = record.created_at or iso_now()
            record.last_hit_at = iso_now()
            record.hit_count = max(record.hit_count, 1)
            records.append(record)
            existing = record

        self._save_global(records)
        return existing

    # ---- 反馈 ----

    def report_outcome(
        self,
        record_id: str,
        success: bool,
        *,
        package: str = "",
        activity: str = "",
    ) -> Optional[HealingRecord]:
        """反馈纠正结果 / Report healing outcome.

        更新 hit_count（成功）或 fail_count（失败）。
        Updates hit_count (success) or fail_count (failure).

        Parameters / 参数:
            record_id: 经验记录 ID
            success: 纠正是否成功
            package: 应用包名（用于定位文件）
            activity: Activity 名（用于定位文件）

        Returns / 返回值:
            HealingRecord: 更新后的记录（或 None）
        """
        # Try activity-level file
        if package and activity:
            records = self._load_activity(package, activity)
            for rec in records:
                if rec.record_id == record_id:
                    if success:
                        rec.hit_count += 1
                    else:
                        rec.fail_count += 1
                    rec.last_hit_at = iso_now()
                    self._save_activity(package, activity, records)
                    return rec

        # Try global
        records = self._load_global()
        for rec in records:
            if rec.record_id == record_id:
                if success:
                    rec.hit_count += 1
                else:
                    rec.fail_count += 1
                rec.last_hit_at = iso_now()
                self._save_global(records)
                return rec

        return None

    # ---- 清理 ----

    def prune(
        self,
        *,
        max_age_days: int = 90,
        min_success_rate: float = 0.3,
    ) -> int:
        """清理过期或低质量经验 / Prune expired or low-quality experiences.

        Parameters / 参数:
            max_age_days: 最大保留天数 / Max retention days
            min_success_rate: 最低成功率 / Minimum success rate

        Returns / 返回值:
            int: 删除的记录数 / Number of pruned records
        """
        pruned = 0
        cutoff = time.time() - max_age_days * 86400

        for json_file in self._root.rglob("*.json"):
            records = self._load_file(json_file)
            before = len(records)
            records = [
                r
                for r in records
                if _parse_ts(r.last_hit_at) > cutoff and r.success_rate >= min_success_rate
            ]
            after = len(records)
            if after < before:
                pruned += before - after
                self._write_file(json_file, records)

        return pruned

    # ---- 统计 ----

    def get_stats(self) -> dict:
        """返回经验库统计 / Return experience store statistics.

        Returns / 返回值:
            dict: {total, by_fix_type, by_source, avg_success_rate, ...}
        """
        total = 0
        by_fix_type: dict[str, int] = {}
        by_source: dict[str, int] = {}
        success_rates: list[float] = []

        for json_file in self._root.rglob("*.json"):
            if json_file.name == "_stats.json":
                continue
            records = self._load_file(json_file)
            for rec in records:
                total += 1
                by_fix_type[rec.fix_type] = by_fix_type.get(rec.fix_type, 0) + 1
                by_source[rec.source] = by_source.get(rec.source, 0) + 1
                success_rates.append(rec.success_rate)

        avg_rate = sum(success_rates) / len(success_rates) if success_rates else 0.0
        return {
            "total_records": total,
            "by_fix_type": by_fix_type,
            "by_source": by_source,
            "avg_success_rate": round(avg_rate, 3),
        }

    # ---- 内部 I/O ----

    def _activity_file(self, package: str, activity: str) -> Path:
        """经验文件路径 / Experience file path."""
        pkg_dir = self._root / _safe_filename(package)
        pkg_dir.mkdir(parents=True, exist_ok=True)
        return pkg_dir / f"{_safe_filename(activity)}.json"

    def _global_file(self) -> Path:
        return self._root / "_global.json"

    def _load_activity(self, package: str, activity: str) -> list[HealingRecord]:
        return self._load_file(self._activity_file(package, activity))

    def _save_activity(
        self, package: str, activity: str, records: list[HealingRecord]
    ) -> None:
        self._write_file(self._activity_file(package, activity), records)

    def _load_global(self) -> list[HealingRecord]:
        return self._load_file(self._global_file())

    def _save_global(self, records: list[HealingRecord]) -> None:
        self._write_file(self._global_file(), records)

    def _load_file(self, path: Path) -> list[HealingRecord]:
        key = str(path)
        if key in self._cache:
            return self._cache[key]
        records: list[HealingRecord] = []
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    records = [HealingRecord.from_dict(d) for d in data]
            except Exception:
                records = []
        self._cache[key] = records
        return records

    def _write_file(self, path: Path, records: list[HealingRecord]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = [r.to_dict() for r in records]
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        self._cache[str(path)] = records


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

def _safe_filename(name: str) -> str:
    """将包名 / Activity 名转为安全文件名 / Sanitize to safe filename."""
    if not name:
        return "_unknown"
    # Replace characters that are problematic for file systems
    safe = name.replace("/", "_").replace("\\", "_").replace(":", "_")
    # Keep only alphanumeric, dots, underscores, hyphens
    safe = "".join(c for c in safe if c.isalnum() or c in "._-")
    return safe or "_unknown"


def _parse_ts(ts_str: str) -> float:
    """解析 ISO 时间字符串为 Unix 时间戳 / Parse ISO time string to Unix timestamp."""
    if not ts_str:
        return 0.0
    try:
        from datetime import datetime, timezone
        # Handle ISO format like "2026-02-06T10:00:00"
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return 0.0
