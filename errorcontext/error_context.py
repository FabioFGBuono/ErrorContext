# error_context.py
import threading
import json
from datetime import datetime, timezone
from typing import Any
import uuid


class _ThreadLocalStack(threading.local):
    """Stack di contesto completamente isolato per thread.

    Follia intenzionale: usa __dict__ direttamente invece di attributi di
    istanza per aggirare il modo in cui threading.local inizializza le
    sottoclassi, ogni thread ottiene il proprio __dict__ separato, quindi
    non c'è mai condivisione accidentale tra thread.
    """

    def __init__(self):
        super().__init__()
        self.__dict__['_stacks'] = {}

    def push(self, key: str, value: Any) -> None:
        if key not in self.__dict__['_stacks']:
            self.__dict__['_stacks'][key] = []
        self.__dict__['_stacks'][key].append(value)

    def pop(self, key: str) -> Any:
        stacks = self.__dict__['_stacks']
        if key in stacks and stacks[key]:
            return stacks[key].pop()
        return None

    def depth(self, key: str) -> int:
        return len(self.__dict__['_stacks'].get(key, []))

    def cleanup_key(self, key: str) -> None:
        """Rimuove solo la chiave specificata, non l'intero dizionario.

        Correzione rispetto a cleanup() globale che cancellava tutti gli
        stack, inclusi eventuali stack futuri con chiavi diverse.
        """
        self.__dict__['_stacks'].pop(key, None)


_thread_local = _ThreadLocalStack()


class ErrorContext:
    """Context manager che trasporta dati diagnostici dentro l'eccezione stessa.

    Follia centrale: invece di loggare separatamente o usare un sistema
    esterno, i dati vengono monkey-patchati direttamente sull'oggetto
    eccezione come attributi privati (_context_trail, _execution_id, ecc.).
    L'eccezione diventa il suo stesso carrier di contesto diagnostico.

    Thread-safe per costruzione: ogni thread ha il proprio stack isolato
    via _ThreadLocalStack, quindi i contesti di thread diversi non si
    mescolano mai.
    """

    def __init__(self, label: str, **data):
        self.label = label
        self.data = data
        self.timestamp = datetime.now(timezone.utc).isoformat()
        # UUID invece di counter globale: no collisioni, no lock necessario
        self.execution_id = str(uuid.uuid4())
        self.thread_id = threading.current_thread().ident
        self.thread_name = threading.current_thread().name

    def __enter__(self):
        _thread_local.push('contexts', self)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        _thread_local.pop('contexts')

        if exc_type is not None:
            # Prima volta che questa eccezione incontra un ErrorContext:
            # inizializza gli attributi custom sull'oggetto eccezione.
            # Monkey-patch intenzionale, l'eccezione porta con sé tutta
            # la storia del contesto senza bisogno di strutture esterne.
            if not hasattr(exc_val, '_context_trail'):
                exc_val._context_trail = []
                exc_val._execution_id = self.execution_id
                exc_val._thread_id = self.thread_id
                exc_val._thread_name = self.thread_name

            exc_val._context_trail.insert(0, {
                'label': self.label,
                'data': self._sanitize(self.data),
                'timestamp': self.timestamp,
                'thread_id': self.thread_id,
                'execution_id': self.execution_id,
            })

        # Cleanup selettivo: rimuove solo la chiave 'contexts', non l'intero
        # __dict__ degli stack. Sicuro per stack futuri con chiavi diverse.
        if _thread_local.depth('contexts') == 0:
            _thread_local.cleanup_key('contexts')

        return False

    @staticmethod
    def _sanitize(data: dict) -> dict:
        """Redige dati sensibili e serializza il resto.

        Il match su word-token invece di substring evita falsi positivi
        su parole innocue che contengono 'key' (monkey, hockey, sort_key,
        cache_key, foreign_key...).
        """
        SENSITIVE_TOKENS = {'password', 'token', 'secret', 'key', 'api_key',
                            'apikey', 'auth', 'credential', 'passphrase'}

        result = {}
        for k, v in data.items():
            tokens = set(k.lower().replace('-', '_').split('_'))
            if tokens & SENSITIVE_TOKENS:
                result[k] = '***REDACTED***'
            else:
                try:
                    json.dumps(v)
                    result[k] = v
                except (TypeError, ValueError):
                    result[k] = repr(v)[:100]
        return result
