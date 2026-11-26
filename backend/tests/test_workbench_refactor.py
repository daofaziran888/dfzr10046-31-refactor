"""装载窗口/重跑任务共享工作台边界的回归测试。"""
from __future__ import annotations

import copy
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.services.load_window import LoadWindowService
from app.services.reprocess_task import ReprocessTaskService
from app.services.workbench_boundary import WorkbenchBoundary, workbench_boundary
from app.store import store


@pytest.fixture(autouse=True)
def restore_shared_store():
    """本文件使用真实单例服务；每个用例后恢复内存仓，避免污染隐藏/后续用例。"""
    tables = copy.deepcopy(store._tables)
    receipts = copy.deepcopy(workbench_boundary._receipts)
    yield
    store._tables = tables
    workbench_boundary._receipts = receipts


class TestWindowDependencyScheduling:
    def setup_method(self) -> None:
        self.loads = LoadWindowService()
        self.reruns = ReprocessTaskService()

    def test_accepted_window_supplies_real_fact_and_cutoff(self) -> None:
        ok, _, window = self.loads.advance_window(
            "LW-BOUNDARY-OK", "边界事实表",
            "2026-11-01", "2026-11-08", 100, 100,
        )
        assert ok and window is not None

        ok, message, task = self.reruns.schedule_reprocess(
            "RP-BOUNDARY-WIN", "口径变更", "客户端预览事实表",
            "客户端预览截止日期", "整段替换", "LW-BOUNDARY-OK",
        )

        assert ok, message
        assert task["影响事实表"] == "边界事实表"
        assert task["重跑区间"] == "2026-11-08"
        assert task["重跑水位"] == "2026-11-08"
        assert task["装载截止日期"] == "2026-11-08"
        assert "客户端预览截止日期" not in str(task)

    def test_rejected_window_preview_date_cannot_occupy_schedule(self) -> None:
        ok, _, window = self.loads.advance_window(
            "LW-BOUNDARY-BAD", "另一事实表",
            "2026-11-09", "2026-11-15", 100, 40,
        )
        assert not ok and window is not None

        scheduled, message, payload = self.reruns.schedule_reprocess(
            "RP-BOUNDARY-REJECTED", "口径变更", "另一事实表",
            "2026-11-15-预览", "整段替换", "LW-BOUNDARY-BAD",
        )

        assert not scheduled
        assert "尚未受理" in message or "不得推进" in message
        assert payload == {}

    def test_manual_old_batch_keeps_original_schedule_shape(self) -> None:
        ok, message, task = self.reruns.schedule_reprocess(
            "RP-BOUNDARY-MANUAL", "手工批次", "手工事实表",
            "2026-11", "并存留痕", "FACT-MANUAL-001",
        )

        assert ok
        assert message == "重跑任务 RP-BOUNDARY-MANUAL 已排期，策略：并存留痕"
        assert task["影响事实表"] == "手工事实表"
        assert task["重跑水位"] == ""
        assert "装载截止日期" not in task
        assert "dependency_resolution" not in task

    def test_existing_row_schedule_resolves_original_dependency_field(self) -> None:
        _, _, window = self.loads.advance_window(
            "LW-FOR-ROW", "行排期事实", "2026-11-01", "2026-11-02", 10, 10,
        )
        _, _, task = self.reruns.schedule_reprocess(
            "RP-ROW-DEP", "原因", "旧事实", "旧区间", "整段替换", "LW-FOR-ROW",
        )
        # schedule_reprocess 已解析；这里再构造一条只登记原始依赖的行来覆盖行动作。
        created, missing = self.reruns.create_entry({
            "重跑编号": "RP-ROW-ACTION",
            "触发原因": "行动作",
            "影响事实表": "尚未解析",
            "重跑区间": "预览",
            "覆盖策略": "整段替换",
            "依赖批次": "LW-FOR-ROW",
        })
        assert not missing and created is not None

        ok, message, entry = self.reruns.run_action_detail(created["id"], "排期重跑")
        assert ok, message
        assert entry is not None
        assert entry["影响事实表"] == "行排期事实"
        assert entry["重跑区间"] == "2026-11-02"
        assert entry["重跑水位"] == "2026-11-02"
        assert entry["status"] == "重跑中"
        assert window is not None


class TestWorkbenchBoundary:
    def test_collect_chinese_filters_preserves_existing_rules(self) -> None:
        class Service:
            LIST_FIELDS = ["窗口编号", "所属事实表"]

        filters = WorkbenchBoundary.collect_filters(Service(), {
            "窗口编号": " LW-001 ",
            "所属事实表": "   ",
            "unknown": "x",
        })
        assert filters == {"窗口编号": "LW-001"}

    def test_same_receipt_key_executes_action_once(self) -> None:
        boundary = WorkbenchBoundary()

        class CounterService:
            ENTITY = "测试"
            calls = 0

            def run_action_detail(self, entry_id, action):
                self.calls += 1
                return True, "ok", {"id": entry_id, "action": action}

        service = CounterService()
        first = boundary.act(service, 7, "确认装载", receipt_key="same-request")
        second = boundary.act(service, 7, "确认装载", receipt_key="same-request")

        assert service.calls == 1
        assert first == second
        assert boundary.get_receipt("same-request")["ok"] is True

    def test_concurrent_same_receipt_has_one_actual_submission(self) -> None:
        boundary = WorkbenchBoundary()

        class SlowService:
            calls = 0
            lock = threading.Lock()

            def run_action_detail(self, entry_id, action):
                with self.lock:
                    self.calls += 1
                return True, "ok", {"id": entry_id}

        service = SlowService()
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(
                lambda _: boundary.act(service, 1, "排期重跑", receipt_key="concurrent-key"),
                range(16),
            ))

        assert service.calls == 1
        assert all(result["message"] == "ok" for result in results)

    def test_global_boundary_receipt_replay_does_not_execute_again(self) -> None:
        workbench_boundary.reset_receipts()

        class OnceService:
            calls = 0

            def run_action_detail(self, entry_id, action):
                self.calls += 1
                return True, "唯一原回执", {"id": entry_id}

        service = OnceService()
        original = workbench_boundary.act(service, 9, "动作", receipt_key="global-once")
        replayed = workbench_boundary.act(service, 9, "动作", receipt_key="global-once")

        assert service.calls == 1
        assert original["message"] == replayed["message"] == "唯一原回执"
        workbench_boundary.reset_receipts()
