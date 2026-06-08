# decorators.py
import inspect
import time
from functools import wraps
from typing import Callable, Optional, Type

from .error_context import ErrorContext
from .retry_policy import RetryPolicy
from .circuit_breaker import CircuitBreaker, CircuitBreakerOpenError, get_circuit_breaker


def _capture_call_context(func: Callable, args, kwargs) -> dict:
    """Cattura i parametri della chiamata ispezionando la signature.

    Follia intenzionale: usa inspect per costruire automaticamente il
    contesto diagnostico dai parametri della funzione, senza che il
    chiamante debba passare nulla esplicitamente.
    Esclude callable e tipi per evitare di serializzare oggetti pesanti.
    """
    try:
        sig = inspect.signature(func)
        bound = sig.bind(*args, **kwargs)
        bound.apply_defaults()
        return {
            k: v for k, v in bound.arguments.items()
            if not callable(v) and not isinstance(v, type)
        }
    except (TypeError, ValueError):
        return {}


def with_retry(policy: RetryPolicy = None) -> Callable:
    """Decorator di solo retry, senza circuit breaker.

    Inietta automaticamente i parametri della funzione nel contesto
    dell'eccezione ad ogni tentativo. Il numero del tentativo e il delay
    vengono anch'essi monkey-patchati sull'eccezione come _retry_info.

    Separato da with_circuit_breaker per permettere composizione
    esplicita: @with_retry + @with_circuit_breaker oppure solo uno dei due.
    """
    if policy is None:
        policy = RetryPolicy()

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            ctx_data = _capture_call_context(func, args, kwargs)
            last_exc = None

            for attempt in range(policy.max_attempts):
                try:
                    with ErrorContext(
                        f"{func.__module__}.{func.__name__}",
                        attempt=attempt,
                        **ctx_data,
                    ):
                        return func(*args, **kwargs)

                except Exception as e:
                    last_exc = e

                    if not policy.should_retry(e, attempt):
                        raise

                    delay = policy.get_delay(attempt)

                    # Monkey-patch del retry info sull'eccezione stessa
                    if not hasattr(e, '_retry_info'):
                        e._retry_info = []
                    e._retry_info.append({
                        'attempt': attempt,
                        'delay': round(delay, 3),
                        'reason': str(e),
                    })

                    time.sleep(delay)

            raise last_exc

        return wrapper
    return decorator


def with_circuit_breaker(
    breaker_name: str,
    policy: RetryPolicy = None,
    failure_threshold: int = 5,
    recovery_timeout: float = 60.0,
    expected_exception: Type[Exception] = Exception,
    success_threshold: int = 2,
) -> Callable:
    """Decorator che combina circuit breaker e retry.

    Separazione di responsabilità rispetto alle versioni precedenti:
    - Il circuit breaker è ottenuto/creato una volta sola al momento della
      decorazione (non a ogni chiamata), tramite il registry globale.
    - Il retry loop conosce lo stato del breaker e lo registra nel
      _retry_info per ogni tentativo.
    - CircuitBreakerOpenError non viene mai ritentata: fail-fast immediato.

    Il contesto ErrorContext avvolge il tentativo, non la chiamata al
    circuit breaker. L'ErrorContext descrive "questo tentativo di questa funzione", 
    il circuit breaker descrive "lo stato della dipendenza esterna".
    """
    if policy is None:
        policy = RetryPolicy()

    # Il breaker viene risolto qui, al momento della decorazione.
    # Questo significa che la configurazione viene validata subito,
    # non alla prima chiamata.
    breaker = get_circuit_breaker(
        name=breaker_name,
        failure_threshold=failure_threshold,
        recovery_timeout=recovery_timeout,
        expected_exception=expected_exception,
        success_threshold=success_threshold,
    )

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            ctx_data = _capture_call_context(func, args, kwargs)
            last_exc = None

            for attempt in range(policy.max_attempts):
                try:
                    with ErrorContext(
                        f"{func.__module__}.{func.__name__}",
                        attempt=attempt,
                        breaker=breaker_name,
                        breaker_state=breaker.state.value,
                        **ctx_data,
                    ):
                        return breaker.call(func, *args, **kwargs)

                except CircuitBreakerOpenError:
                    # Il circuito è aperto: non ha senso ritentare,
                    # la dipendenza è considerata down. Fail-fast.
                    raise

                except Exception as e:
                    last_exc = e

                    if not policy.should_retry(e, attempt):
                        raise

                    delay = policy.get_delay(attempt)

                    if not hasattr(e, '_retry_info'):
                        e._retry_info = []
                    e._retry_info.append({
                        'attempt': attempt,
                        'delay': round(delay, 3),
                        'reason': str(e),
                        'breaker_state': breaker.state.value,
                    })

                    time.sleep(delay)

            raise last_exc

        return wrapper
    return decorator
