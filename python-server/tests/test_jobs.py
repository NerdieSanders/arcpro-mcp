import importlib
import pathlib
import sys
import threading
import time
import unittest

import anyio

PYTHON_SERVER = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PYTHON_SERVER))

jobs = importlib.import_module("arcgis_mcp.jobs")
pipe_client = importlib.import_module("arcgis_mcp.pipe_client")


class FakeClient:
    """Pretends to be ArcGIS Pro: execute_code runs until done or cancelled."""

    def __init__(self, run_seconds):
        self.run_seconds = run_seconds
        self.calls = []
        self.cancelled = threading.Event()
        self.job_id = None

    def send_command(self, command, params=None, timeout_ms=None, retries=None):
        params = params or {}
        self.calls.append(command)
        if command == "execute_code":
            self.job_id = params["job_id"]
            deadline = time.monotonic() + self.run_seconds
            while time.monotonic() < deadline:
                if self.cancelled.is_set():
                    return {"success": True, "data": {"cancelled": True}}
                time.sleep(0.02)
            return {"success": True, "data": {"ok": True}}
        if command == "job_status":
            job = {"job_id": self.job_id, "percent": 40, "last_message": "step 2"}
            return {"success": True, "data": {"running_job": job}}
        if command == "cancel_job":
            self.cancelled.set()
            return {"success": True, "data": {"cancelled": True}}
        raise AssertionError(command)


class FakeContext:
    def __init__(self):
        self.messages = []

    async def report_progress(self, progress, total=None, message=None):
        self.messages.append(message)


class JobsTest(unittest.TestCase):
    def setUp(self):
        self._saved = (jobs.client, jobs.HEARTBEAT_SECONDS)
        jobs.HEARTBEAT_SECONDS = 0.1

    def tearDown(self):
        jobs.client, jobs.HEARTBEAT_SECONDS = self._saved

    def test_long_job_sends_heartbeats_with_job_progress(self):
        jobs.client = FakeClient(run_seconds=0.5)
        ctx = FakeContext()

        result = anyio.run(jobs.run_job, "execute_code", {"code": "x"}, ctx)

        self.assertEqual({"ok": True}, result["data"])
        self.assertGreaterEqual(len(ctx.messages), 2)
        self.assertIn("40%: step 2", ctx.messages[-1])
        self.assertNotIn("cancel_job", jobs.client.calls)

    def test_client_cancellation_cancels_the_job_in_pro(self):
        jobs.client = FakeClient(run_seconds=5)

        async def cancel_soon():
            with anyio.move_on_after(0.3):
                await jobs.run_job("execute_code", {"code": "x"}, None)

        anyio.run(cancel_soon)

        self.assertIn("cancel_job", jobs.client.calls)
        self.assertTrue(jobs.client.cancelled.wait(1))

    def test_max_wait_returns_running_then_job_result_finishes(self):
        jobs.client = FakeClient(run_seconds=0.6)

        async def flow():
            job_id = jobs._start("execute_code", {"code": "x"})
            first = await jobs.wait(job_id, None, max_wait=0.2)
            second = await jobs.wait(job_id, None, max_wait=5)
            third = await jobs.wait(job_id, None, max_wait=5)
            return first, second, third

        first, second, third = anyio.run(flow)

        self.assertTrue(first["running"])
        self.assertEqual("step 2", first["data"]["progress"]["last_message"])
        self.assertEqual({"ok": True}, second["data"])
        self.assertEqual("UNKNOWN_JOB", third["error_code"])
        self.assertNotIn("cancel_job", jobs.client.calls)


class RetryTest(unittest.TestCase):
    def test_failure_after_send_is_never_retried(self):
        client = pipe_client.ArcGisPipeClient(retries=2, retry_delay_ms=0)
        attempts = []

        def broken_read(command, params, timeout_ms):
            attempts.append(command)
            raise OSError("pipe closed before the full response was read")

        client._send_once = broken_read
        response = client.send_command("execute_code", {"code": "x"})

        self.assertFalse(response["success"])
        self.assertEqual(1, len(attempts))

    def test_failure_before_send_is_retried(self):
        client = pipe_client.ArcGisPipeClient(retries=2, retry_delay_ms=0)
        attempts = []

        def not_sent(command, params, timeout_ms):
            attempts.append(command)
            raise pipe_client.RequestNotSentError("pipe not found")

        client._send_once = not_sent
        client.send_command("status")

        self.assertEqual(3, len(attempts))


if __name__ == "__main__":
    unittest.main()
