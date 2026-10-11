"""Tests du CircuitBreaker (critère de succès n°4)."""

import pytest

from app.validation.verifier import CircuitBreaker


def _boom():
    raise RuntimeError("fournisseur en panne")


def test_opens_after_threshold_and_short_circuits():
    cb = CircuitBreaker(failure_threshold=3, recovery_timeout=60)

    for _ in range(3):
        with pytest.raises(RuntimeError):
            cb.call(_boom)

    assert cb.state == "open"
    # Une fois ouvert, l'appel échoue immédiatement (pas d'appel réseau).
    with pytest.raises(Exception, match="OPEN"):
        cb.call(_boom)


def test_half_open_recovers_on_success():
    cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0)

    for _ in range(2):
        with pytest.raises(RuntimeError):
            cb.call(_boom)
    assert cb.state == "open"

    # recovery_timeout=0 -> le prochain appel passe en half-open puis closed.
    assert cb.call(lambda: "recovered") == "recovered"
    assert cb.state == "closed"
    assert cb.failure_count == 0


def test_reset_restores_closed_state():
    cb = CircuitBreaker(failure_threshold=1, recovery_timeout=60)
    with pytest.raises(RuntimeError):
        cb.call(_boom)
    assert cb.state == "open"

    cb.reset()
    assert cb.state == "closed"
    assert cb.failure_count == 0
    assert cb.last_failure_time is None


def test_failures_below_threshold_keep_closed():
    cb = CircuitBreaker(failure_threshold=5, recovery_timeout=60)
    with pytest.raises(RuntimeError):
        cb.call(_boom)
    assert cb.state == "closed"
    assert cb.failure_count == 1
