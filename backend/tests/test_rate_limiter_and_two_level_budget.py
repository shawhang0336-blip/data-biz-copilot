"""RPM 令牌桶限流器、跨线程 429 冷却及父子 Bus 两级预算级联回滚测试。"""

from concurrent.futures import ThreadPoolExecutor
import time

import pytest

from app.infra.llm.client import TokenBucketRateLimiter
from app.infra.llm.guard import call_llm_validated
from app.kernel.run import BudgetExceededError, CancellationToken, RunCancelledError
from app.policy.dc_bus import build_run_bus, build_script_child_bus


def test_rate_limiter_pacing_cooldown_and_cancellation():
    # 120 RPM = 2 tokens/sec, burst capacity = min(120, 6) = 6
    limiter = TokenBucketRateLimiter(rpm=120)
    t0 = time.monotonic()
    for _ in range(8):
        limiter.acquire()
    elapsed = time.monotonic() - t0
    # 前 6 次突发立刻通过，后 2 次需等待约 ~1.0s
    assert elapsed >= 0.75

    # 触发 429 冷却，正在等待的线程应可被 CancellationToken 秒级唤醒中断
    limiter.notify_rate_limited(retry_after=5.0)
    assert limiter.cooldown_remaining() > 3.5
    token = CancellationToken()

    def _cancel_soon():
        time.sleep(0.08)
        token.cancel("用户取消等待")

    with ThreadPoolExecutor(max_workers=2) as pool:
        pool.submit(_cancel_soon)
        with pytest.raises(RunCancelledError):
            limiter.acquire(cancellation=token)


def test_guard_retries_429_and_parent_child_bus_cascade_rollback():
    class _RateLimitedThenOkLlm:
        def __init__(self):
            self.attempts = 0

        def assign(self, tool_name, instruction, payload, feedback=None, timeout=None):
            self.attempts += 1
            if self.attempts <= 2:
                raise RuntimeError("LLM HTTP 429 rate_limit_exceeded: too many requests retry_after=0.05")
            return (
                {
                    "business_domain_hint": "对公信贷",
                    "target_concept_hint": "有效贷款余额",
                    "source_summary": "汇总正常贷款客户净额。",
                    "calculation_kind": "AGGREGATE",
                    "calculation_summary": "按有效余额求和。",
                    "inclusions": [],
                    "exclusions": [],
                    "branches": [],
                    "dedup_grain_hint": "",
                },
                {"usage": {"total_tokens": 30}},
            )

    job_bus = build_run_bus({"max_llm_calls": 10, "max_total_tokens": 100000}, run_id="job-1")
    script_bus = build_script_child_bus(max_calls=3, parent=job_bus, script_tag="script:s1")

    res, trace = call_llm_validated(
        _RateLimitedThenOkLlm(),
        "translate_logic_evidence",
        "测试429退避",
        {"hello": "world"},
        backoff=(0.02, 0.02),
        bus=script_bus,
        transient_attempts=3,
    )
    assert res["target_concept_hint"] == "有效贷款余额"
    assert trace["attempts"] == 3
    # 成功归还后在途占用归零，且父子均记入 3 次调用
    assert script_bus.budget_guard._in_flight_calls == 0
    assert job_bus.budget_guard._in_flight_calls == 0
    assert job_bus.budget_guard.snapshot()["llm_calls"] == 3

    # 下一次在 script_bus 上调用应触发子预算超限，且自动回滚 job_bus 的在途预占计数
    with pytest.raises(BudgetExceededError) as exc_info:
        script_bus.begin_llm_call()
    assert exc_info.value.run_id == "script:s1"
    assert job_bus.budget_guard._in_flight_calls == 0
