# ⚠️ ErrorContext

![Status](https://img.shields.io/badge/Status-Experimenting-orange)
![Focus](https://img.shields.io/badge/Focus-Learning-blue)
![Warning](https://img.shields.io/badge/Warning-Here_Be_Dragons-yellow)
![Not_For_Production](https://img.shields.io/badge/Not_for_Production-red)

> Python stack traces tell you **where** things broke. `errorcontext` tells you **why**, with no external dependencies. No separate logging setup. No configuration files. The exception carries its own context. Always. Enjoy!


`errorcontext` let the exception accumulate its own context as it unwinds. The call stack *is* the trail. The exception *is* the log entry and no coordination is required. Obviously is a conceptual experiment in exception‑driven observability, it’s fully functional, but its main purpose is to explore what happens when the exception becomes the log.

**Magic:** It's only ~300 lines of code

---

## The problem ☕

You're on call at 2am.... Your monitoring shows a `RuntimeError` in production.... The stack trace says it happened in `charge_card()`. Great. But:

- What was the `user_id`?
- Was this the first attempt or the third?
- Was the circuit breaker already degraded when this hit?
- Which thread was handling this request?
- Who am I?
- Where is th coffee?

Standard Python gives you none of that and you add `print()` statements, redeploy, wait for it to happen again. It's 3 a.m. and the coffee is over...


`errorcontext` solves the problem! It **monkey-patches the exception itself**. Every context manager, every retry attempt, every circuit breaker state transition gets attached directly to the exception object as it propagates up the call stack. By the time you catch it, the exception is a fully annotated incident report.


## Sensitive data

`_sanitize()` automatically redacts fields whose name contains any of `password`, `token`, `secret`, `key`, `api_key`, `apikey`, `auth`, `credential`, `passphrase`. Matching is done on word tokens (split by `_`), not substrings, so `sort_key`, `cache_key`, `foreign_key` are redacted correctly, but `monkey`, `hockey`, `turkey` are not. Non-serializable values are `repr()`'d and truncated at 100 characters. It’s elementary in its current state, but it’s easy to make it more complete.


### Easy example: Just wrap stuff

```python
from errorcontext import ErrorContext

def charge_card(user_id: int, amount: float):
    with ErrorContext("payment_gateway", gateway="stripe", user_id=user_id):
        # If anything raises here, user_id and gateway are attached to
        # the exception automatically. No try/except needed.
        result = stripe.charge(user_id, amount)
        return result
```

When it fails, anywhere up the call stack:

```python
try:
    charge_card(999, 50.0)
except Exception as e:
    print(e._context_trail)
    # [{'label': 'payment_gateway', 'data': {'gateway': 'stripe', 'user_id': 989}, ...}]
```

And the exception here *is* the log entry itself.


```
🔴 RuntimeError: User not found in gateway

📍 Context Trail (3 levels):
 └─ payment_service.process_payment @ T140234 (2026-06-08T14:32:15)
   • user_id = 999
   • amount = 50.0
   └─ payment_service.charge_card @ T140234 (2026-06-08T14:32:15)
      • user_id = 999
      • amount = 50.0
      • attempt = 2
      • breaker_state = half_open
      └─ payment_gateway @ T140234 (2026-06-08T14:32:16)
         • gateway = stripe
         • user_id = 989
```


### Production logging

`DistributedErrorLogger` reads everything off the exception and formats as human-readable for your terminal, as JSON for your log aggregator, and ECS for Elastic.

```python
from errorcontext import DistributedErrorLogger

logger = DistributedErrorLogger(service_name="payment-service", version="2.1.0")

try:
    process_payment(user_id=989, amount=59.0)
except Exception as e:

    # Terminal: for you at 2am
    print(logger.pretty_print(e))

    # JSON: for CloudWatch, Datadog, whatever
    log_line = logger.log_to_json(e, user_context={"ip": "10.0.0.1", "session": "Zuppa_di_drago"})

    # ECS: for Elastic stack
    logger.log_structured(e, user_context={"user_agent": "mobile/3.2"})

    # Remote: fire at your log endpoint
    logger.log_to_remote(e, "https://logs.moreCoffee.AnotherCoffee/")
```


### Multithreaded systems

Everything is thread-safe by construction and each thread has its own isolated context stack via `threading.local`. Exceptions from different threads never mix their trails.

```python
import threading
from errorcontext import ErrorContext, DistributedErrorLogger

logger = DistributedErrorLogger("worker-pool", "1.0")

def worker(task_id: int):
    with ErrorContext("task", task_id=task_id, worker=threading.current_thread().name):
        # Each thread's context is completely isolated.
        # If this raises, only this thread's context is in the trail.
        process_task(task_id)

threads = [threading.Thread(target=worker, args=(i,)) for i in range(50)]
for t in threads: t.start()
for t in threads: t.join()
```

The `thread_id` and `thread_name` are stamped on every trail entry and on the exception itself, so you can filter your logs by thread when debugging concurrency issues.

---

## How it actually works

The central trick is that `ErrorContext.__exit__` runs as the exception propagates *up* through each `with` block.

```
raise RuntimeError("boom")             ← exception created, no trail yet
  └─ __exit__ of "payment_gateway"     ← inserts {'label': 'payment_gateway', ...}
    └─ __exit__ of "charge_card"       ← inserts {'label': 'charge_card', ...} at index 0
      └─ __exit__ of "process_payment" ← inserts at index 0 again
        └─ your except block           ← trail is now in chronological order
```

Each `__exit__` prepends to `_context_trail` with `insert(0, ...)`, but by the time you catch the exception, the trail reads top-to-bottom from outermost to innermost context. The exception object is both the error *and* the incident report.

**No side channels. No global state. No external systems needed**
**Just the exception, carrying everything with it**
**Just ErrorContext**

---


## Circuit breaker metrics

```python
from errorcontext import get_circuit_breaker, get_all_circuit_breakers

# Single breaker
cb = get_circuit_breaker("stripe_api")
print(cb.export_metrics_json())

# All breakers, useful for a /health endpoint
for name, cb in get_all_circuit_breakers().items():
    metrics = cb.get_metrics()
    print(f"{name}: {metrics['state']} ({metrics['metrics']['total_calls']} calls)")

# Manual reset, when you've fixed the dependency and don't want to wait
cb.reset()
```

Work in progress... 
