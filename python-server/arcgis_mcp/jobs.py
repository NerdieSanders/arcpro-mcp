"""Long-running commands with progress heartbeats and real cancellation.

A job command (execute_code, run_gp_tool) blocks on the pipe in a worker
thread. Meanwhile the event loop stays free, sends a progress notification
every few seconds (which keeps the client's idle timer from firing), and
reads the job's latest message over a second pipe connection. If the client
cancels the request, cancel_job is sent to ArcGIS Pro so the work stops
instead of running on unseen.
"""

import time
import uuid

import anyio
from mcp.server.fastmcp import Context

from .pipe_client import ArcGisPipeClient

HEARTBEAT_SECONDS = 5.0

client = ArcGisPipeClient()


def new_job_id() -> str:
    return uuid.uuid4().hex[:12]


async def send(command: str, params: dict | None = None, timeout_ms=5000) -> dict:
    """Runs a short command without blocking the event loop."""
    return await anyio.to_thread.run_sync(
        lambda: client.send_command(command, params or {}, timeout_ms=timeout_ms)
    )


async def cancel(job_id: str | None = None) -> dict:
    return await send("cancel_job", {"job_id": job_id} if job_id else {})


async def run_job(command: str, params: dict, ctx: Context | None) -> dict:
    """Runs a job command, reporting progress until it returns."""
    job_id = new_job_id()
    params = {**params, "job_id": job_id}
    started = time.monotonic()
    result: dict = {}

    async def call():
        result.update(
            await anyio.to_thread.run_sync(
                lambda: client.send_command(command, params, retries=0),
                abandon_on_cancel=True,
            )
        )
        tg.cancel_scope.cancel()

    try:
        async with anyio.create_task_group() as tg:
            tg.start_soon(call)
            while True:
                await anyio.sleep(HEARTBEAT_SECONDS)
                await _heartbeat(ctx, job_id, time.monotonic() - started)
    except BaseException:
        if not result:
            # The client cancelled (or the server is shutting down) while the
            # job was still running in Pro. Stop it there too.
            with anyio.CancelScope(shield=True):
                await cancel(job_id)
        raise

    result.setdefault("data", None)
    return result


async def _heartbeat(ctx: Context | None, job_id: str, elapsed: float) -> None:
    if ctx is None:
        return

    message = f"{elapsed:.0f} s elapsed"
    status = await send("job_status", timeout_ms=1000)
    job = (status.get("data") or {}).get("running_job") or {}
    if job.get("job_id") == job_id:
        if job.get("percent") is not None:
            message += f", {job['percent']}%"
        if job.get("last_message"):
            message += f": {job['last_message'][:200]}"

    await ctx.report_progress(progress=round(elapsed), message=message)
