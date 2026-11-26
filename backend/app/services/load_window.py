"""装载窗口领域服务：维护装载区间管理和偏差检测，校验水位推进，产出装载窗口数据集。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class LoadWindowService(BaseModuleService):
    MODULE = "load_window"
    ENTITY = "装载窗口"
    LIST_FIELDS = ["窗口编号", "所属事实表", "窗口起点", "窗口终点", "预期行数", "实际行数", "装载水位", "装载耗时"]
    REQUIRED_FIELDS = ["窗口编号", "所属事实表", "窗口起点"]
    STATUS_ORDER = ["待装载", "装载中", "已装载", "装载中断"]
    ACTION_RULES = {"启动装载": "装载中", "确认装载": "已装载", "标记中断": "装载中断"}
    NEGATIVE_ACTIONS = ["标记中断"]

    @staticmethod
    def deviation_ratio(expected_rows: int, actual_rows: int) -> float | None:
        """既有偏差判定：预期行数必须为正，偏差超过 20% 不得推进。"""
        if expected_rows <= 0:
            return None
        return abs(actual_rows - expected_rows) / expected_rows

    def find_window(self, reference: Any) -> dict[str, Any] | None:
        """按窗口编号、主键或窗口回执引用找到实际窗口。"""
        if reference is None:
            return None

        if isinstance(reference, dict):
            # 允许直接传入 advance_window/动作边界回包中的 entry。
            entry = reference.get("entry", reference)
            if not isinstance(entry, dict):
                return None
            if entry.get("id") is not None:
                found = store.find(self.MODULE, int(entry["id"]))
                if found is not None:
                    return found
            reference = entry.get("窗口编号") or entry.get("id")

        text = str(reference).strip()
        if not text:
            return None

        rows = store.rows(self.MODULE)
        for row in rows:
            if str(row.get("窗口编号", "")).strip() == text:
                return row

        for prefix in ("load_window:", "window:", "id:"):
            if text.startswith(prefix):
                text = text[len(prefix):]
                break

        if text.isdigit():
            return store.find(self.MODULE, int(text))
        return None

    def accepted_cutoff(self, window: dict[str, Any] | None) -> str | None:
        """返回窗口真实受理后的装载截止日期；被拒/未推进窗口没有受理日期。"""
        if not window or window.get("status") != "已装载":
            return None
        if window.get("ok") is False or window.get("accepted") is False:
            return None

        # 受理回执会显式记录截止日期；已推进窗口的水位也等于窗口终点。
        cutoff = (
            window.get("装载截止日期")
            or window.get("受理截止日期")
            or window.get("装载水位")
            or window.get("窗口终点")
        )
        cutoff_text = str(cutoff or "").strip()
        return cutoff_text or None

    def resolve_accepted_dependency(self, reference: Any) -> tuple[dict[str, Any] | None, str | None]:
        """把依赖批次引用解析为已受理装载窗口。

        返回 ``(resolution, error_message)``。引用不是任何装载窗口时返回
        ``(None, None)``，调用方按旧的手工依赖批次处理。
        """
        window = self.find_window(reference)
        if window is None:
            return None, None

        window_no = str(window.get("窗口编号") or reference)
        cutoff = self.accepted_cutoff(window)
        fact_table = str(window.get("所属事实表") or "").strip()
        if cutoff is None or not fact_table:
            return None, (
                f"依赖的装载窗口 {window_no} 尚未受理或推进被拒，"
                "不能使用预览截止日期排期"
            )

        resolution = {
            "matched": True,
            "accepted": True,
            "module": self.MODULE,
            "window_id": window.get("id"),
            "window_no": window_no,
            "cutoff_date": cutoff,
            "fact_table": fact_table,
            "window": window,
            # 中文键便于直接写入既有工作台行数据。
            "装载窗口编号": window_no,
            "装载截止日期": cutoff,
            "所属事实表": fact_table,
        }
        return resolution, None

    def advance_window(
        self, window_id: str, fact_table: str, start: str, end: str,
        expected_rows: int, actual_rows: int,
    ) -> tuple[bool, str, dict[str, Any] | None]:
        """推进装载窗口：实际行数与预期行数偏差超过 20% 时窗口不得推进。"""
        ratio = self.deviation_ratio(expected_rows, actual_rows)
        if ratio is None:
            return False, f"预期行数 {expected_rows} 无效", None

        if ratio > 0.20:
            entry = {
                "窗口编号": window_id, "所属事实表": fact_table,
                "窗口起点": start, "窗口终点": end,
                "预期行数": expected_rows, "实际行数": actual_rows,
                "装载水位": start, "装载耗时": "",
                "status": "装载中断", "pending": False, "abnormal": True,
                "偏差率": f"{ratio:.1%}",
                "accepted": False, "ok": False,
            }
            store.append_row(self.MODULE, entry)
            return False, (
                f"实际行数 {actual_rows} 与预期 {expected_rows} 偏差 {ratio:.1%}，窗口不得推进"
            ), entry

        entry = {
            "窗口编号": window_id, "所属事实表": fact_table,
            "窗口起点": start, "窗口终点": end,
            "预期行数": expected_rows, "实际行数": actual_rows,
            "装载水位": end, "装载耗时": "1.5s",
            "装载截止日期": end,
            "status": "已装载", "pending": False, "abnormal": False,
            "accepted": True, "ok": True,
        }
        store.append_row(self.MODULE, entry)
        return True, f"装载窗口 [{start}, {end}] 已推进，水位 {end}", entry

    @staticmethod
    def _row_row_counts(row: dict[str, Any]) -> tuple[int | None, int | None, str | None]:
        try:
            expected = int(float(row.get("预期行数")))
            actual = int(float(row.get("实际行数")))
        except (TypeError, ValueError):
            return None, None, "预期行数或实际行数不是有效整数"
        return expected, actual, None

    def run_action_detail(
        self, entry_id: int, action: str,
    ) -> tuple[bool, str, dict[str, Any] | None]:
        """行内“确认装载”复用既有偏差判定，并只推进被提交的那一行。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return False, f"{self.ENTITY} {entry_id} 不存在或已归档", None

        if action != "确认装载":
            entry, message = super().run_action(entry_id, action)
            return entry is not None, message, entry

        expected, actual, parse_error = self._row_row_counts(entry)
        if parse_error is not None:
            return False, parse_error, entry
        assert expected is not None and actual is not None

        ratio = self.deviation_ratio(expected, actual)
        if ratio is None:
            message = f"预期行数 {expected} 无效"
            with store.lock:
                entry["status"] = "装载中断"
                entry["pending"] = False
                entry["abnormal"] = True
            return False, message, entry

        if ratio > 0.20:
            message = (
                f"实际行数 {actual} 与预期 {expected} 偏差 {ratio:.1%}，窗口不得推进"
            )
            with store.lock:
                entry["status"] = "装载中断"
                entry["pending"] = False
                entry["abnormal"] = True
                entry["偏差率"] = f"{ratio:.1%}"
                entry["accepted"] = False
                entry["ok"] = False
            return False, message, entry

        end = str(entry.get("窗口终点") or entry.get("窗口起点") or "").strip()
        start = str(entry.get("窗口起点") or "").strip()
        message = f"装载窗口 [{start}, {end}] 已推进，水位 {end}"
        with store.lock:
            entry["status"] = "已装载"
            entry["pending"] = False
            entry["abnormal"] = False
            entry["装载水位"] = end
            entry["装载截止日期"] = end
            entry.setdefault("装载耗时", "1.5s")
            entry["accepted"] = True
            entry["ok"] = True
        return True, message, entry

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        ok, message, entry = self.run_action_detail(entry_id, action)
        # 旧接口用 entry is None 表示失败；被偏差规则拒绝的行仍可通过富回执查看。
        return (entry if ok else None), message

    def detect_dirty_window(self, fact_table: str) -> list[dict[str, Any]]:
        """检测脏数据窗口：偏差超阈值或中断的窗口。"""
        rows = store.rows(self.MODULE)
        return [dict(r) for r in rows if r.get("所属事实表") == fact_table
                and (r.get("status") == "装载中断" or r.get("abnormal"))]
