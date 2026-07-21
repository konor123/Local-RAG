import tempfile
import unittest
from pathlib import Path

from ollama_runtime import OllamaRuntimeSupervisor, OllamaStartupError, normalize_base_url


class FakeClock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


class FakeProcess:
    def __init__(self, exit_code=None):
        self.exit_code = exit_code
        self.pid = 1234

    def poll(self):
        return self.exit_code


class OllamaRuntimeTests(unittest.TestCase):
    def _supervisor(self, probe, **kwargs):
        clock = kwargs.pop("clock", FakeClock())
        log_path = kwargs.pop("log_path", Path(tempfile.gettempdir()) / "osl_ollama_runtime_test.log")
        return OllamaRuntimeSupervisor(
            "http://localhost:11434/",
            probe=probe,
            clock=clock,
            sleeper=clock.sleep,
            startup_timeout=3,
            handoff_grace=kwargs.pop("handoff_grace", 0),
            log_path=log_path,
            **kwargs,
        )

    def test_normalizes_configured_endpoint(self):
        self.assertEqual(normalize_base_url(" http://localhost:11434/ "), "http://localhost:11434")
        with self.assertRaises(ValueError):
            normalize_base_url("localhost:11434/api/tags")

    def test_uses_configured_endpoint_when_server_is_already_running(self):
        calls = []
        supervisor = self._supervisor(lambda url, timeout: calls.append(url) or (True, ""))

        self.assertEqual(supervisor.ensure_running(), "already_running")
        self.assertEqual(calls, ["http://localhost:11434"])

    def test_reports_missing_executable(self):
        supervisor = self._supervisor(lambda url, timeout: (False, "ConnectionError"), executable_resolver=lambda: None)

        with self.assertRaises(OllamaStartupError) as raised:
            supervisor.ensure_running()
        self.assertEqual(raised.exception.diagnostic.category, "executable_not_found")

    def test_reports_launcher_exception(self):
        def launcher(*args, **kwargs):
            raise OSError("blocked")

        supervisor = self._supervisor(lambda url, timeout: (False, "ConnectionError"), executable_resolver=lambda: "ollama", launcher=launcher)

        with self.assertRaises(OllamaStartupError) as raised:
            supervisor.ensure_running()
        self.assertEqual(raised.exception.diagnostic.category, "launch_failed")

    def test_retains_owned_process_when_readiness_succeeds(self):
        responses = iter([(False, "ConnectionError"), (True, "")])
        process = FakeProcess()
        supervisor = self._supervisor(lambda url, timeout: next(responses), executable_resolver=lambda: "ollama", launcher=lambda *args, **kwargs: process)

        self.assertEqual(supervisor.ensure_running(), "started_owned")
        self.assertIs(supervisor.owned_process, process)

    def test_handles_delayed_external_handoff_after_launcher_exits(self):
        responses = iter([(False, "ConnectionError"), (False, "ConnectionError"), (True, "")])
        process = FakeProcess(exit_code=0)
        supervisor = self._supervisor(lambda url, timeout: next(responses), executable_resolver=lambda: "ollama", launcher=lambda *args, **kwargs: process, handoff_grace=1)

        self.assertEqual(supervisor.ensure_running(), "delegated_external")
        self.assertIsNone(supervisor.owned_process)

    def test_reports_child_exit_after_handoff_grace(self):
        process = FakeProcess(exit_code=7)
        supervisor = self._supervisor(lambda url, timeout: (False, "ConnectionError"), executable_resolver=lambda: "ollama", launcher=lambda *args, **kwargs: process)

        with self.assertRaises(OllamaStartupError) as raised:
            supervisor.ensure_running()
        self.assertEqual(raised.exception.diagnostic.category, "child_exited")
        self.assertEqual(raised.exception.diagnostic.exit_code, 7)

    def test_classifies_http_endpoint_failure(self):
        process = FakeProcess()
        supervisor = self._supervisor(lambda url, timeout: (False, "HTTP 404"), executable_resolver=lambda: "ollama", launcher=lambda *args, **kwargs: process)

        with self.assertRaises(OllamaStartupError) as raised:
            supervisor.ensure_running()
        self.assertEqual(raised.exception.diagnostic.category, "endpoint_not_ollama")

    def test_redacts_credentials_in_output_tail(self):
        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "ollama.log"
            log_path.write_text("connect http://secret@example.test:11434 failed", encoding="utf-8")
            supervisor = self._supervisor(lambda url, timeout: (False, "ConnectionError"), log_path=log_path, executable_resolver=lambda: None)

            with self.assertRaises(OllamaStartupError) as raised:
                supervisor.ensure_running()
            self.assertNotIn("secret", raised.exception.diagnostic.output_tail)


if __name__ == "__main__":
    unittest.main()
