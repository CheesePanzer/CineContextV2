"""Execute OpenAI-compatible inference requests inside the GPU job."""

import json
import os
import socket
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def wait_for_model(base_url, server_pid):
    while True:
        os.kill(server_pid, 0)
        try:
            with urlopen(base_url + "/models", timeout=3) as response:
                if any(model["id"] == "qwen3.8-27b" for model in json.load(response)["data"]):
                    return
        except (URLError, socket.timeout):
            pass
        time.sleep(4)


def complete(base_url, data):
    request = Request(base_url + "/chat/completions", data=data, headers={"Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=390) as response:
            return {"status": response.status, "body": json.load(response)}
    except HTTPError as error:
        try:
            return {"status": error.code, "body": json.load(error)}
        except ValueError:
            return {"status": error.code, "body": {"error": {"message": error.reason}}}
    except (socket.timeout, URLError):
        return {"status": 504, "body": {"error": {"message": "TACC inference timed out"}}}


def main(server_pid):
    base_url = os.environ["QWEN_BASE_URL"]
    wait_for_model(base_url, server_pid)
    queue = Path(os.environ["TACC_USERDIR"]) / "cinecontext-course-kit" / ("e2e-" + os.environ["SLURM_JOB_ID"])
    queue.mkdir(mode=0o700, parents=True, exist_ok=True)
    (queue / "ready").write_text("qwen3.8-27b\n")
    print("CINECONTEXT_QWEN_READY", flush=True)
    while True:
        os.kill(server_pid, 0)
        for request in sorted(queue.glob("*.request.json"), key=lambda path: path.stat().st_mtime):
            reply = queue / request.name.replace(".request.json", ".response.json")
            if not reply.exists():
                temporary = reply.with_suffix(".tmp")
                temporary.write_text(json.dumps(complete(base_url, request.read_bytes())))
                temporary.replace(reply)
            request.unlink()
        for reply in queue.glob("*.response.json"):
            if time.time() - reply.stat().st_mtime > 900:
                reply.unlink()
        time.sleep(1)


if __name__ == "__main__":
    main(int(sys.argv[1]))
