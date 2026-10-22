#!/usr/bin/env python3
"""Run as root on the Stage Locust host after the synthetic actor is seeded.

Stdin: {"manifest": <lifecycle manifest>, "actor": {"user_id": UUID, "role": "USER", "membership": "excellent"}}.
Existing persistent SSM credentials are read in memory, never rotated or printed.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid

from staging_lifecycle import REGION, validate_manifest


def private_file(path, value):
    path = Path(path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, value.encode())
    finally:
        os.close(fd)


def install(manifest, actor, source, root=Path("/"), fetch=None, start=True):
    validate_manifest(manifest)
    if (set(actor) != {"user_id", "role", "membership"} or actor.get("role") != "USER" or
            actor.get("membership") not in ("excellent", "vip", "vvip") or
            str(uuid.UUID(actor.get("user_id", ""))) != actor["user_id"]):
        raise ValueError("A seeded synthetic Stage USER is required")
    deployment = manifest["deployment"]
    target = deployment["api_url"]
    if not re.fullmatch(r"http://internal-[a-zA-Z0-9-]+\.ap-northeast-2\.elb\.amazonaws\.com", target):
        raise ValueError("Only the exact internal Stage ALB is accepted")
    if not re.fullmatch(r"10\.20\.(?:0|1)\.\d{1,3}", deployment["locust_instance"]["private_ip"]):
        raise ValueError("Locust must bind the existing VPC private address")
    if fetch is None:
        def fetch():
            result = subprocess.run(["aws", "ssm", "get-parameters", "--region", REGION, "--with-decryption",
                "--names", "/getddo/staging/locust/admin-user", "/getddo/staging/locust/admin-password",
                "/getddo/staging/locust/session-secret", "--output", "json"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
            if result.returncode:
                raise RuntimeError("Could not read persistent Locust credentials")
            return {p["Name"].rsplit("/", 1)[1]: p["Value"] for p in json.loads(result.stdout)["Parameters"]}
    credentials = fetch()
    if set(credentials) != {"admin-user", "admin-password", "session-secret"} or len(credentials["session-secret"]) < 32:
        raise ValueError("Persistent Locust credentials are incomplete")
    from werkzeug.security import generate_password_hash
    secrets_dir = root / "run/getddo-staging/locust"
    actor_dir = root / "run/getddo-staging/locust-scenario"
    manifest_dir = root / "etc/getddo-loadtest"
    for directory in (secrets_dir, actor_dir, manifest_dir, root / "var/lib/getddo-loadtest/reports"):
        if directory.is_symlink():
            raise ValueError("Host configuration directory must not be a symlink")
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory.chmod(0o700)
    private_file(secrets_dir / "admin-user", credentials["admin-user"])
    private_file(secrets_dir / "admin-password-hash", generate_password_hash(credentials["admin-password"], method="scrypt"))
    private_file(secrets_dir / "session-secret", credentials["session-secret"])
    private_file(actor_dir / "actor.json", json.dumps(actor))
    private_file(manifest_dir / "manifest.json", json.dumps({
        "environment": "staging", "target": target, "public_origin": manifest["public_origin"],
        "expected_workers": 2, "scenario": "smoke_events", "actor_file": "/run/scenario/actor.json"}))
    if start:
        environment = dict(os.environ, LOCUST_BIND_IP=deployment["locust_instance"]["private_ip"])
        result = subprocess.run(["docker", "compose", "-f", "loadtest/compose.yaml", "up", "-d", "--scale", "worker=2"],
                                cwd=source, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if result.returncode:
            raise RuntimeError("Locust idle runtime installation failed")


def main():
    if os.geteuid() != 0:
        raise RuntimeError("Locust configuration must be installed by root")
    payload = json.load(sys.stdin)
    install(payload["manifest"], payload["actor"], Path(__file__).resolve().parent.parent)
    return 0


if __name__ == "__main__":
    sys.exit(main())
