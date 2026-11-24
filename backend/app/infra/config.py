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
    "max_llm_concurrency": 6,
    "max_llm_rpm": 60,
    "max_calls_per_script": 8,
    "max_llm_calls": 2400,
    "max_total_tokens": 20000000,
    "max_elapsed_seconds": 7200,
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


def get_llm_config(session: Session, settings: Optional[Settings] = None) -> Dict[str, object]:
    """读取数据库持久化的 LLM 配置并回退至环境变量默认值（读取接口不返回明文密钥）。"""
    from app.infra.db_tables import SystemSettingModel

    base = settings or get_settings()
    row = session.get(SystemSettingModel, "llm_config")
    stored = row.value_json if (row is not None and isinstance(row.value_json, dict)) else {}
    api_key = str(stored.get("llm_api_key") or base.llm_api_key or "")
    return {
        "llm_provider": str(stored.get("llm_provider") or base.llm_provider),
        "llm_base_url": str(stored.get("llm_base_url") or base.llm_base_url),
        "llm_model": str(stored.get("llm_model") or base.llm_model),
        "llm_timeout": int(stored.get("llm_timeout") or base.llm_timeout),
        "has_api_key": bool(api_key),
        "llm_api_key": "",
    }


def get_effective_settings(session: Session, settings: Optional[Settings] = None) -> Settings:
    """结合环境配置与数据库持久化的 LLM 运行配置生成本次执行使用的 Settings 快照。"""
    from app.infra.db_tables import SystemSettingModel

    base = settings or get_settings()
    row = session.get(SystemSettingModel, "llm_config")
    stored = row.value_json if (row is not None and isinstance(row.value_json, dict)) else {}
    if not stored:
        return base
    updates: Dict[str, object] = {}
    for field in ("llm_provider", "llm_base_url", "llm_model", "llm_timeout"):
        if stored.get(field) is not None and stored.get(field) != "":
            updates[field] = stored[field]
    if stored.get("llm_api_key"):
        updates["llm_api_key"] = stored["llm_api_key"]
    return base.model_copy(update=updates)
