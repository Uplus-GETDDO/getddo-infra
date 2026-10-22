#!/usr/bin/env python3
"""CodeBuild entry point. The persistent buildspec verifies this source before execution."""
import os
from pathlib import Path
import sys

from staging_aws import Effects
from staging_lifecycle import check_owner, cleanup, provision, validate_manifest


def main():
    effects = Effects(Path(__file__).resolve().parent.parent)
    run = os.environ.get("RUN_ID") or effects.owner()["run_id"]
    manifest = validate_manifest(effects.manifest(run))
    check_owner(effects.owner(), manifest)
    action = os.environ["ACTION"]
    if manifest.get("phase") == "destroyed":
        return 0
    if action == "up" and manifest.get("phase") in ("ready", "collected"):
        return 0
    if action == "up":
        provision(effects, manifest)
    elif action in ("destroy", "ttl", "rollback"):
        try:
            cleanup(effects, manifest, action)
        except Exception as error:
            effects.record(manifest, "cleanup_failed", reason=action, detail=type(error).__name__)
            raise
    elif action == "collect":
        effects.collect(manifest, timeout=360)
    else:
        raise ValueError("Unsupported staging action")
    return 0


if __name__ == "__main__":
    sys.exit(main())
