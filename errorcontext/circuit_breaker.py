# circuit_breaker.py
import threading
import json
from datetime import datetime, timezone
from enum import Enum
from collections import deque
from typing import Any, Callable, Dict, Optional, Type


class CircuitState(Enum):
    CLOSED    = "closed"
    OPEN      = "open"
    HALF_OPEN = "half_open"
    PROBING   = "probing"   # Stato intermedio: un solo thread sta testando


class CircuitBreakerOpenError(Exception):
    """Sollevata quando il circuito è aperto e rifiuta la chiamata."""
    pass


class CircuitBreaker:
    """Circuit breaker thread-safe con event history e metriche persistenti.

    Design intenzionale:
    - deque(maxlen=window_size): l'event history non cresce mai oltre il
      limite, senza bisogno di pulizia esplicita.
    - RLock rientrante: lo stesso thread può acquisire il lock più volte
      senza deadlock (utile quando _on_success/_on_failure chiamano
      _change_state che acquisisce lo stesso lock).
    - Stato PROBING: risolve il race condition in HALF_OPEN. Quando il
      circuito tenta il reset, passa subito a PROBING dentro il lock,
      un solo thread esegue il probe, gli altri ricevono l'errore come
      se il circuito fosse ancora aperto. Solo dopo il probe il circuito
      torna CLOSED o OPEN.
    - opened_at: timestamp preciso di quando il circuito si è aperto,
      esposto nelle metriche per alerting e SLA.
    - total_rejections: conta le richieste rifiutate quando OPEN,
      metrica distinta da total_failures.
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        expected_exception: Type[Exception] = Exception,
        window_size: int = 100,
        success_threshold: int = 2,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.expected_exception = expected_exception
        self.window_size = window_size
        self.success_threshold = success_threshold

        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time: Optional[datetime] = None
        self.opened_at: Optional[datetime] = None

        self.event_history: deque = deque(maxlen=window_size)
        self._lock = threading.RLock()

        self.metrics: Dict[str, Any] = {
            'total_calls': 0,
            'total_failures': 0,
            'total_successes': 0,
            'total_rejections': 0,
            'state_changes': 0,
            'created_at': datetime.now(timezone.utc).isoformat(),
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _record_event(
        self,
        event_type: str,
        exc: Exception = None,
        details: Dict = None,
    ) -> None:
        with self._lock:
            self.event_history.append({
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'type': event_type,
                'state': self.state.value,
                'exception': str(exc)[:100] if exc else None,
                'thread_id': threading.current_thread().ident,
                'details': details or {},
            })

    def _change_state(self, new_state: CircuitState) -> None:
        with self._lock:
            if self.state == new_state:
                return
            old_state = self.state
            self.state = new_state
            self.metrics['state_changes'] += 1
            if new_state == CircuitState.OPEN:
                self.opened_at = datetime.now(timezone.utc)
            self._record_event(
                'state_change',
                details={'from': old_state.value, 'to': new_state.value},
            )

    def _should_attempt_reset(self) -> bool:
        if self.last_failure_time is None:
            return False
        elapsed = (datetime.now(timezone.utc) - self.last_failure_time).total_seconds()
        return elapsed >= self.recovery_timeout

    def _time_until_retry(self) -> float:
        if self.last_failure_time is None:
            return 0.0
        elapsed = (datetime.now(timezone.utc) - self.last_failure_time).total_seconds()
        return max(0.0, self.recovery_timeout - elapsed)

    def _on_success(self) -> None:
        with self._lock:
            self.metrics['total_successes'] += 1
            self.failure_count = 0

            if self.state in (CircuitState.HALF_OPEN, CircuitState.PROBING):
                self.success_count += 1
                if self.success_count >= self.success_threshold:
                    self._change_state(CircuitState.CLOSED)
                    self.success_count = 0
                    self._record_event('circuit_recovered')
                else:
                    # Non ancora abbastanza successi: torna HALF_OPEN
                    # per permettere al prossimo thread di fare un altro probe
                    self._change_state(CircuitState.HALF_OPEN)

            self._record_event('call_success')

    def _on_failure(self, exc: Exception) -> None:
        with self._lock:
            self.metrics['total_failures'] += 1
            self.failure_count += 1
            self.last_failure_time = datetime.now(timezone.utc)
            self.success_count = 0

            self._record_event('call_failure', exc)

            if self.failure_count >= self.failure_threshold:
                self._change_state(CircuitState.OPEN)
            elif self.state == CircuitState.PROBING:
                # Probe fallito: riapri subito
                self._change_state(CircuitState.OPEN)

    # ------------------------------------------------------------------
    # Interfaccia pubblica
    # ------------------------------------------------------------------

    def call(self, func: Callable, *args, **kwargs) -> Any:
        """Esegue func dentro il circuit breaker.

        Gestione degli stati:
        - CLOSED:    passa direttamente.
        - OPEN:      rifiuta, oppure transita a PROBING se è ora di riprovare.
        - HALF_OPEN: un thread alla volta transita a PROBING ed esegue il probe;
                     gli altri sono rifiutati come se il circuito fosse OPEN.
                     Questo risolve il race condition: il passaggio a PROBING
                     avviene dentro il lock, quindi è atomico.
        - PROBING:   rifiuta (probe già in corso su un altro thread).
        """
        with self._lock:
            self.metrics['total_calls'] += 1

            if self.state == CircuitState.OPEN:
                if self._should_attempt_reset():
                    self._change_state(CircuitState.HALF_OPEN)
                else:
                    self.metrics['total_rejections'] += 1
                    time_until = self._time_until_retry()
                    self._record_event('circuit_rejected')
                    raise CircuitBreakerOpenError(
                        f"Circuit breaker '{self.name}' is OPEN. "
                        f"Retry in {time_until:.1f}s. "
                        f"Opened at {self.opened_at}."
                    )

            if self.state == CircuitState.HALF_OPEN:
                # Questo thread "prenota" il probe passando a PROBING
                # dentro il lock, nessun altro thread passerà qui
                self._change_state(CircuitState.PROBING)

            elif self.state == CircuitState.PROBING:
                # Probe già in corso su un altro thread, rifiuta
                self.metrics['total_rejections'] += 1
                self._record_event('circuit_rejected_probing')
                raise CircuitBreakerOpenError(
                    f"Circuit breaker '{self.name}' is PROBING. "
                    f"Another thread is testing recovery."
                )
        # Lock rilasciato prima di chiamare func (per non tenerlo durante I/O)
        try:
            result = func(*args, **kwargs)
            self._on_success()
            return result
        except self.expected_exception as e:
            self._on_failure(e)
            raise

    def get_metrics(self) -> Dict[str, Any]:
        with self._lock:
            time_in_open = None
            if self.opened_at and self.state == CircuitState.OPEN:
                time_in_open = (
                    datetime.now(timezone.utc) - self.opened_at
                ).total_seconds()

            return {
                'name': self.name,
                'state': self.state.value,
                'failure_count': self.failure_count,
                'success_count': self.success_count,
                'time_in_open_seconds': time_in_open,
                'time_until_retry': (
                    self._time_until_retry()
                    if self.state == CircuitState.OPEN else 0
                ),
                'metrics': self.metrics.copy(),
                'recent_events': list(self.event_history)[-15:],
            }

    def export_metrics_json(self) -> str:
        return json.dumps(self.get_metrics(), default=str, indent=2)

    def reset(self) -> None:
        with self._lock:
            self.state = CircuitState.CLOSED
            self.failure_count = 0
            self.success_count = 0
            self.last_failure_time = None
            self.opened_at = None
            self._record_event('manual_reset')


# ---------------------------------------------------------------------------
# Registry globale: factory con memoization thread-safe
# ---------------------------------------------------------------------------

_circuit_breakers: Dict[str, CircuitBreaker] = {}
_breaker_lock = threading.Lock()


def get_circuit_breaker(
    name: str,
    failure_threshold: int = 5,
    recovery_timeout: float = 60.0,
    expected_exception: Type[Exception] = Exception,
    success_threshold: int = 2,
) -> CircuitBreaker:
    """Factory con memoization thread-safe.

    Se un breaker con lo stesso nome esiste già ma con parametri diversi,
    lancia ValueError invece di ignorare silenziosamente la discrepanza.
    Questo evita il bug classico: due chiamate con lo stesso nome ma
    parametri diversi restituiscono silenziosamente la prima configurazione.
    """
    with _breaker_lock:
        if name in _circuit_breakers:
            existing = _circuit_breakers[name]
            mismatches = []
            if existing.failure_threshold != failure_threshold:
                mismatches.append(
                    f"failure_threshold: existing={existing.failure_threshold}, "
                    f"requested={failure_threshold}"
                )
            if existing.recovery_timeout != recovery_timeout:
                mismatches.append(
                    f"recovery_timeout: existing={existing.recovery_timeout}, "
                    f"requested={recovery_timeout}"
                )
            if mismatches:
                raise ValueError(
                    f"Circuit breaker '{name}' already exists with different "
                    f"configuration: {'; '.join(mismatches)}. "
                    f"Use reset() or choose a different name."
                )
            return existing

        _circuit_breakers[name] = CircuitBreaker(
            name=name,
            failure_threshold=failure_threshold,
            recovery_timeout=recovery_timeout,
            expected_exception=expected_exception,
            success_threshold=success_threshold,
        )
        return _circuit_breakers[name]


def get_all_circuit_breakers() -> Dict[str, CircuitBreaker]:
    with _breaker_lock:
        return _circuit_breakers.copy()
