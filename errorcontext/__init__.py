# errorcontext/__init__.py
"""
ErrorContext - The Context tracking, retry e circuit breaker per Python.

Invece di loggare separatamente o usare sistemi esterni, i dati diagnostici vengono monkey-patchati direttamente sull'eccezione.
L'eccezione diventa il suo stesso carrier di contesto.

Uso base:
    from errorcontext import ErrorContext, with_retry, with_circuit_breaker
    from errorcontext import DistributedErrorLogger, NETWORK_RETRY

    logger = DistributedErrorLogger("my_service", "1.0")

    @with_circuit_breaker("my_dep", policy=NETWORK_RETRY)
    def call_external():
        ...

    try:
        call_external()
    except Exception as e:
        print(logger.pretty_print(e))
"""

from .error_context import ErrorContext
from .retry_policy import RetryPolicy, NETWORK_RETRY, DATABASE_RETRY, PAYMENT_RETRY
from .circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerOpenError,
    CircuitState,
    get_circuit_breaker,
    get_all_circuit_breakers,
)
from .decorators import with_retry, with_circuit_breaker
from .logger import DistributedErrorLogger

__all__ = [
    'ErrorContext',
    'RetryPolicy',
    'NETWORK_RETRY',
    'DATABASE_RETRY',
    'PAYMENT_RETRY',
    'CircuitBreaker',
    'CircuitBreakerOpenError',
    'CircuitState',
    'get_circuit_breaker',
    'get_all_circuit_breakers',
    'with_retry',
    'with_circuit_breaker',
    'DistributedErrorLogger',
]
