# examples/payment_service.py
"""
payment service con threading, circuit breaker e retry.
"""
import logging
import random
import sys
import threading
import time

sys.path.insert(0, '..')

from errorcontext import (
    ErrorContext,
    DistributedErrorLogger,
    CircuitBreakerOpenError,
    get_all_circuit_breakers,
    with_circuit_breaker,
    with_retry,
    NETWORK_RETRY,
    PAYMENT_RETRY,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(threadName)s] %(levelname)s: %(message)s',
)

logger = DistributedErrorLogger(service_name="payment_service", version="1.0.0")


@with_retry(PAYMENT_RETRY)
def validate_amount(amount: float):
    if amount <= 0:
        raise ValueError("Amount must be positive")
    if amount > 10_000:
        raise ValueError("Amount exceeds limit")


@with_circuit_breaker(
    breaker_name="payment_gateway",
    policy=NETWORK_RETRY,
    failure_threshold=3,
    recovery_timeout=5.0,
    expected_exception=ConnectionError,
)
def charge_card(user_id: int, amount: float, gateway: str = "stripe"):
    with ErrorContext("payment_gateway", gateway=gateway, user_id=user_id):
        # Simula un comportamento instabile
        if random.random() < 0.6:
            raise ConnectionError("Gateway timeout")
        return {"txn_id": f"txn_{user_id}_{int(time.time())}", "amount": amount}


@with_circuit_breaker(
    breaker_name="fraud_check",
    failure_threshold=2,
    recovery_timeout=10.0,
)
def fraud_check(user_id: int, amount: float):
    with ErrorContext("fraud_detection", threshold=5_000):
        if amount > 5_000:
            raise RuntimeError("High-value transaction requires review")
        return True


def process_payment(user_id: int, amount: float):
    try:
        validate_amount(amount)
        fraud_check(user_id, amount)
        result = charge_card(user_id, amount)
        logging.info(f"✅ Payment processed: {result}")
        return result
    except CircuitBreakerOpenError as e:
        logging.error(f"❌ Circuit open: {e}")
        raise
    except Exception as e:
        logging.error(logger.pretty_print(e))
        raise


# ---------------------------------------------------------------------------
# Test multithreaded
# ---------------------------------------------------------------------------

def worker(thread_id: int, user_ids: list):
    for user_id in user_ids:
        try:
            process_payment(user_id, 100.0 + thread_id * 10)
            time.sleep(0.05)
        except Exception:
            pass


if __name__ == "__main__":
    print("\n" + "=" * 70)
    print("TEST 1: Multi-threaded payment processing (3 thread x 4 users)")
    print("=" * 70)

    batches = [list(range(1, 5)), list(range(5, 9)), list(range(9, 13))]
    threads = [
        threading.Thread(target=worker, args=(i, batch), name=f"Worker-{i}")
        for i, batch in enumerate(batches)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    print(logger.print_all_circuit_breakers())

    print("\n" + "=" * 70)
    print("TEST 2: Circuit breaker forzato: OPEN -> PROBING -> CLOSED")
    print("=" * 70)

    @with_circuit_breaker(
        breaker_name="test_breaker",
        failure_threshold=2,
        recovery_timeout=2.0,
    )
    def always_fails(op_id: int):
        with ErrorContext("unstable_op", operation_id=op_id):
            raise ConnectionError("Always fails")

    for i in range(5):
        try:
            always_fails(i)
        except CircuitBreakerOpenError as e:
            print(f"❌ Attempt {i}: OPEN - {e}")
        except Exception as e:
            print(f"⚠️  Attempt {i}: {type(e).__name__}: {e}")
        time.sleep(0.3)

    print("\n⏳ Waiting for recovery timeout (2.5s)...")
    time.sleep(2.5)

    @with_circuit_breaker(
        breaker_name="fixed_op",
        failure_threshold=2,
        recovery_timeout=2.0,
    )
    def now_works():
        with ErrorContext("fixed_op"):
            return "✅ Recovery successful!"

    try:
        print(now_works())
    except Exception as e:
        print(f"Still failing: {e}")

    print(logger.print_all_circuit_breakers())
