#!/usr/bin/env python3
"""
Multi-stage image/text finder with shared screenshot.
多级查找器：共享同一张截图，支持图片 → 图片/文字的级联查找。
"""

from __future__ import annotations

import pathlib
from typing import Optional

from android_tool.ocr import find_text_boxes, parse_tesseract_tsv, run_tesseract_tsv
from android_tool.recordings_store import ensure_abs, now_dirname, safe_name
from android_tool.screenshot import take_screenshot_png_bytes
from android_tool.uiautomator import dump_ui_xml, find_nodes, parse_uiautomator_nodes
from android_tool.vision import (
    _annotate_boxes_on_image_bytes,
    _decode_png_to_bgr,
    _find_icon_with_features_on_bgr,
    _is_mostly_black,
    _load_image_bgr,
    _match_template,
    _require_cv2,
    Box,
)


class MultiStageFinder:
    """
    多级查找器：共享截图做级联查找（图片/文字）。
    Multi-stage finder: reuse one screenshot for chained image/text searches.

    用法/Usage:
    - finder = MultiStageFinder(device_serial="xxx")
    - first = finder.find_image(template_path="icon.png")
    - roi = finder.box_to_roi(first["matches"][0])
    - second = finder.find_text(query="登录", roi=roi)
    """

    def __init__(
        self,
        device_serial: Optional[str],
        *,
        out_dir: str = "./recordings",
        name: Optional[str] = None,
        debug_annotate: bool = False,
    ) -> None:
        self.device_serial = device_serial
        self.out_root = ensure_abs(out_dir)
        self.name = safe_name(name or "multi_find")
        self.debug_annotate = bool(debug_annotate)
        self._png: Optional[bytes] = None
        self._screen_bgr = None
        self._stats: Optional[dict] = None

    def reset(self) -> None:
        """清空缓存截图/Reset cached screenshot."""
        self._png = None
        self._screen_bgr = None
        self._stats = None

    def capture(self, *, force: bool = False) -> dict:
        """
        获取并缓存当前截图（如已缓存可复用）。
        Capture and cache current screenshot (reusable).
        """
        if self._screen_bgr is not None and not force:
            return {"ok": True, "device_serial": self.device_serial, "cached": True, "stats": self._stats}
        if not self.device_serial:
            return {"ok": False, "error": "device_serial is required"}
        png = take_screenshot_png_bytes(self.device_serial)
        screen_bgr = _decode_png_to_bgr(png)
        is_black, stats = _is_mostly_black(screen_bgr)
        self._png = png
        self._screen_bgr = screen_bgr
        self._stats = stats
        if is_black:
            return {"ok": False, "device_serial": self.device_serial, "error": "screenshot_black", "stats": stats}
        return {"ok": True, "device_serial": self.device_serial, "cached": False, "stats": stats}

    def box_to_roi(self, box: dict, *, padding: int = 0) -> list[int]:
        """
        将匹配 box 转换为 ROI 参数 [x,y,w,h]。
        Convert a match box to ROI list [x,y,w,h].
        """
        x = int(box.get("x", 0))
        y = int(box.get("y", 0))
        w = int(box.get("w", 0))
        h = int(box.get("h", 0))
        pad = max(0, int(padding))
        return [max(0, x - pad), max(0, y - pad), max(1, w + pad * 2), max(1, h + pad * 2)]

    def find_image(
        self,
        *,
        template_path: str,
        roi: Optional[list[int] | tuple[int, int, int, int] | dict] = None,
        threshold: float = 0.85,
        grayscale: bool = True,
        scales: Optional[list[float]] = None,
        method: str = "ccoeff_normed",
        max_results: int = 5,
        ratio_thresh: float = 0.7,
        feature_min_inliers: int = 0,
        feature_enabled: bool = True,
    ) -> dict:
        """
        在共享截图中找图标：先特征匹配(SIFT)，失败后模板匹配。
        Find image on shared screenshot: SIFT first, then template matching.
        """
        cap = self.capture()
        if not cap.get("ok"):
            return cap

        tmpl_path = pathlib.Path(template_path).expanduser().resolve()
        if not tmpl_path.exists():
            return {"ok": False, "error": "template_not_found", "template_path": str(tmpl_path)}

        screen_bgr = self._screen_bgr
        if screen_bgr is None:
            return {"ok": False, "error": "screenshot_missing"}

        tmpl_bgr = _load_image_bgr(tmpl_path)
        roi_t, _ = _normalize_roi(roi)
        screen_roi, (ox, oy) = _crop_with_roi(screen_bgr, roi_t)

        cv2, np = _require_cv2()
        ok, H, inlier_mask = False, None, None
        if feature_enabled:
            try:
                ok, H, inlier_mask = _find_icon_with_features_on_bgr(
                    tmpl_bgr,
                    screen_roi,
                    ratio_thresh=ratio_thresh,
                    min_inliers=int(feature_min_inliers),
                )
            except Exception as e:
                ok, H, inlier_mask = False, None, None
                sift_debug = {"ok": False, "error": str(e)}
            else:
                sift_debug = {
                    "ok": bool(ok),
                    "inliers": len(inlier_mask or []),
                    "ratio_thresh": float(ratio_thresh),
                    "min_inliers": int(feature_min_inliers),
                }
        else:
            sift_debug = {"ok": False, "disabled": True}

        if ok and H is not None:
            th, tw = tmpl_bgr.shape[:2]
            corners = np.float32([[0, 0], [tw, 0], [tw, th], [0, th]]).reshape(-1, 1, 2)
            proj = cv2.perspectiveTransform(corners, H)
            xs = proj[:, 0, 0]
            ys = proj[:, 0, 1]
            x1 = max(0, int(xs.min()))
            y1 = max(0, int(ys.min()))
            x2 = min(screen_roi.shape[1] - 1, int(xs.max()))
            y2 = min(screen_roi.shape[0] - 1, int(ys.max()))
            if x2 > x1 and y2 > y1:
                w = max(1, x2 - x1)
                h = max(1, y2 - y1)
                cx = ox + x1 + w / 2.0
                cy = oy + y1 + h / 2.0
                match = {
                    "x": int(ox + x1),
                    "y": int(oy + y1),
                    "w": int(w),
                    "h": int(h),
                    "center_x": int(round(cx)),
                    "center_y": int(round(cy)),
                    "score": 1.0,
                    "scale": 1.0,
                }
                res = {
                    "ok": True,
                    "device_serial": self.device_serial,
                    "template_path": str(tmpl_path),
                    "count": 1,
                    "matches": [match],
                    "method_used": "sift_features",
                    "debug": {"sift": sift_debug},
                }
                if self.debug_annotate:
                    res["annotated_path"] = self._save_annotated([Box(x=int(ox + x1), y=int(oy + y1), w=int(w), h=int(h), label="sift")], suffix="sift")
                return res

        candidates, meta = _match_template(
            screen_bgr=screen_bgr,
            tmpl_bgr=tmpl_bgr,
            threshold=float(threshold),
            grayscale=bool(grayscale),
            roi=roi_t,
            scales=scales,
            method=method,
            max_results=int(max_results),
        )
        res = {
            "ok": True,
            "device_serial": self.device_serial,
            "template_path": str(tmpl_path),
            **meta,
            "count": len(candidates),
            "matches": [c.to_dict() for c in candidates],
            "method_used": "template",
            "debug": {"sift": sift_debug},
        }
        if self.debug_annotate and candidates:
            boxes = [Box(x=int(c.x), y=int(c.y), w=int(c.w), h=int(c.h), label=f"{c.score:.3f}") for c in candidates]
            res["annotated_path"] = self._save_annotated(boxes, suffix="template")
        return res

    def find_text(
        self,
        *,
        query: str,
        roi: Optional[list[int] | tuple[int, int, int, int] | dict] = None,
        lang: str = "eng",
        psm: int = 6,
        exact: bool = False,
        case_sensitive: bool = False,
        limit: int = 10,
    ) -> dict:
        """
        在共享截图中找文字（OCR），可限定 ROI。
        Find text (OCR) on shared screenshot, optionally within ROI.
        """
        cap = self.capture()
        if not cap.get("ok"):
            return cap

        screen_bgr = self._screen_bgr
        if screen_bgr is None:
            return {"ok": False, "error": "screenshot_missing"}

        roi_t, _ = _normalize_roi(roi)
        screen_roi, (ox, oy) = _crop_with_roi(screen_bgr, roi_t)

        out_dir = self.out_root / "ocr"
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = now_dirname()
        name = safe_name(f"{self.name}_{ts}")
        img_path = out_dir / f"{name}_roi.png"

        cv2, _ = _require_cv2()
        cv2.imwrite(str(img_path), screen_roi)

        tsv = run_tesseract_tsv(img_path, lang=lang, psm=psm)
        boxes = parse_tesseract_tsv(tsv)
        hits = find_text_boxes(
            boxes,
            query=query,
            exact=exact,
            case_sensitive=case_sensitive,
            limit=limit,
        )

        matches = []
        for b in hits:
            d = b.to_dict()
            d["x"] = int(d["x"] + ox)
            d["y"] = int(d["y"] + oy)
            d["center_x"] = int(d["center_x"] + ox)
            d["center_y"] = int(d["center_y"] + oy)
            matches.append(d)

        res = {
            "ok": True,
            "device_serial": self.device_serial,
            "query": query,
            "lang": lang,
            "psm": int(psm),
            "exact": bool(exact),
            "case_sensitive": bool(case_sensitive),
            "roi": roi_t,
            "matches_count": len(matches),
            "matches": matches,
            "screenshot_path": str(img_path),
        }
        if self.debug_annotate and matches:
            boxes = [Box(x=int(m["x"]), y=int(m["y"]), w=int(m["w"]), h=int(m["h"]), label=m.get("text")) for m in matches]
            res["annotated_path"] = self._save_annotated(boxes, suffix="ocr")
        return res

    def find_text_ui(
        self,
        *,
        query: str,
        roi: Optional[list[int] | tuple[int, int, int, int] | dict] = None,
        field: str = "text_or_desc",
        exact: bool = False,
        case_sensitive: bool = False,
        limit: int = 10,
    ) -> dict:
        """
        在当前 UI 层级中找文字（非 OCR），可限定 ROI。
        Find text via UIAutomator (no OCR), optionally within ROI.
        """
        if not self.device_serial:
            return {"ok": False, "error": "device_serial is required"}

        res = dump_ui_xml(self.device_serial, compressed=True)
        if not (isinstance(res, dict) and res.get("ok") and res.get("xml")):
            return {"ok": False, "error": "ui_dump_failed", "detail": res}

        nodes = parse_uiautomator_nodes(str(res.get("xml") or ""))
        hits = find_nodes(
            nodes,
            query=query,
            field=field,
            exact=bool(exact),
            case_sensitive=bool(case_sensitive),
            limit=int(limit),
        )

        roi_t, _ = _normalize_roi(roi)
        matches = []
        if roi_t:
            rx, ry, rw, rh = roi_t
            rx2 = rx + rw
            ry2 = ry + rh
            for n in hits:
                b = n.bounds_tuple()
                if not b:
                    continue
                x1, y1, x2, y2 = b
                cx = int((x1 + x2) / 2)
                cy = int((y1 + y2) / 2)
                if rx <= cx <= rx2 and ry <= cy <= ry2:
                    matches.append(n.to_dict())
        else:
            matches = [n.to_dict() for n in hits]

        return {
            "ok": True,
            "device_serial": self.device_serial,
            "query": query,
            "field": field,
            "exact": bool(exact),
            "case_sensitive": bool(case_sensitive),
            "roi": roi_t,
            "matches_count": len(matches),
            "matches": matches,
        }

    def _save_annotated(self, boxes: list[Box], *, suffix: str) -> Optional[str]:
        if not boxes or self._screen_bgr is None:
            return None
        try:
            png = _annotate_boxes_on_image_bytes(img_bgr=self._screen_bgr, boxes=boxes)
            ts = now_dirname()
            out_path = self.out_root / "multi_stage_debug" / f"{self.name}_{ts}_{suffix}.png"
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(png)
            return str(out_path)
        except Exception:
            return None


def _normalize_roi(roi: Optional[list[int] | tuple[int, int, int, int] | dict]) -> tuple[Optional[tuple[int, int, int, int]], tuple[int, int]]:
    if roi is None:
        return None, (0, 0)
    if isinstance(roi, dict):
        return (
            (int(roi.get("x", 0)), int(roi.get("y", 0)), int(roi.get("w", 0)), int(roi.get("h", 0))),
            (int(roi.get("x", 0)), int(roi.get("y", 0))),
        )
    try:
        return (int(roi[0]), int(roi[1]), int(roi[2]), int(roi[3])), (int(roi[0]), int(roi[1]))
    except Exception:
        return None, (0, 0)


def _crop_with_roi(img_bgr, roi: Optional[tuple[int, int, int, int]]):
    if not roi:
        return img_bgr, (0, 0)
    x, y, w, h = roi
    x = max(0, int(x))
    y = max(0, int(y))
    w = max(1, int(w))
    h = max(1, int(h))
    return img_bgr[y : y + h, x : x + w], (x, y)
