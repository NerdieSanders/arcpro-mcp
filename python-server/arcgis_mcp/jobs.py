"""Long-running commands with progress heartbeats and real cancellation.

A job command (execute_code, run_gp_tool) blocks on the pipe in a worker
thread. Meanwhile the event loop stays free, sends a progress notification
every few seconds (which keeps the client's idle timer from firing), and
reads the job's latest message over a second pipe connection. If the client
cancels the request, cancel_job is sent to ArcGIS Pro so the work stops
instead of running on unseen.

Some clients (the Claude desktop app) end every tool call after about 60 s,
and progress does not extend that. Setting ARCPRO_MCP_MAX_WAIT_S below that
limit makes a long call return early with the job still running; job_result
then waits for it in further calls of at most the same length.
"""

import os
import threading
import time
import uuid

import anyio
from mcp.server.fastmcp import Context

from .pipe_client import ArcGisPipeClient

HEARTBEAT_SECONDS = 5.0
POLL_SECONDS = 0.25
MAX_WAIT_S = float(os.environ.get("ARCPRO_MCP_MAX_WAIT_S", "0") or 0)

client = ArcGisPipeClient()

# Jobs started by this server process: job_id -> {"command", "result"}.
# "result" stays None until Pro answers.
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()


def new_job_id() -> str:
    return uuid.uuid4().hex[:12]


async def send(command: str, params: dict | None = None, timeout_ms=5000) -> dict:
    """Runs a short command without blocking the event loop."""
    return await anyio.to_thread.run_sync(
        lambda: client.send_command(command, params or {}, timeout_ms=timeout_ms)
    )


async def cancel(job_id: str | None = None) -> dict:
    return await send("cancel_job", {"job_id": job_id} if job_id else {})


def job_command(job_id: str) -> str | None:
    with _jobs_lock:
        job = _jobs.get(job_id)
    return job["command"] if job else None


def _start(command: str, params: dict) -> str:
    job_id = new_job_id()
    params = {**params, "job_id": job_id}
    with _jobs_lock:
        _jobs[job_id] = {"command": command, "result": None}

    def work():
        result = client.send_command(command, params, retries=0)
        with _jobs_lock:
            _jobs[job_id]["result"] = result

    threading.Thread(target=work, name=f"job-{job_id}", daemon=True).start()
    return job_id


async def run_job(command: str, params: dict, ctx: Context | None) -> dict:
    """Starts a job command and waits for it, reporting progress."""
    job_id = _start(command, params)
    return await wait(job_id, ctx)


async def wait(job_id: str, ctx: Context | None, max_wait: float | None = None) -> dict:
    """Waits for a job. Returns Pro's response, or a running marker when
    max_wait (default ARCPRO_MCP_MAX_WAIT_S; 0 means no limit) runs out."""
    max_wait = MAX_WAIT_S if max_wait is None else max_wait
    started = time.monotonic()
    next_beat = started + HEARTBEAT_SECONDS

    try:
        while True:
            with _jobs_lock:
                job = _jobs.get(job_id)
                if job is None:
                    return {
                        "success": False,
                        "error_code": "UNKNOWN_JOB",
                        "message": f"No job '{job_id}' was started by this server, "
                        "or its result was already returned.",
                    }
                if job["result"] is not None:
                    del _jobs[job_id]
                    result = job["result"]
                    result.setdefault("data", None)
                    return result

            now = time.monotonic()
            if max_wait and now - started >= max_wait:
                status = await _job_snapshot(job_id)
                return {
                    "success": True,
                    "running": True,
                    "data": {
                        "job_id": job_id,
                        "running": True,
                        "progress": status,
                        "next_step": f"Call job_result(job_id='{job_id}') to keep "
                        "waiting, or cancel_job to stop it.",
                    },
                }
            if now >= next_beat:
                await _heartbeat(ctx, job_id, now - started)
                next_beat = now + HEARTBEAT_SECONDS
            await anyio.sleep(POLL_SECONDS)
    except BaseException:
        # The client cancelled (or the server is shutting down) while the job
        # was still running in Pro. Stop it there too.
        with _jobs_lock:
            unfinished = job_id in _jobs and _jobs[job_id]["result"] is None
        if unfinished:
            with anyio.CancelScope(shield=True):
                await cancel(job_id)
        raise


async def _job_snapshot(job_id: str) -> dict:
    status = await send("job_status", timeout_ms=1000)
    job = (status.get("data") or {}).get("running_job") or {}
    return job if job.get("job_id") == job_id else {}


async def _heartbeat(ctx: Context | None, job_id: str, elapsed: float) -> None:
    if ctx is None:
        return

    message = f"{elapsed:.0f} s elapsed"
    job = await _job_snapshot(job_id)
    if job.get("percent") is not None:
        message += f", {job['percent']}%"
    if job.get("last_message"):
        message += f": {job['last_message'][:200]}"

    await ctx.report_progress(progress=round(elapsed), message=message)
