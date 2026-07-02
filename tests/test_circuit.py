"""Per-backend circuit breaker (leg 04 step 4)."""

from app.config import get_settings
from app.models import Backend
from app.resilience import CircuitBreakerRegistry, CircuitState


def _backend(name="b1"):
    return Backend(name=name, url="http://x", ollama_tag="t")


def test_opens_after_threshold_then_blocks():
    reg = CircuitBreakerRegistry()
    b = _backend()
    threshold = get_settings().circuit_fail_threshold
    for _ in range(threshold):
        assert reg.allow(b)
        reg.record_failure(b)
    # Now open: further requests are blocked until cooldown.
    assert not reg.allow(b)
    assert reg._get(b).state == CircuitState.OPEN


def test_half_open_after_cooldown_then_closes_on_success():
    reg = CircuitBreakerRegistry()
    b = _backend("b2")
    for _ in range(get_settings().circuit_fail_threshold):
        reg.record_failure(b)
    # Force cooldown to have elapsed.
    reg._get(b).opened_at -= get_settings().circuit_cooldown + 1
    assert reg.allow(b)  # admits a half-open probe
    assert reg._get(b).state == CircuitState.HALF_OPEN
    reg.record_success(b)
    assert reg._get(b).state == CircuitState.CLOSED
    assert reg.allow(b)


def test_success_resets_failures():
    reg = CircuitBreakerRegistry()
    b = _backend("b3")
    reg.record_failure(b)
    reg.record_success(b)
    assert reg._get(b).consecutive_failures == 0
