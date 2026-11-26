"""内存数据仓库：给每个业务模块准备一份可筛选、可流转的示例数据。

真实项目里这里会换成数据库访问层；当前实现只依赖标准库，保证克隆下来就能起。
"""
from __future__ import annotations

import threading
from typing import Any

from app.seed import SEED_ROWS


class Store:
    def __init__(self) -> None:
        self._tables: dict[str, list[dict[str, Any]]] = {
            name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()
        }
        # 工作台请求可能在线程池中并发；编号分配和追加必须是一个临界区，
        # 避免两个实际提交对象拿到同一个 id 或互相覆盖。
        self.lock = threading.RLock()

    def module_names(self) -> list[str]:
        return sorted(self._tables)

    def rows(self, module: str) -> list[dict[str, Any]]:
        return self._tables.setdefault(module, [])

    def next_id(self, module: str) -> int:
        with self.lock:
            rows = self.rows(module)
            return max((int(row.get("id", 0)) for row in rows), default=0) + 1

    def append_row(self, module: str, row: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            if row.get("id") is None:
                row["id"] = self.next_id(module)
            rows = self.rows(module)
            rows.append(row)
            return row

    def find(self, module: str, entry_id: int) -> dict[str, Any] | None:
        with self.lock:
            for row in self.rows(module):
                if int(row.get("id", 0)) == entry_id:
                    return row
        return None

    def overview(self) -> dict[str, object]:
        modules: list[dict[str, object]] = []
        for name in self.module_names():
            rows = self.rows(name)
            modules.append({
                "name": name,
                "created": len(rows),
                "pending": sum(1 for row in rows if row.get("pending")),
                "abnormal": sum(1 for row in rows if row.get("abnormal")),
            })
        cards = [
            {"label": "业务模块", "value": len(modules)},
            {"label": "今日新增", "value": sum(int(item["created"]) for item in modules)},
            {"label": "待处理", "value": sum(int(item["pending"]) for item in modules)},
            {"label": "异常量", "value": sum(int(item["abnormal"]) for item in modules)},
        ]
        return {"cards": cards, "modules": modules}


store = Store()
