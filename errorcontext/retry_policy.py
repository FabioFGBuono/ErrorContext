# retry_policy.py
import random
import time
from typing import Callable, Optional, Tuple, Type


class RetryPolicy:
    """Policy di retry con backoff esponenziale, jitter e cap sul delay.

    Il jitter viene applicato DOPO il cap (min(delay, max_delay)) in modo
    che sia sempre efficace anche agli attempt alti, dove il delay grezzo
    supererebbe già max_delay. Se il jitter fosse applicato prima del cap,
    agli attempt alti restituirebbe sempre max_delay e perderebbe il suo
    scopo di distribuire il carico.
    """

    def __init__(
        self,
        max_attempts: int = 3,
        base_delay: float = 1.0,
        exponential: bool = True,
        jitter: bool = True,
        retryable_exceptions: Tuple[Type[Exception], ...] = (Exception,),
        should_retry_fn: Optional[Callable[[Exception, int], bool]] = None,
        max_delay: float = 300.0,
    ):
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.exponential = exponential
        self.jitter = jitter
        self.retryable_exceptions = retryable_exceptions
        self.should_retry_fn = should_retry_fn
        self.max_delay = max_delay

    def get_delay(self, attempt: int) -> float:
        """Calcola delay con backoff, poi jitter applicato dopo il cap."""
        if self.exponential:
            delay = self.base_delay * (2 ** attempt)
        else:
            delay = self.base_delay * (attempt + 1)

        # Cap prima, jitter dopo, così il jitter è sempre attivo
        delay = min(delay, self.max_delay)

        if self.jitter:
            delay *= (0.5 + random.random())  # range: [0.5x, 1.5x]

        return delay

    def should_retry(self, exc: Exception, attempt: int) -> bool:
        """Decide se ritentare in base all'eccezione e al numero di tentativo."""
        if not isinstance(exc, self.retryable_exceptions):
            return False
        if attempt >= self.max_attempts - 1:
            return False
        if self.should_retry_fn:
            return self.should_retry_fn(exc, attempt)
        return True


# ---------------------------------------------------------------------------
# Preset per contesti comuni
# ---------------------------------------------------------------------------

NETWORK_RETRY = RetryPolicy(
    max_attempts=4,
    base_delay=0.5,
    exponential=True,
    jitter=True,
    retryable_exceptions=(ConnectionError, TimeoutError, OSError),
    max_delay=30.0,
)

DATABASE_RETRY = RetryPolicy(
    max_attempts=3,
    base_delay=0.1,
    exponential=False,   # Linear per DB
    jitter=False,
    retryable_exceptions=(Exception,),
    max_delay=10.0,
)

PAYMENT_RETRY = RetryPolicy(
    max_attempts=2,
    base_delay=2.0,
    exponential=False,
    jitter=True,
    retryable_exceptions=(TimeoutError, ConnectionError),
    max_delay=15.0,
)
