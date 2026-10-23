"""Trusted persistent CodeBuild bootstrap. Fetch only the active run's digest-verified source."""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile

import boto3

bucket = "getddo-deploy-942466928325-ap-northeast-2"
s3 = boto3.client("s3", region_name="ap-northeast-2")

def read(key):
    response = s3.get_object(Bucket=bucket, Key=key)
    with response["Body"] as body:
        return body.read()

owner = json.loads(read("staging/control/active.json"))
failed_build = os.environ.get("FAILED_BUILD_ID")
if failed_build:
    build = boto3.client("codebuild", region_name="ap-northeast-2").batch_get_builds(ids=[failed_build])["builds"][0]
    variables = {v["name"]: v["value"] for v in build["environment"]["environmentVariables"]}
    if build["projectName"] != "getddo-staging-up" or variables.get("RUN_ID") != owner["run_id"]:
        raise ValueError("Failure event belongs to another run")
run = os.environ.get("RUN_ID") or owner["run_id"]
if run != owner["run_id"] or not run.isalnum() or len(run) != 32:
    raise ValueError("Invalid active run")
manifest = json.loads(read(f"staging/runs/{run}/manifest.json"))
digest = manifest["bundle_sha256"]
if digest != owner["bundle_sha256"] or manifest["bundle_key"] != f"staging/bundles/{digest}.tar.gz":
    raise ValueError("Invalid bundle ownership")
content = read(manifest["bundle_key"])
if hashlib.sha256(content).hexdigest() != digest:
    raise ValueError("Bundle digest mismatch")
root = Path("/tmp/staging-source")
root.mkdir(mode=0o700)
with tarfile.open(fileobj=io.BytesIO(content), mode="r:gz") as archive:
    for member in archive.getmembers():
        if not (member.isfile() or member.isdir()) or Path(member.name).is_absolute() or ".." in Path(member.name).parts:
            raise ValueError("Unsafe archive entry")
    archive.extractall(root, filter="data")
os.environ["RUN_ID"] = run
subprocess.run(["python3", str(root / "scripts/staging_runner.py")], cwd=root, check=True)
