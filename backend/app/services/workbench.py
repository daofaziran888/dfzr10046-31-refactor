"""工作台共享边界的兼容导入路径。"""
from app.services.workbench_boundary import (
    WorkbenchBoundary,
    workbench_boundary,
)

boundary = workbench_boundary
shared_boundary = workbench_boundary

__all__ = ["WorkbenchBoundary", "workbench_boundary", "boundary", "shared_boundary"]
