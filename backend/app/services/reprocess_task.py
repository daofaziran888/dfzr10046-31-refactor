"""重跑任务领域服务：覆盖策略与血缘影响传递，调度重跑状态机和水位推进，产出重跑任务数据集。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.services.load_window import LoadWindowService
from app.store import store


class ReprocessTaskService(BaseModuleService):
    MODULE = "reprocess_task"
    ENTITY = "重跑任务"
    LIST_FIELDS = ["重跑编号", "触发原因", "影响事实表", "重跑区间", "覆盖策略", "依赖批次", "重跑水位", "重跑耗时"]
    REQUIRED_FIELDS = ["重跑编号", "触发原因", "影响事实表"]
    STATUS_ORDER = ["待排期", "重跑中", "已完成", "重跑失败"]
    ACTION_RULES = {"排期重跑": "重跑中", "确认完成": "已完成", "标记失败": "重跑失败"}
    NEGATIVE_ACTIONS = ["标记失败"]

    def _resolve_dependency(
        self, depends_on: Any,
    ) -> tuple[dict[str, Any] | None, str | None]:
        """解析依赖批次；只有确实指向装载窗口时才改变手工批次语义。"""
        return LoadWindowService().resolve_accepted_dependency(depends_on)

    @staticmethod
    def _window_schedule_fields(resolution: dict[str, Any]) -> dict[str, Any]:
        cutoff = resolution["cutoff_date"]
        fact_table = resolution["fact_table"]
        return {
            # 客户端预览中的事实表/区间日期不得继续占据排期字段。
            "影响事实表": fact_table,
            "重跑区间": cutoff,
            "重跑水位": cutoff,
            "装载截止日期": cutoff,
            "装载窗口编号": resolution["window_no"],
            "窗口事实表": fact_table,
            "dependency_resolution": {
                "type": "load_window",
                "window_id": resolution["window_id"],
                "window_no": resolution["window_no"],
                "cutoff_date": cutoff,
                "fact_table": fact_table,
            },
        }

    def _apply_window_dependency(self, entry: dict[str, Any]) -> str | None:
        """把已受理窗口的真实事实表/截止日期绑定到实际重跑任务。"""
        resolution, error = self._resolve_dependency(entry.get("依赖批次"))
        if error:
            return error
        if resolution is None:
            return None

        with store.lock:
            entry.update(self._window_schedule_fields(resolution))
        return None

    def schedule_reprocess(
        self, task_id: str, reason: str, target_table: str,
        reprocess_range: str, strategy: str, depends_on: str,
    ) -> tuple[bool, str, dict[str, Any]]:
        """排期重跑任务：覆盖策略决定旧区间是整段替换还是并存留痕。"""
        if strategy not in ("整段替换", "并存留痕"):
            return False, f"覆盖策略 {strategy} 无效", {}

        resolution, dependency_error = self._resolve_dependency(depends_on)
        if dependency_error:
            return False, dependency_error, {}

        entry = {
            "重跑编号": task_id, "触发原因": reason,
            "影响事实表": target_table, "重跑区间": reprocess_range,
            "覆盖策略": strategy, "依赖批次": depends_on,
            "重跑水位": "", "重跑耗时": "",
            "status": "待排期", "pending": True, "abnormal": False,
        }
        if resolution is not None:
            entry.update(self._window_schedule_fields(resolution))

        store.append_row(self.MODULE, entry)
        return True, f"重跑任务 {task_id} 已排期，策略：{strategy}", entry

    def run_action_detail(
        self, entry_id: int, action: str,
    ) -> tuple[bool, str, dict[str, Any] | None]:
        """行内排期先解析原有依赖批次，再推进实际那一条重跑任务。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return False, f"{self.ENTITY} {entry_id} 不存在或已归档", None

        if action == "排期重跑":
            dependency_error = self._apply_window_dependency(entry)
            if dependency_error:
                return False, dependency_error, entry

        updated, message = super().run_action(entry_id, action)
        return updated is not None, message, updated

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        ok, message, entry = self.run_action_detail(entry_id, action)
        return (entry if ok else None), message

    def propagate_lineage_impact(self, target_table: str) -> dict[str, list[str]]:
        """沿血缘边传递重跑影响范围。"""
        lineage_rows = store.rows("lineage_edge")
        direct: list[str] = []
        for r in lineage_rows:
            if r.get("上游对象") == target_table:
                direct.append(str(r.get("下游对象", "")))
        seen = set(direct)
        indirect: list[str] = []
        for d in direct:
            for r in lineage_rows:
                if r.get("上游对象") == d and r.get("下游对象") not in seen:
                    indirect.append(str(r.get("下游对象", "")))
                    seen.add(str(r.get("下游对象", "")))
        return {"direct_impact": direct, "indirect_impact": indirect}

    def complete_reprocess(self, task_id: str, watermark: str) -> tuple[bool, str]:
        """确认重跑完成：更新重跑水位。"""
        rows = store.rows(self.MODULE)
        for r in rows:
            if r.get("重跑编号") == task_id:
                r["status"] = "已完成"
                r["重跑水位"] = watermark
                r["pending"] = False
                return True, f"重跑任务 {task_id} 已完成，水位 {watermark}"
        return False, f"重跑任务 {task_id} 不存在"
