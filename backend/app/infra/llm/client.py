"""受控 Tool-Call 大模型 HTTP 客户端与并发信号量（基础设施层 Layer 0）。"""

import json
import threading
import time
from typing import Any, Dict, Optional, Tuple

import httpx
from json_repair import repair_json

from app.infra.config import Settings
from app.infra.llm.schemas import TOOL_SCHEMAS, tool_prompt_preamble

_semaphore_lock = threading.Lock()
_llm_semaphore = threading.BoundedSemaphore(4)
_llm_semaphore_limit = 4

_ORIGINAL_HTTPX_POST = httpx.post
_http_client_lock = threading.Lock()
_http_client: Optional[httpx.Client] = None


def _get_http_client() -> httpx.Client:
    global _http_client
    with _http_client_lock:
        if _http_client is None or _http_client.is_closed:
            _http_client = httpx.Client(
                limits=httpx.Limits(max_connections=32, max_keepalive_connections=16)
            )
        return _http_client


def close_http_client() -> None:
    global _http_client
    with _http_client_lock:
        client = _http_client
        _http_client = None
    if client is not None and not client.is_closed:
        client.close()


def configure_llm_concurrency(limit: int) -> None:
    global _llm_semaphore, _llm_semaphore_limit
    limit = max(1, int(limit))
    with _semaphore_lock:
        if limit != _llm_semaphore_limit:
            _llm_semaphore_limit = limit
            _llm_semaphore = threading.BoundedSemaphore(limit)


def llm_call_semaphore() -> threading.BoundedSemaphore:
    return _llm_semaphore


class LlmTimeoutError(RuntimeError):
    """模型调用超时（可安全按瞬时通道重试）。"""


class RealDesignLlm:
    def __init__(self, settings: Settings) -> None:
        if not settings.llm_api_key:
            raise RuntimeError("LLM_API_KEY is required")
        self.settings = settings

    def assign(
        self,
        tool_name: str,
        instruction: str,
        payload: Dict[str, Any],
        feedback: Optional[str] = None,
        timeout: Optional[int] = None,
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        if timeout is None:
            timeout = self.settings.llm_timeout
        if tool_name not in TOOL_SCHEMAS:
            raise RuntimeError(f"Unknown LLM tool: {tool_name}")
        schema = TOOL_SCHEMAS[tool_name]
        prompt = (
            f"{tool_prompt_preamble(tool_name)}\n"
            f"任务：{instruction}\n输入：{json.dumps(payload, ensure_ascii=False)}"
        )
        if feedback:
            prompt += f"\n上次输出未通过确定性门禁或校验，请用纯业务语言重写修正：{feedback}"
        started_at = time.monotonic()
        request_json: Dict[str, Any] = {
            "model": self.settings.llm_model,
            "messages": [
                {
                    "role": "system",
                    "content": "你是只使用纯中文业务语言（严禁出现任何代码、SQL、物理表名或字段英文名）进行结构化工具调用的金融业务架构专家。",
                },
                {"role": "user", "content": prompt},
            ],
            "tools": [
                {
                    "type": "function",
                    "function": {"name": tool_name, "description": instruction, "parameters": schema},
                }
            ],
            "temperature": 0,
            "max_tokens": 8192,
            "tool_choice": (
                {"type": "function", "function": {"name": tool_name}}
                if self.settings.llm_tool_choice == "forced"
                else "auto"
            ),
        }
        if self.settings.llm_thinking:
            request_json["thinking"] = {"type": self.settings.llm_thinking}
        base_url = self.settings.llm_base_url.rstrip("/")
        url = f"{base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.settings.llm_api_key}"}
        try:
            with llm_call_semaphore():
                if httpx.post is _ORIGINAL_HTTPX_POST:
                    response = _get_http_client().post(url, headers=headers, json=request_json, timeout=timeout)
                else:
                    response = httpx.post(url, headers=headers, json=request_json, timeout=timeout)
        except httpx.TimeoutException as exc:
            elapsed = round(time.monotonic() - started_at, 2)
            raise LlmTimeoutError(f"{type(exc).__name__} after {elapsed}s; request_chars={len(prompt)}") from exc
        except httpx.HTTPError as exc:
            elapsed = round(time.monotonic() - started_at, 2)
            raise RuntimeError(f"{type(exc).__name__} after {elapsed}s; request_chars={len(prompt)}") from exc

        if response.is_error:
            elapsed = round(time.monotonic() - started_at, 2)
            try:
                parsed_err = response.json()
                error = parsed_err.get("error", {}) if isinstance(parsed_err, dict) else {}
                code = error.get("code", "")
                msg = error.get("message", "")
            except Exception:  # noqa: BLE001
                code = ""
                raw_text = (getattr(response, "text", "") or "").strip().replace("\n", " ")
                msg = raw_text[:240] or "non-JSON error body"
            raise RuntimeError(f"LLM HTTP {response.status_code} {code}: {msg}; elapsed={elapsed}s")

        try:
            body = response.json()
        except Exception as exc:  # noqa: BLE001
            elapsed = round(time.monotonic() - started_at, 2)
            raise RuntimeError(f"LLM HTTP 502 invalid JSON response: {exc}; elapsed={elapsed}s") from exc

        if not isinstance(body, dict):
            elapsed = round(time.monotonic() - started_at, 2)
            raise RuntimeError(f"LLM HTTP 502 non-object JSON response; elapsed={elapsed}s")

        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            elapsed = round(time.monotonic() - started_at, 2)
            err = body.get("error") if isinstance(body.get("error"), dict) else {}
            if err:
                raise RuntimeError(
                    f"LLM HTTP 502 upstream error {err.get('code', '')}: {err.get('message', '')}; elapsed={elapsed}s"
                )
            raise RuntimeError(f"LLM HTTP 502 empty choices in response; elapsed={elapsed}s")

        choice = choices[0] if isinstance(choices[0], dict) else {}
        message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
        calls = message.get("tool_calls") or []
        if not calls:
            diagnostic = {
                "request_id": body.get("id"),
                "finish_reason": choice.get("finish_reason"),
                "content_chars": len(message.get("content") or ""),
                "reasoning_chars": len(message.get("reasoning_content") or ""),
                "request_chars": len(prompt),
                "usage": body.get("usage"),
                "elapsed_seconds": round(time.monotonic() - started_at, 2),
            }
            raise RuntimeError(f"Model returned no tool call: {json.dumps(diagnostic, ensure_ascii=False)}")

        arguments: Dict[str, Any] = {}
        json_repaired = False
        for call in calls:
            fn_obj = call.get("function") if isinstance(call, dict) else None
            if not isinstance(fn_obj, dict) or fn_obj.get("name") != tool_name:
                raise RuntimeError("Model returned an unexpected tool")
            raw_arguments = fn_obj.get("arguments")
            if isinstance(raw_arguments, dict):
                current = raw_arguments
            elif isinstance(raw_arguments, str):
                try:
                    current = json.loads(raw_arguments)
                except json.JSONDecodeError:
                    current = repair_json(raw_arguments, return_objects=True)
                    json_repaired = True
            else:
                raise ValueError(f"工具 {tool_name} 返回的参数不是有效 JSON 字符串或对象")
            if not isinstance(current, dict):
                raise ValueError(f"工具 {tool_name} 返回的参数无法解析为 JSON 对象")
            for key, value in current.items():
                if isinstance(value, list):
                    arguments.setdefault(key, []).extend(value)
                else:
                    arguments[key] = value

        trace = {
            "provider": self.settings.llm_provider,
            "model": self.settings.llm_model,
            "tool_name": tool_name,
            "elapsed_seconds": round(time.monotonic() - started_at, 2),
            "usage": body.get("usage"),
            "json_repaired": json_repaired,
            "arguments": arguments,
        }
        return arguments, trace
