"""Exponential-backoff retry helper for the orchestration boundary (Task 14.2).

The pipeline's remote calls -- Bedrock Batch (Extract) and S3 (Archive) -- can
fail transiently. Per the design's Error Handling table those calls are retried
"with exponential backoff up to N attempts"; on persistent failure the item is
recorded and the run continues (Extract) or halts for that batch (Archive).

This module provides one small, dependency-free primitive used at the
*orchestration boundary* (the design explicitly allows "a small retry helper
used at the orchestration boundary" rather than threading retries through every
provider). It is deliberately generic:

* :func:`retry_call` runs a zero-argument callable, retrying on the configured
  exception types with exponentially increasing sleeps
  (``base_delay * factor**attempt``), optionally capped and jittered.
* :func:`with_retry` wraps a callable so every invocation is retried -- used to
  wrap ``llm_generate`` before handing it to the Extractor.

The sleep function is injectable so tests run instantly (no real waiting) and
deterministically (jitter disabled / seeded).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Tuple, Type, TypeVar

T = TypeVar("T")

#: Default exceptions considered transient/retryable. ``Exception`` is broad on
#: purpose: at the orchestration boundary we cannot import every provider's
#: error type, and a genuinely unrecoverable error simply exhausts the (small)
#: attempt budget and is then surfaced to the caller unchanged.
DEFAULT_RETRYABLE: Tuple[Type[BaseException], ...] = (Exception,)


@dataclass(frozen=True)
class RetryPolicy:
    """Configuration for exponential-backoff retries.

    Attributes:
        max_attempts: Total number of tries (>=1). ``3`` means one initial call
            plus two retries.
        base_delay: Seconds to wait before the first retry.
        factor: Multiplier applied to the delay after each failed attempt.
        max_delay: Optional upper bound on any single sleep (``None`` = no cap).
        retryable: Exception types that trigger a retry; others propagate at once.
    """

    max_attempts: int = 3
    base_delay: float = 0.5
    factor: float = 2.0
    max_delay: float | None = 30.0
    retryable: Tuple[Type[BaseException], ...] = DEFAULT_RETRYABLE

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if self.base_delay < 0:
            raise ValueError("base_delay must be >= 0")
        if self.factor < 1:
            raise ValueError("factor must be >= 1")

    def delay_for(self, attempt: int) -> float:
        """Return the backoff delay (seconds) after ``attempt`` failures (1-based)."""
        delay = self.base_delay * (self.factor ** (attempt - 1))
        if self.max_delay is not None:
            delay = min(delay, self.max_delay)
        return delay


def retry_call(
    func: Callable[[], T],
    *,
    policy: RetryPolicy | None = None,
    sleep: Callable[[float], None] = time.sleep,
    on_retry: Callable[[int, BaseException, float], None] | None = None,
) -> T:
    """Call ``func`` with exponential-backoff retries.

    Args:
        func: Zero-argument callable to invoke.
        policy: Retry policy; a default (3 attempts) is used when omitted.
        sleep: Sleep function (injectable; tests pass a no-op for speed).
        on_retry: Optional hook ``(attempt, exc, delay)`` called before each
            backoff sleep -- used by the orchestrator to log/annotate the manifest.

    Returns:
        Whatever ``func`` returns on the first successful attempt.

    Raises:
        The last exception raised by ``func`` once attempts are exhausted, or a
        non-retryable exception immediately.
    """
    active = policy or RetryPolicy()
    last_exc: BaseException | None = None

    for attempt in range(1, active.max_attempts + 1):
        try:
            return func()
        except active.retryable as exc:  # type: ignore[misc]
            last_exc = exc
            if attempt >= active.max_attempts:
                break
            delay = active.delay_for(attempt)
            if on_retry is not None:
                on_retry(attempt, exc, delay)
            if delay > 0:
                sleep(delay)

    assert last_exc is not None  # loop ran at least once
    raise last_exc


def with_retry(
    func: Callable[..., T],
    *,
    policy: RetryPolicy | None = None,
    sleep: Callable[[float], None] = time.sleep,
    on_retry: Callable[[int, BaseException, float], None] | None = None,
) -> Callable[..., T]:
    """Wrap ``func`` so each call is retried per ``policy``.

    Returns a callable with the same signature; each invocation is routed
    through :func:`retry_call`. Used to wrap ``llm_generate`` before passing it
    to the Extractor so Bedrock Batch calls get exponential backoff without the
    Extractor knowing about retries.
    """

    def wrapper(*args: Any, **kwargs: Any) -> T:
        return retry_call(
            lambda: func(*args, **kwargs),
            policy=policy,
            sleep=sleep,
            on_retry=on_retry,
        )

    return wrapper
