#!/usr/bin/env python3
"""Staging ownership and cleanup state machine. All effects are injected for tests."""
import datetime as dt
import hashlib
import json
import math
import re

ACCOUNT = "942466928325"
REGION = "ap-northeast-2"
BUCKET = "getddo-deploy-942466928325-ap-northeast-2"
LOCK = "staging/control/active.json"
PERSISTENT_PARAMETERS = frozenset({
    "/getddo/staging/locust/admin-user",
    "/getddo/staging/locust/admin-password",
    "/getddo/staging/locust/session-secret",
})


def validate_manifest(value):
    if value.get("account_id") != ACCOUNT or value.get("region") != REGION:
        raise ValueError("Unexpected staging account or region")
    run = value.get("run_id", "")
    if not re.fullmatch(r"[a-z0-9-]{8,48}", run):
        raise ValueError("Invalid run identifier")
    if value.get("deploy_bucket") != BUCKET:
        raise ValueError("Unexpected deployment bucket")
    for key in ("bundle_sha256", "source_sha"):
        if not re.fullmatch(r"[0-9a-f]{64}" if key == "bundle_sha256" else r"[0-9a-f]{40}", value.get(key, "")):
            raise ValueError("Invalid immutable source identity")
    if value.get("bundle_key") != f"staging/bundles/{value['bundle_sha256']}.tar.gz":
        raise ValueError("Bundle key does not match digest")
    deadline = dt.datetime.fromisoformat(value["expires_at"])
    if deadline.tzinfo is None:
        raise ValueError("Expiry must include a timezone")
    minutes = value.get("max_duration_minutes", 0)
    if type(minutes) is not int or not 15 <= minutes <= 5760:
        raise ValueError("Maximum lifetime must be 15..5760 minutes")
    created = dt.datetime.fromisoformat(value["created_at"])
    if created.tzinfo is None:
        raise ValueError("Creation time must include a timezone")
    lifetime = deadline - created
    if value.get("expiry_mode", "relative") == "absolute":
        matches = (dt.timedelta(minutes=15) <= lifetime <= dt.timedelta(hours=96)
                   and math.ceil(lifetime.total_seconds() / 60) == minutes)
    elif value.get("expiry_mode", "relative") == "relative":
        matches = lifetime == dt.timedelta(minutes=minutes)
    else:
        matches = False
    if not matches:
        raise ValueError("Expiry does not match the requested maximum lifetime")
    if set(value.get("terraform", {})) != {"ami_id", "monitoring_security_group_id", "shared_alb_security_group_id"}:
        raise ValueError("Only approved non-secret Terraform inputs are accepted")
    if not re.fullmatch(r"https://[a-z0-9-]+\.cloudfront\.net", value.get("public_origin", "")):
        raise ValueError("An exact existing CloudFront HTTPS origin is required")
    for hook in ("prepare", "collect", "detach"):
        path = value.get("hooks", {}).get(hook, "")
        if not re.fullmatch(r"scripts/[a-zA-Z0-9_/-]+\.py", path) or ".." in path.split("/"):
            raise ValueError("Immutable bundle integration hooks are required")
    return value


def expiry_fields(now, *, max_duration=None, expires_at=None):
    """Resolve a bounded lifetime while preserving an explicit wall-clock deadline."""
    if (max_duration is None) == (expires_at is None):
        raise ValueError("Specify exactly one of --max-duration or --expires-at")
    if expires_at is not None:
        try:
            deadline = dt.datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            raise ValueError("--expires-at must be an ISO 8601 time with a timezone") from None
        if deadline.tzinfo is None or deadline.utcoffset() is None:
            raise ValueError("--expires-at must include a timezone, for example +09:00")
        lifetime = deadline - now
        if not dt.timedelta(minutes=15) <= lifetime <= dt.timedelta(hours=96):
            raise ValueError("Expiry must be 15 minutes..96 hours after creation")
        return dict(expires_at=deadline.isoformat(), expiry_mode="absolute",
                    max_duration_minutes=math.ceil(lifetime.total_seconds() / 60))
    if type(max_duration) is not int or not 15 <= max_duration <= 5760:
        raise ValueError("--max-duration must be 15..5760 minutes")
    return dict(expires_at=(now + dt.timedelta(minutes=max_duration)).isoformat(),
                max_duration_minutes=max_duration)


def verify_bundle(content, expected):
    if hashlib.sha256(content).hexdigest() != expected:
        raise ValueError("Source bundle SHA256 mismatch")


def check_owner(lock, manifest):
    if lock.get("run_id") != manifest["run_id"] or lock.get("bundle_sha256") != manifest["bundle_sha256"]:
        raise RuntimeError("Staging singleton lock belongs to another run")


def managed_resources(state):
    def resources(module):
        yield from module.get("resources", [])
        for child in module.get("child_modules", []):
            yield from resources(child)
    return (r for r in resources(state.get("values", {}).get("root_module", {})) if r.get("mode") == "managed")


def check_state_owner(state, run):
    """A stale singleton must never authorize changing another run's Terraform State."""
    values = state.get('values', {})
    recorded = values.get('outputs', {}).get('run_id', {}).get('value')
    if recorded is not None and recorded != run:
        raise RuntimeError('Terraform State belongs to another staging run')
    managed = list(managed_resources(state))
    owned = recorded == run
    for resource in managed:
        attributes = resource.get('values', {})
        tags = attributes.get('tags_all') or attributes.get('tags') or {}
        if tags.get('Environment') not in (None, 'staging') or tags.get('RunId') not in (None, run):
            raise RuntimeError('Terraform State contains foreign resources')
        owned = owned or tags.get('RunId') == run
    if managed and not owned:
        raise RuntimeError('Terraform State resource ownership is unproven')


def cleanup_parameter_names(names):
    """Temporary app and SQL access settings belong to this workload; UI auth survives."""
    return [n for n in names if n.startswith(("/getddo/staging/app/", "/getddo/staging/test-access/"))
            and n not in PERSISTENT_PARAMETERS]


def cleanup(effects, manifest, reason):
    """Normal result failure pauses; expiry/rollback prioritize bounded deletion."""
    check_owner(effects.owner(), manifest)
    effects.quiesce(manifest)  # Prevent an in-flight up from recreating resources.
    collection_error = None
    try:
        effects.collect(manifest, timeout=360)
    except Exception as error:
        collection_error = type(error).__name__
        effects.record(manifest, "collection_failed", detail=collection_error)
        if reason == "destroy":
            raise RuntimeError("Results could not be saved; normal destroy paused") from error
    detach_error = None
    try:
        effects.detach(manifest)
    except Exception as error:
        detach_error = type(error).__name__
        effects.record(manifest, "detach_failed", detail=detach_error)
        if reason == "destroy":
            raise
    effects.destroy(manifest)
    effects.delete_deployment_lock(manifest)
    effects.delete_app_parameters(manifest)
    remaining = effects.wait_deleted(manifest)
    effects.verify_credentials(manifest)
    if remaining or detach_error:
        effects.record(manifest, "cleanup_failed", remaining=remaining, detach_error=detach_error)
        raise RuntimeError("Staging resources remain after destroy")
    effects.record(manifest, "destroyed", collection_error=collection_error)
    effects.release(manifest)


def provision(effects, manifest):
    check_owner(effects.owner(), manifest)
    effects.verify_guard(manifest)
    try:
        effects.record(manifest, "provisioning")
        effects.apply(manifest)
        effects.prepare(manifest)  # Installs app/idle Locust; never starts a test.
        effects.verify_guard(manifest)
        effects.record(manifest, "ready")
    except Exception:
        # Original failure is retained even when rollback itself fails.
        try:
            cleanup(effects, manifest, "rollback")
        except Exception as rollback:
            effects.record(manifest, "rollback_failed", detail=type(rollback).__name__)
        raise


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
