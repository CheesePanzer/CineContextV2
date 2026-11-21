import asyncio
import logging
import os
import re
import shutil
import tempfile
from pathlib import Path
from time import monotonic, time
from uuid import uuid4

from alcedo import ConflictError, Options, ServiceUnavailableError

from ..ai.tacc_transport import JOB_RECORDS, TaccSdkTransport
from ..settings import ModelOptions

logger = logging.getLogger(__name__)
DEPLOYMENT = Path(__file__).resolve().parents[3] / "deploy" / "tacc"
NODE_COOLDOWN_SECONDS = 1800
_NODE_NAME_RE = re.compile(r"^[\w-]+$", re.ASCII)
_INSUFFICIENT_GPU_MEMORY = b"not have enough free memory to start Qwen"
_FAILED_NODE_PATTERNS = (
    re.compile(rb"srun:\s+error:\s+([\w-]+):\s+task\b"),
    re.compile(rb"allocated GPUs on ([\w-]+) do not have enough free memory"),
)


def _gpu_memory_failed_node(log: bytes) -> str | None:
    if _INSUFFICIENT_GPU_MEMORY not in log:
        return None
    for pattern in _FAILED_NODE_PATTERNS:
        match = pattern.search(log)
        if match:
            node = match.group(1).decode("ascii", errors="ignore")
            if _NODE_NAME_RE.fullmatch(node):
                return node
    return None


class TaccJobs:
    def __init__(self, options: Options[ModelOptions]):
        self.options = options.value
        self._job_id = self.options.tacc_job_id
        self._lock = asyncio.Lock()
        self._submission = None
        self._cooldown_nodes: dict[str, float] = {}
        self._inspected_jobs: set[str] = set()

    @property
    def job_id(self) -> str | None:
        return self._job_id

    async def submit_after(self, previous_job_id: str, *, restart: bool = False) -> str:
        async with self._lock:
            if self._job_id is None:
                raise ConflictError("This model connection does not manage a TACC job.")
            if self._job_id != previous_job_id:
                return self._job_id
            if self._submission is None or self._submission.done():
                self._submission = asyncio.create_task(self._submit(previous_job_id, restart=restart))
            submission = self._submission
        return await asyncio.shield(submission)

    async def _submit(self, previous_job_id: str, *, restart: bool = False) -> str:
        transport = TaccSdkTransport(self.options.tcloud_bin, previous_job_id, self.options.timeout_seconds)
        try:
            match = re.search(r"^\s*- time=(\d+:\d{2}:\d{2})\s*$",
                              (DEPLOYMENT / "tuxiv.conf").read_text(), re.M)
            if not match:
                raise ServiceUnavailableError("The TACC job time limit is missing from the deployment configuration.")
            JOB_RECORDS.mkdir(mode=0o700, parents=True, exist_ok=True)
            excluded = os.getenv("CINECONTEXT_TACC_EXCLUDE_NODES", "")
            if excluded and not re.fullmatch(r"[\w-]+(?:,[\w-]+)*", excluded, re.ASCII):
                raise ServiceUnavailableError("Check the configured list of TACC nodes to exclude.")
            state, _ = await transport.job_state(timeout=12)
            cancelled = False
            if restart and state in {"queued", "starting"}:
                cancelled = True
                await transport.command("cancel", "-j", previous_job_id, timeout=12)
                for attempt in range(15):
                    state, _ = await transport.job_state(timeout=6)
                    if state == "stopped":
                        break
                    if state == "unavailable":
                        raise ServiceUnavailableError(
                            "The cancellation could not be confirmed. Refresh TACC status before retrying."
                        )
                    await asyncio.sleep(1)
            if state != "stopped":
                raise ConflictError("TACC has not confirmed that the previous job ended. Check its status and try again.")
            failed_job_id = None if cancelled else previous_job_id
            output = await self._submit_configured_job(transport, failed_job_id=failed_job_id)
            if not output:
                raise ServiceUnavailableError("TACC could not confirm a new job. Check tcloud ps before retrying.")
            submitted = re.findall(rb"^Submitted batch job ([0-9]+)\s*$", output, re.M)
            if len(submitted) != 1:
                raise ServiceUnavailableError("TACC did not return a job ID. Check tcloud ps before retrying.")
        except (OSError, TimeoutError) as error:
            raise ServiceUnavailableError("TACC submission could not be confirmed. Check tcloud ps before retrying.") from error

        self._job_id = submitted[0].decode("ascii")
        try:
            (JOB_RECORDS / self._job_id).write_text(f"{int(time())} {match[1]}\n")
            latest = JOB_RECORDS / f"latest.{uuid4().hex}"
            latest.write_text(f"{self._job_id}\n")
            os.replace(latest, JOB_RECORDS / "latest")
        except OSError:
            logger.exception("Could not save the local record for TACC job %s", self._job_id)
        return self._job_id

    async def _record_unhealthy_node(self, transport: TaccSdkTransport, job_id: str) -> None:
        if job_id in self._inspected_jobs:
            return
        try:
            log = await transport.command("cat", f"slurm_log/cinecontext-qwen-{job_id}.log", timeout=6)
        except (OSError, TimeoutError):
            logger.debug("Could not inspect Slurm log for previous TACC job %s", job_id)
            return
        if log is None:
            return
        self._inspected_jobs.add(job_id)
        if not log:
            return
        node = _gpu_memory_failed_node(log)
        if node:
            self._cooldown_nodes[node] = monotonic() + NODE_COOLDOWN_SECONDS
            logger.info(
                "Temporarily excluding TACC node %s for %d minutes after GPU memory check failed on job %s",
                node, NODE_COOLDOWN_SECONDS // 60, job_id,
            )

    def _active_excluded_nodes(self) -> str:
        excluded = os.getenv("CINECONTEXT_TACC_EXCLUDE_NODES", "")
        if excluded and not re.fullmatch(r"[\w-]+(?:,[\w-]+)*", excluded, re.ASCII):
            raise ServiceUnavailableError("Check the configured list of TACC nodes to exclude.")
        now = monotonic()
        self._cooldown_nodes = {node: until for node, until in self._cooldown_nodes.items() if until > now}
        nodes = list(dict.fromkeys([*(excluded.split(",") if excluded else []), *self._cooldown_nodes]))
        return ",".join(nodes)

    async def _submit_configured_job(self, transport: TaccSdkTransport, *, failed_job_id: str | None = None):
        if failed_job_id:
            await self._record_unhealthy_node(transport, failed_job_id)
        excluded = self._active_excluded_nodes()
        if not excluded:
            return await transport.command("submit", timeout=170, cwd=DEPLOYMENT)
        with tempfile.TemporaryDirectory(prefix="cinecontext-tacc-") as folder:
            deployment = Path(folder)
            for name in ("job.sh", "worker.py", "service_supervisor.py"):
                shutil.copy2(DEPLOYMENT / name, deployment / name)
            config = (DEPLOYMENT / "tuxiv.conf").read_text()
            config = config.replace("  general:\n", f"  general:\n    - exclude={excluded}\n", 1)
            (deployment / "tuxiv.conf").write_text(config)
            return await transport.command("submit", timeout=170, cwd=deployment)
