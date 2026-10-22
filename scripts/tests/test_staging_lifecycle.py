import copy
import datetime as dt
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from staging_lifecycle import (ACCOUNT, BUCKET, REGION, PERSISTENT_PARAMETERS, check_owner,
                               cleanup, cleanup_parameter_names, provision, validate_manifest, verify_bundle)


def manifest():
    now = dt.datetime.now(dt.timezone.utc)
    return dict(account_id=ACCOUNT, region=REGION, deploy_bucket=BUCKET, run_id="a" * 32,
                bundle_sha256="b" * 64, source_sha="c" * 40, bundle_key="staging/bundles/" + "b" * 64 + ".tar.gz",
                created_at=now.isoformat(), expires_at=(now + dt.timedelta(hours=1)).isoformat(), max_duration_minutes=60,
                terraform={"ami_id": "ami-0123456789abcdef0", "monitoring_security_group_id": "sg-monitor", "shared_alb_security_group_id": "sg-alb"},
                public_origin="https://example.cloudfront.net", hooks={name: "scripts/staging_hooks.py" for name in ("prepare", "collect", "detach", "run", "stop")})


class Fake:
    def __init__(self, fail=None, residual=None):
        self.calls = []
        self.fail = fail
        self.remaining = residual or []
        self.lock = manifest()

    def owner(self):
        return self.lock

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.calls.append(name)
            if name == self.fail:
                raise RuntimeError("injected failure")
            if name == "wait_deleted":
                return self.remaining
        return call


class LifecycleTests(unittest.TestCase):
    def test_no_workload_without_verified_guard(self):
        effects = Fake("verify_guard")
        with self.assertRaises(RuntimeError):
            provision(effects, manifest())
        self.assertNotIn("apply", effects.calls)

    def test_apply_failure_rolls_back_and_propagates(self):
        effects = Fake("apply")
        with self.assertRaises(RuntimeError):
            provision(effects, manifest())
        self.assertIn("destroy", effects.calls)
        self.assertIn("wait_deleted", effects.calls)
        self.assertIn("release", effects.calls)

    def test_prepare_failure_rolls_back_partial_resources(self):
        effects = Fake("prepare")
        with self.assertRaises(RuntimeError):
            provision(effects, manifest())
        self.assertIn("destroy", effects.calls)
        self.assertNotIn("run", effects.calls)

    def test_ready_does_not_start_load(self):
        effects = Fake()
        provision(effects, manifest())
        self.assertEqual(effects.calls, ["verify_guard", "record", "apply", "prepare", "verify_guard", "record"])

    def test_destroy_collection_failure_pauses_and_retains_lock(self):
        effects = Fake("collect")
        with self.assertRaisesRegex(RuntimeError, "paused"):
            cleanup(effects, manifest(), "destroy")
        self.assertNotIn("destroy", effects.calls)
        self.assertNotIn("release", effects.calls)

    def test_ttl_collection_failure_still_deletes(self):
        effects = Fake("collect")
        cleanup(effects, manifest(), "ttl")
        self.assertIn("destroy", effects.calls)
        self.assertLess(effects.calls.index("collect"), effects.calls.index("destroy"))
        self.assertIn("release", effects.calls)

    def test_ttl_detach_failure_still_deletes_but_retains_lock(self):
        effects = Fake("detach")
        with self.assertRaises(RuntimeError):
            cleanup(effects, manifest(), "ttl")
        self.assertIn("destroy", effects.calls)
        self.assertNotIn("release", effects.calls)

    def test_expiry_cannot_be_extended_in_manifest(self):
        value = manifest()
        value["expires_at"] = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=2)).isoformat()
        with self.assertRaises(ValueError):
            validate_manifest(value)

    def test_secret_terraform_config_rejected(self):
        value = manifest()
        value["terraform"]["db_master_password"] = "must-not-persist"
        with self.assertRaises(ValueError):
            validate_manifest(value)

    def test_deletion_failure_never_reports_success(self):
        effects = Fake("destroy")
        with self.assertRaises(RuntimeError):
            cleanup(effects, manifest(), "ttl")
        self.assertNotIn("release", effects.calls)

    def test_residual_detection_retains_ownership(self):
        effects = Fake(residual=["i-still-running"])
        with self.assertRaisesRegex(RuntimeError, "remain"):
            cleanup(effects, manifest(), "ttl")
        self.assertNotIn("release", effects.calls)

    def test_no_cross_run_cleanup(self):
        effects = Fake()
        effects.lock["run_id"] = "d" * 32
        with self.assertRaisesRegex(RuntimeError, "another run"):
            cleanup(effects, manifest(), "ttl")
        self.assertEqual(effects.calls, [])

    def test_credential_preservation(self):
        names = [*PERSISTENT_PARAMETERS, "/getddo/staging/app/DB_PASSWORD", "/getddo/dev/app/password"]
        self.assertEqual(cleanup_parameter_names(names), ["/getddo/staging/app/DB_PASSWORD"])

    def test_changed_persistent_credentials_never_release_ownership(self):
        effects = Fake("verify_credentials")
        with self.assertRaises(RuntimeError):
            cleanup(effects, manifest(), "destroy")
        self.assertIn("destroy", effects.calls)
        self.assertNotIn("release", effects.calls)

    def test_manifest_account_and_source_ownership(self):
        value = manifest()
        self.assertEqual(validate_manifest(value), value)
        for key, replacement in (("account_id", "123456789012"), ("region", "us-east-1"),
                                 ("deploy_bucket", "prod"), ("bundle_key", "releases/latest.tar.gz"),
                                 ("source_sha", "main"), ("max_duration_minutes", 0)):
            changed = copy.deepcopy(value)
            changed[key] = replacement
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_manifest(changed)

    def test_bundle_tampering_rejected(self):
        with self.assertRaisesRegex(ValueError, "mismatch"):
            verify_bundle(b"changed", "0" * 64)


if __name__ == "__main__":
    unittest.main()
