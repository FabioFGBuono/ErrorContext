# tests/test_error_context.py
import re
import threading
import unittest
import sys
sys.path.insert(0, '..')

from errorcontext import ErrorContext


class TestErrorContext(unittest.TestCase):

    def test_trail_built_on_exception(self):
        try:
            with ErrorContext("outer", x=1):
                with ErrorContext("inner", y=2):
                    raise RuntimeError("boom")
        except RuntimeError as e:
            self.assertTrue(hasattr(e, '_context_trail'))
            self.assertEqual(len(e._context_trail), 2)
            self.assertEqual(e._context_trail[0]['label'], "outer")
            self.assertEqual(e._context_trail[1]['label'], "inner")

    def test_no_trail_on_success(self):
        # Must not raise anything
        with ErrorContext("outer", x=1):
            pass

    def test_sanitize_redacts_sensitive_keys(self):
        try:
            with ErrorContext("ctx", password="s3cr3t", user_id=42):
                raise RuntimeError("boom")
        except RuntimeError as e:
            data = e._context_trail[0]['data']
            self.assertEqual(data['password'], '***REDACTED***')
            self.assertEqual(data['user_id'], 42)

    def test_sanitize_no_false_positives_on_substring(self):
        """'monkey' contains 'key' as substring but not as token... must not redact."""
        try:
            with ErrorContext("ctx", sort_key="asc", monkey="business"):
                raise RuntimeError("boom")
        except RuntimeError as e:
            data = e._context_trail[0]['data']
            self.assertEqual(data['sort_key'], '***REDACTED***')   # token match
            self.assertEqual(data['monkey'], 'business')           # substring only

    def test_thread_isolation(self):
        errors = {}

        def worker(name, value):
            try:
                with ErrorContext("ctx", name=name, value=value):
                    raise RuntimeError(f"fail-{name}")
            except RuntimeError as e:
                errors[name] = e

        threads = [
            threading.Thread(target=worker, args=(f"t{i}", i))
            for i in range(20)
        ]
        for t in threads: t.start()
        for t in threads: t.join()

        for name, exc in errors.items():
            trail_data = exc._context_trail[0]['data']
            self.assertEqual(
                trail_data['name'], name,
                f"Thread {name} got contaminated context: {trail_data}"
            )

    def test_execution_id_is_uuid(self):
        UUID_RE = re.compile(
            r'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
        )
        try:
            with ErrorContext("ctx"):
                raise RuntimeError("boom")
        except RuntimeError as e:
            self.assertRegex(e._execution_id, UUID_RE)

    def test_context_not_leaked_after_exit(self):
        """Stack deve essere pulita dopo un __exit__ senza eccezione."""
        from errorcontext.error_context import _thread_local
        with ErrorContext("ctx"):
            pass
        self.assertEqual(_thread_local.depth('contexts'), 0)


if __name__ == '__main__':
    unittest.main()
