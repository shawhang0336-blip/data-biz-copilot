"""运行配置、预算存储与身份占位常量（基础设施层 Layer 0）。"""

from functools import lru_cache
from pathlib import Path
from typing import Dict, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.orm import Session

ROOT_DIR = Path(__file__).resolve().parents[3]
OPERATOR_PLACEHOLDER = "业务确认人"

DEFAULT_BUDGET: Dict[str, int] = {
    "max_retries": 3,
    "max_llm_concurrency": 4,
    "max_llm_calls": 120,
    "max_total_tokens": 1500000,
    "max_elapsed_seconds": 900,
}


class Settings(BaseSettings):
    app_name: str = "Data Copilot 业务逻辑资产与智能问答台"
    database_url: str = f"sqlite:///{ROOT_DIR / 'backend' / 'var' / 'dc_agent.db'}"
    blob_dir: str = str(ROOT_DIR / "backend" / "var" / "blobs")
    llm_provider: str = "scripted"
    llm_base_url: str = "https://open.bigmodel.cn/api/paas/v4"
    llm_model: str = "glm-4-plus"
    llm_api_key: Optional[str] = None
    llm_thinking: Optional[str] = None
    llm_timeout: int = 120
    llm_tool_choice: str = "auto"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:5174,http://127.0.0.1:5174"

    model_config = SettingsConfigDict(env_file=str(ROOT_DIR / ".env"), extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()


def get_budget(session: Session) -> Dict[str, int]:
    """读取确定性执行预算配置，缺失项以 DEFAULT_BUDGET 兜底。"""
    from app.infra.db_tables import SystemSettingModel

    row = session.get(SystemSettingModel, "execution_budget")
    stored = row.value_json if (row is not None and isinstance(row.value_json, dict)) else {}
    return {**DEFAULT_BUDGET, **{k: v for k, v in stored.items() if v is not None}}
