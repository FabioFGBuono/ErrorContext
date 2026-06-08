# logger.py
import json
import logging
import sys
import threading
import traceback
from datetime import datetime, timezone
from typing import Optional

from .circuit_breaker import get_all_circuit_breakers


class DistributedErrorLogger:
    """Serializza e formatta le eccezioni arricchite da ErrorContext.

    Legge gli attributi monkey-patchati sull'eccezione (_context_trail,
    _retry_info, _execution_id, _thread_id, _thread_name) e li presenta
    in tre formati:
    - pretty_print: output umano per debug locale, con emoji e indentazione
    - log_to_json: JSON strutturato per ELK/Datadog/CloudWatch
    - log_structured: formato ECS (Elastic Common Schema) per integrazione
      diretta con stack Elastic

    Il logger non dipende da niente, tutti i dati sono già
    dentro l'eccezione per costruzione.
    """

    def __init__(self, service_name: str, version: str = "1.0"):
        self.service_name = service_name
        self.version = version
        self.logger = logging.getLogger(f"distributed_errors.{service_name}")

    def serialize(
        self,
        exc: Exception,
        user_context: Optional[dict] = None,
    ) -> dict:
        """Serializza l'eccezione completa in un dizionario strutturato."""
        trail = getattr(exc, '_context_trail', [])
        retry_info = getattr(exc, '_retry_info', [])
        execution_id = getattr(exc, '_execution_id', None)
        thread_id = getattr(exc, '_thread_id', None)
        thread_name = getattr(exc, '_thread_name', None)

        return {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'execution_id': execution_id or str(id(exc)),
            'thread': {
                'id': thread_id or threading.current_thread().ident,
                'name': thread_name or threading.current_thread().name,
            },
            'service': self.service_name,
            'version': self.version,
            'error': {
                'type': type(exc).__name__,
                'message': str(exc),
                'traceback': traceback.format_exc(),
            },
            'context_trail': trail,
            'retry_info': retry_info,
            'user_context': user_context or {},
            'python_version': sys.version,
        }

    def log_to_json(
        self,
        exc: Exception,
        user_context: Optional[dict] = None,
    ) -> str:
        """Serializza in JSON per stdout/file/remote logging."""
        data = self.serialize(exc, user_context)
        return json.dumps(data, default=str, indent=2)

    def log_structured(
        self,
        exc: Exception,
        user_context: Optional[dict] = None,
    ) -> None:
        """Log strutturato in formato ECS (Elastic Common Schema)."""
        data = self.serialize(exc, user_context)
        ecs_log = {
            '@timestamp': data['timestamp'],
            'service.name': data['service'],
            'service.version': data['version'],
            'error.message': data['error']['message'],
            'error.type': data['error']['type'],
            'error.stack_trace': data['error']['traceback'],
            'labels': {
                'execution_id': data['execution_id'],
                'trace_length': len(data['context_trail']),
                'retry_count': len(data['retry_info']),
            },
        }
        self.logger.error(json.dumps(ecs_log, default=str))

    def log_to_remote(
        self,
        exc: Exception,
        remote_url: str,
        user_context: Optional[dict] = None,
        timeout: float = 5.0,
    ) -> None:
        """Invia il log a un endpoint remoto (ELK, Datadog, ecc.).

        Sincrono: in produzione considera una queue
        o un thread separato per non bloccare l'handler dell'eccezione.
        """
        import urllib.request
        data = self.serialize(exc, user_context)
        try:
            req = urllib.request.Request(
                remote_url,
                data=json.dumps(data, default=str).encode('utf-8'),
                headers={'Content-Type': 'application/json'},
            )
            urllib.request.urlopen(req, timeout=timeout)
        except Exception as send_exc:
            self.logger.error(
                f"Failed to send error to remote ({remote_url}): {send_exc}"
            )

    def pretty_print(self, exc: Exception) -> str:
        """Output umano per debug locale con emoji e indentazione ad albero."""
        trail = getattr(exc, '_context_trail', [])
        retry_info = getattr(exc, '_retry_info', [])
        thread_id = getattr(exc, '_thread_id', None)
        thread_name = getattr(exc, '_thread_name', None)

        sep = '=' * 70
        output = [
            f"\n{sep}",
            f"🔴 {type(exc).__name__}: {exc}",
            f"📊 Service: {self.service_name} v{self.version}",
            f"⏱️  {datetime.now(timezone.utc).isoformat()}",
            f"🧵 Thread: {thread_name} (ID: {thread_id})",
        ]

        if retry_info:
            output.append(f"\n🔄 Retry History ({len(retry_info)} attempts):")
            for info in retry_info:
                breaker_state = info.get('breaker_state', 'n/a')
                output.append(
                    f"  Attempt {info['attempt']}: "
                    f"waited {info['delay']:.3f}s | "
                    f"breaker: {breaker_state} | "
                    f"{str(info['reason'])[:50]}"
                )

        if trail:
            output.append(f"\n📍 Context Trail ({len(trail)} levels):")
            for i, ctx in enumerate(trail):
                indent = "  " * i
                output.append(
                    f"{indent}└─ {ctx['label']} "
                    f"@ T{ctx['thread_id']} "
                    f"({ctx['timestamp']})"
                )
                for k, v in ctx['data'].items():
                    output.append(f"{indent}   • {k} = {repr(v)[:60]}")

        output.append(sep + "\n")
        return "\n".join(output)

    def print_all_circuit_breakers(self) -> str:
        """Stampa lo stato di tutti i circuit breaker registrati."""
        breakers = get_all_circuit_breakers()
        if not breakers:
            return "No circuit breakers registered."

        sep = '-' * 50
        output = [f"\n📡 Circuit Breakers ({len(breakers)} registered):"]
        for name, breaker in breakers.items():
            m = breaker.get_metrics()
            state_icon = {
                'closed': '🟢', 'open': '🔴', 'half_open': '🟡', 'probing': '🔵'
            }.get(m['state'], '⚪')

            output.append(f"\n{sep}")
            output.append(f"{state_icon} {name} - {m['state'].upper()}")
            output.append(
                f"   Calls: {m['metrics']['total_calls']} | "
                f"OK: {m['metrics']['total_successes']} | "
                f"Fail: {m['metrics']['total_failures']} | "
                f"Rejected: {m['metrics']['total_rejections']}"
            )
            if m['time_in_open_seconds'] is not None:
                output.append(
                    f"   Open for: {m['time_in_open_seconds']:.1f}s | "
                    f"Retry in: {m['time_until_retry']:.1f}s"
                )
            output.append(f"   State changes: {m['metrics']['state_changes']}")

        output.append(sep)
        return "\n".join(output)
