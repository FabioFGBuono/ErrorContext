# tests/test_circuit_breaker.py
import threading
import time
import unittest
import uuid
import sys
sys.path.insert(0, '..')

from errorcontext import CircuitBreaker, CircuitBreakerOpenError, CircuitState, get_circuit_breaker


def make_breaker(**kwargs):
    defaults = dict(
        name=f"test_{uuid.uuid4().hex[:8]}",
        failure_threshold=3,
        recovery_timeout=1.0,
        expected_exception=ConnectionError,
    )
    defaults.update(kwargs)
    return CircuitBreaker(**defaults)


def fail(): raise ConnectionError("fail")
def ok():   return "ok"


class TestCircuitBreaker(unittest.TestCase):

    def test_starts_closed(self):
        cb = make_breaker()
        self.assertEqual(cb.state, CircuitState.CLOSED)

    def test_opens_after_threshold(self):
        cb = make_breaker(failure_threshold=3)
        for _ in range(3):
            with self.assertRaises(ConnectionError):
                cb.call(fail)
        self.assertEqual(cb.state, CircuitState.OPEN)

    def test_rejects_when_open(self):
        cb = make_breaker(failure_threshold=2)
        for _ in range(2):
            with self.assertRaises(ConnectionError):
                cb.call(fail)
        with self.assertRaises(CircuitBreakerOpenError):
            cb.call(fail)

    def test_recovers_after_timeout(self):
        cb = make_breaker(failure_threshold=2, recovery_timeout=0.2, success_threshold=2)
        for _ in range(2):
            with self.assertRaises(ConnectionError):
                cb.call(fail)
        self.assertEqual(cb.state, CircuitState.OPEN)

        time.sleep(0.3)
        cb.call(ok)   # probe → PROBING → success
        cb.call(ok)   # second success → CLOSED
        self.assertEqual(cb.state, CircuitState.CLOSED)

    def test_half_open_probe_race(self):
        """Solo un thread alla volta deve eseguire il probe."""
        cb = make_breaker(failure_threshold=2, recovery_timeout=0.1, success_threshold=1)
        for _ in range(2):
            with self.assertRaises(ConnectionError):
                cb.call(fail)

        time.sleep(0.15)

        probe_results = []
        lock = threading.Lock()

        def slow_ok():
            time.sleep(0.05)
            return "ok"

        def attempt():
            try:
                result = cb.call(slow_ok)
                with lock: probe_results.append(('ok', result))
            except CircuitBreakerOpenError:
                with lock: probe_results.append(('rejected', None))
            except Exception as e:
                with lock: probe_results.append(('error', str(e)))

        threads = [threading.Thread(target=attempt) for _ in range(5)]
        for t in threads: t.start()
        for t in threads: t.join()

        probes = [r for r in probe_results if r[0] == 'ok']
        self.assertEqual(len(probes), 1,
            f"Expected exactly 1 probe, got {len(probes)}: {probe_results}")

    def test_factory_raises_on_config_mismatch(self):
        name = f"unique_{uuid.uuid4().hex}"
        get_circuit_breaker(name, failure_threshold=3)
        with self.assertRaises(ValueError) as ctx:
            get_circuit_breaker(name, failure_threshold=10)
        self.assertIn("already exists with different configuration", str(ctx.exception))

    def test_manual_reset(self):
        cb = make_breaker(failure_threshold=2)
        for _ in range(2):
            with self.assertRaises(ConnectionError):
                cb.call(fail)
        self.assertEqual(cb.state, CircuitState.OPEN)
        cb.reset()
        self.assertEqual(cb.state, CircuitState.CLOSED)
        self.assertEqual(cb.failure_count, 0)

    def test_metrics_track_calls(self):
        cb = make_breaker(failure_threshold=10)
        cb.call(ok)
        cb.call(ok)
        with self.assertRaises(ConnectionError):
            cb.call(fail)
        m = cb.get_metrics()
        self.assertEqual(m['metrics']['total_calls'], 3)
        self.assertEqual(m['metrics']['total_successes'], 2)
        self.assertEqual(m['metrics']['total_failures'], 1)


if __name__ == '__main__':
    unittest.main()
