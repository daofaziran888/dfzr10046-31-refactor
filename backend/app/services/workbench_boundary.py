"""工作台请求与动作回执边界。

路由只负责 HTTP 适配；中文筛选键收集、登记结果、行动作显式 ok、幂等回执和
跨模块依赖解析都从这里验证。旧浏览器不带请求标识时完全退化为原有单次请求。
"""
from __future__ import annotations

import copy
import threading
from typing import Any, Mapping

from app.services import service_for


class WorkbenchBoundary:
    def __init__(self) -> None:
        self._receipts: dict[str, dict[str, Any]] = {}
        self._key_locks: dict[str, threading.RLock] = {}
        self._registry_lock = threading.RLock()

    @staticmethod
    def collect_filters(service: Any, query_params: Mapping[str, Any]) -> dict[str, str]:
        """沿用旧接口：只收 LIST_FIELDS 中的中文键，并去掉首尾空白。

        空白串在这一步被自然丢弃；键名、包含匹配和 keyword/status 通道不变。
        """
        return {
            key: str(value).strip()
            for key, value in query_params.items()
            if key in service.LIST_FIELDS and str(value).strip()
        }

    @staticmethod
    def query(
        service: Any,
        *,
        filters: dict[str, str],
        keyword: str | None,
        status: str | None,
        page: int,
        size: int,
    ) -> dict[str, Any]:
        items, total = service.list_entries(
            filters=filters, keyword=keyword, status=status, page=page, size=size,
        )
        return {"items": items, "total": total, "page": page, "size": size}

    @staticmethod
    def register(service: Any, values: dict[str, Any]) -> dict[str, Any]:
        entry, missing = service.create_entry(values)
        if missing:
            return {
                "ok": False,
                "message": f"缺少必填字段：{'、'.join(missing)}",
                "entry": None,
            }
        return {"ok": True, "message": f"{service.ENTITY}已登记", "entry": entry}

    def act(
        self,
        service: Any,
        entry_id: int,
        action: str,
        *,
        receipt_key: str | None = None,
    ) -> dict[str, Any]:
        """执行行操作；相同 receipt_key 只允许一个实际提交对象产生副作用。"""
        if not receipt_key:
            return self._act_once(service, entry_id, action)

        with self._registry_lock:
            key_lock = self._key_locks.setdefault(receipt_key, threading.RLock())

        with key_lock:
            existing = self._receipts.get(receipt_key)
            if existing is not None:
                # 新入口取回的是同一份原回执快照，不能再次推进/排期。
                return copy.deepcopy(existing)
            receipt = self._act_once(service, entry_id, action)
            receipt["request_id"] = receipt_key
            # 服务行之后可能继续流转；缓存的“原回执”必须定格在本次响应。
            self._receipts[receipt_key] = copy.deepcopy(receipt)
            return copy.deepcopy(receipt)

    @staticmethod
    def _act_once(service: Any, entry_id: int, action: str) -> dict[str, Any]:
        detail = getattr(service, "run_action_detail", None)
        if callable(detail):
            ok, message, entry = detail(entry_id, action.strip())
        else:  # 兼容外部旧服务/测试替身。
            entry, message = service.run_action(entry_id, action.strip())
            ok = entry is not None
        return {"ok": ok, "message": message, "entry": entry}

    def get_receipt(self, receipt_key: str) -> dict[str, Any] | None:
        with self._registry_lock:
            receipt = self._receipts.get(receipt_key)
            return copy.deepcopy(receipt) if receipt is not None else None

    def reset_receipts(self) -> None:
        """供测试和显式运维复位使用；正常请求路径不调用。"""
        with self._registry_lock:
            self._receipts.clear()
            self._key_locks.clear()

    @staticmethod
    def service(module: str) -> Any | None:
        return service_for(module)


workbench_boundary = WorkbenchBoundary()
