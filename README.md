# ErrorContext

> Python stack traces tell you **where** things broke. `errorcontext` tells you **why**, with no external dependencies. No separate logging setup. No configuration files. The exception carries its own context. Always. Enjoy!


`errorcontext` let the exception accumulate its own context as it unwinds. The call stack *is* the trail. The exception *is* the log entry and no coordination is required. Obviously is a conceptual experiment in exception‑driven observability, it’s fully functional, but its main purpose is to explore what happens when the exception becomes the log.

**Magic:** It's only ~300 lines of code


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

---

## The problem

You're on call at 2am.... Your monitoring shows a `RuntimeError` in production.... The stack trace says it happened in `charge_card()`. Great. But:

- What was the `user_id`?
- Was this the first attempt or the third?
- Was the circuit breaker already degraded when this hit?
- Which thread was handling this request?
- Who am I?
- Where is th coffee?

Standard Python gives you none of that and you add `print()` statements, redeploy, wait for it to happen again. It's 3 a.m. and the coffee is over...


`errorcontext` solves the problem! It **monkey-patches the exception itself**. Every context manager, every retry attempt, every circuit breaker state transition gets attached directly to the exception object as it propagates up the call stack. By the time you catch it, the exception is a fully annotated incident report.
