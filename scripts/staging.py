#!/usr/bin/env python3
"""Submit independent AWS staging executions; this terminal owns no long-running task."""
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import sys
import uuid

from staging_lifecycle import ACCOUNT, BUCKET, LOCK, REGION, validate_manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["up", "status", "collect", "destroy"])
    parser.add_argument("--max-duration", type=int, help="Mandatory maximum lifetime in minutes (15..1440)")
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--run-id")
    args = parser.parse_args()
    from staging_aws import AWS
    aws = AWS()
    if args.action == "up":
        if not args.max_duration or not 15 <= args.max_duration <= 1440 or not args.bundle or not args.config:
            parser.error("up requires --max-duration 15..1440, --bundle, and --config")
        config = json.loads(args.config.read_text())
        now = dt.datetime.now(dt.timezone.utc)
        content = args.bundle.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        manifest = validate_manifest(dict(config, account_id=ACCOUNT, region=REGION, deploy_bucket=BUCKET,
            run_id=uuid.uuid4().hex, bundle_sha256=digest, bundle_key=f"staging/bundles/{digest}.tar.gz",
            created_at=now.isoformat(), expires_at=(now + dt.timedelta(minutes=args.max_duration)).isoformat(),
            max_duration_minutes=args.max_duration, phase="submitted"))
        for hook in ("prepare", "collect", "detach"):
            if not manifest.get("hooks", {}).get(hook):
                parser.error("config requires immutable hooks for prepare/collect/detach")
        # Immutable bundle write precedes ownership. A duplicate digest is safe only after readback.
        s3 = aws.clients["s3"]
        try:
            s3.put_object(Bucket=BUCKET, Key=manifest["bundle_key"], Body=content,
                          ServerSideEncryption="AES256", IfNoneMatch="*")
        except s3.exceptions.ClientError as error:
            if error.response["Error"]["Code"] not in ("PreconditionFailed", "412"):
                raise
            from staging_lifecycle import verify_bundle
            response = s3.get_object(Bucket=BUCKET, Key=manifest["bundle_key"])
            with response["Body"] as body:
                verify_bundle(body.read(), digest)
        manifest["persistent_credentials"] = aws.credentials_metadata()
        aws.put(LOCK, {"run_id": manifest["run_id"], "bundle_sha256": digest}, IfNoneMatch="*")
        # If submission is interrupted, retain ownership for operator recovery; no workload exists yet.
        aws.record(manifest, "submitted")
        aws.guard(manifest)
        build_id = aws.start(manifest, "up")
        print(json.dumps({"run_id": manifest["run_id"], "build_id": build_id, "expires_at": manifest["expires_at"]}))
    else:
        run = args.run_id or aws.owner()["run_id"]
        manifest = validate_manifest(aws.manifest(run))
        if args.action == "status":
            print(json.dumps(manifest))
        else:
            from staging_lifecycle import check_owner
            if manifest.get("phase") == "destroyed" and args.action == "destroy":
                print(json.dumps({"run_id": run, "phase": "destroyed"}))
                return 0
            check_owner(aws.owner(), manifest)
            print(json.dumps({"run_id": run, "build_id": aws.start(manifest, args.action)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
