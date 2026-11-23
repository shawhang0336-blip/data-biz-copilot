from typing import Any, Dict, Optional


class WorkflowError(RuntimeError):
    """受控工作流异常，携带定位与诊断上下文。"""

    def __init__(self, message: str, *, context: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.context = context or {}
