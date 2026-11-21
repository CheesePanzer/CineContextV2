"""Use the course SDK when the app runs outside the TACC network."""

import asyncio
import json
import logging
import os
import re
import signal
import tempfile
from pathlib import Path
from time import monotonic, time
from uuid import uuid4

import httpx

from .types import ModelReadiness

logger = logging.getLogger(__name__)
JOB_RECORDS = Path(__file__).resolve().parents[3] / ".runtime" / "tacc-jobs"
TACC_NOT_READY_CODE = "tacc_not_ready"


def slurm_duration_seconds(value: str) -> int | None:
    match = re.fullmatch(r"(?:(\d+)-)?(?:(\d+):)?(\d+):(\d+)", value)
    if not match:
        return None
    days, hours, minutes, seconds = (int(part or 0) for part in match.groups())
    return ((days * 24 + hours) * 60 + minutes) * 60 + seconds


class TaccSdkTransport(httpx.AsyncBaseTransport):
    def __init__(self, sdk, job_id, timeout):
        if not job_id.isascii() or not job_id.isdecimal():
            raise ValueError("TACC job ID must be numeric")
        self.sdk = sdk
        self.job_id = job_id
        self.queue = f"cinecontext-course-kit/e2e-{job_id}"
        self.timeout = timeout

    async def command(self, *args, timeout=20, cwd=None):
        process_options = {"start_new_session": True} if os.name == "posix" else {}
        process = await asyncio.create_subprocess_exec(
            self.sdk, *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            cwd=cwd, **process_options,
        )
        try:
            output, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except (TimeoutError, asyncio.CancelledError):
            try:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
            except ProcessLookupError:
                pass
            await process.wait()
            raise
        if process.returncode == 0 and b"[Tcloud Error]" not in output:
            return output
        if args[0] == "ps" and b"Invalid job id specified" in output:
            return b"JOBID"
        if args[0] == "cat" and b"No such file or directory" in output:
            return b""
        if args[0] in {"ps", "cat"}:
            logger.debug("TACC status command failed command=%s job_id=%s exit_code=%s ssh_error=%s",
                         args[0], self.job_id, process.returncode, b"ssh:" in output.lower())
        return None

    async def job_state(self, timeout=20) -> tuple[str, int | None]:
        output = await self.command("ps", "-j", self.job_id, timeout=timeout)
        if output is None:
            return "unavailable", None
        for row in output.decode(errors="replace").splitlines():
            columns = row.split()
            if len(columns) >= 5 and columns[0] == self.job_id:
                state = {"PD": "queued", "CF": "queued", "R": "starting", "CG": "stopped"}.get(
                    columns[4], "unavailable",
                )
                elapsed = slurm_duration_seconds(columns[5]) if len(columns) > 5 else None
                return state, elapsed
        return ("stopped" if b"JOBID" in output else "unavailable"), None

    def job_timing(self) -> tuple[int | None, int | None]:
        try:
            submitted_at, walltime = (JOB_RECORDS / self.job_id).read_text(encoding="utf-8").split()
            return int(submitted_at), slurm_duration_seconds(walltime)
        except (OSError, ValueError):
            return None, None

    async def readiness(self, include_timing=False) -> ModelReadiness:
        try:
            state, elapsed = await self.job_state(timeout=6)
        except (OSError, TimeoutError):
            return ModelReadiness("unavailable", self.job_id)

        queued = remaining = None
        if include_timing and state in {"queued", "starting"}:
            submitted_at, limit = self.job_timing()
            if state == "queued" and submitted_at is not None:
                queued = max(0, int(time()) - submitted_at)
            elif state == "starting" and limit is not None and elapsed is not None:
                remaining = max(0, limit - elapsed)

        if state == "starting":
            try:
                marker = await self.command("cat", f"{self.queue}/ready", timeout=6)
                if marker:
                    state = "ready"
                elif marker is None:
                    state = "unavailable"
            except (OSError, TimeoutError):
                state = "unavailable"
        return ModelReadiness(state, self.job_id, queued, remaining)

    async def handle_async_request(self, request):
        if request.method != "POST" or request.url.path != "/v1/chat/completions":
            return httpx.Response(404, json={"error": {"message": "Unsupported TACC operation"}})

        readiness = (await self.readiness()).status
        # A running allocation may briefly fail the remote ready-file check.
        # The exchange already waits for the worker and checks that the job is still active.
        if readiness in {"queued", "stopped"}:
            message = {
                "queued": "TACC is waiting for GPUs.",
                "stopped": "The TACC job has ended. Start a new job.",
            }[readiness]
            return httpx.Response(503, json={"error": {"message": message, "code": TACC_NOT_READY_CODE}})

        try:
            async with asyncio.timeout(self.timeout):
                return await self.exchange(request)
        except TimeoutError as error:
            raise httpx.ReadTimeout("Timed out waiting for the TACC job to return a result", request=request) from error
        except OSError as error:
            raise httpx.ConnectError(f"Cannot use the TACC SDK: {error}", request=request) from error

    async def exchange(self, request):
        request_id = uuid4().hex
        with tempfile.TemporaryDirectory() as directory:
            incoming = Path(directory, f"{request_id}.request.json")
            response_path = f"{self.queue}/{request_id}.response.json"
            incoming.write_bytes(await request.aread())
            uploaded = False
            next_health_check = monotonic() + 20
            while True:
                try:
                    if not uploaded:
                        uploaded = await self.command("upload", str(incoming), self.queue + "/") is not None
                        if uploaded:
                            logger.info("Submitted Qwen request %s to %s", request_id, self.queue)
                    if uploaded and (output := await self.command("cat", response_path)):
                        reply = json.loads(output)
                        if reply["status"] == 504:
                            raise httpx.ReadTimeout("TACC inference timed out", request=request)
                        return httpx.Response(reply["status"], json=reply["body"])
                    if monotonic() >= next_health_check:
                        state, _ = await self.job_state()
                        if state == "stopped":
                            raise httpx.ConnectError(f"TACC job {self.job_id} is no longer active", request=request)
                        next_health_check = monotonic() + 30
                except TimeoutError:
                    # An SDK command timeout does not cancel the inference request.
                    pass
                await asyncio.sleep(5)
