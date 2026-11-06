#!/usr/bin/env bash
set -Eeuo pipefail
cd "${1:-$HOME/ai}"
exec /usr/bin/python3 - <<'PYTHON'
import fcntl
import json
import re
import subprocess
import sys
from pathlib import Path


def log(message):
    print(f"cleanup-images: {message}", file=sys.stderr, flush=True)


def docker(*args):
    result = subprocess.run(["docker", *args], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"Docker {args[0]} {args[1]} failed (status {result.returncode})")
    return result.stdout


def image_id(ref):
    return json.loads(docker("image", "inspect", ref))[0]["Id"]


def release(suffix):
    compose = Path("compose.prod.yaml" + suffix)
    env = Path(".env" + suffix)
    image = Path(".image.env" + suffix)
    if not compose.is_file() or not env.is_file() or not image.is_file():
        raise RuntimeError(f"Incomplete release files: {suffix or 'current'}")
    args = ("compose", "--env-file", str(env), "--env-file", str(image),
            "-f", str(compose))
    environment = docker(*args, "config", "--environment")
    refs = [line.partition("=")[2] for line in environment.splitlines()
            if line.startswith("APP_IMAGE=")]
    if len(refs) != 1 or not re.fullmatch(
        r"[a-z0-9][a-z0-9./:_-]*(?::[0-9a-f]{40}|@sha256:[0-9a-f]{64})", refs[0]
    ):
        raise RuntimeError("APP_IMAGE must identify an immutable SHA or digest")
    config = json.loads(docker(*args, "config", "--format", "json"))
    refs_in_use = {service["image"] for service in config["services"].values()
                   if "image" in service}
    if refs[0] not in refs_in_use:
        raise RuntimeError("APP_IMAGE is absent from rendered Compose")
    return refs[0], {image_id(ref) for ref in refs_in_use}


def main():
    lock = Path(".deploy.lock").open("a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("skipped: deployment or cleanup is active")
        return 0
    if any(Path(name).exists() for name in
           (".image.env.next", "compose.prod.yaml.next", ".schedule.next")):
        log("skipped: deployment is staged")
        return 0
    current, protected = release("")
    rollback_files = [Path(name + ".rollback") for name in
                      ("compose.prod.yaml", ".env", ".image.env")]
    if any(path.exists() for path in rollback_files):
        _, rollback_ids = release(".rollback")
        protected.update(rollback_ids)
    repository = current.split("@", 1)[0] if "@" in current else current.rsplit(":", 1)[0]
    containers = docker("container", "ls", "--all", "--quiet").split()
    for container in containers:
        protected.add(json.loads(docker("container", "inspect", container))[0]["Image"])
    rows = docker("image", "ls", "--no-trunc", "--format", "{{json .}}").splitlines()
    candidates = set()
    for row in rows:
        entry = json.loads(row)
        if entry["Repository"] == repository and re.fullmatch(r"[0-9a-f]{40}", entry["Tag"]):
            candidates.add(f"{repository}:{entry['Tag']}")
    failed = False
    for ref in sorted(candidates):
        if image_id(ref) in protected:
            continue
        try:
            docker("image", "rm", "--no-prune", ref)
            log(f"removed {ref}")
        except RuntimeError as error:
            log(f"failed to remove {ref}: {error}")
            failed = True
    log(f"completed: checked {len(candidates)} SHA tags; protected {len(protected)} image IDs")
    return int(failed)


try:
    sys.exit(main())
except Exception as error:
    log(f"aborted safely: {error}")
    sys.exit(1)
PYTHON
