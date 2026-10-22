import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from staging_locust_install import install
from test_staging_lifecycle import manifest


class LocustInstallTests(unittest.TestCase):
    def test_credentials_are_private_and_idle_install_does_not_start_load(self):
        value = manifest()
        value["deployment"] = {"api_url": "http://internal-stage.ap-northeast-2.elb.amazonaws.com",
                               "locust_instance": {"private_ip": "10.20.0.42"}}
        actor = {"user_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "role": "USER"}
        fetch = Mock(return_value={"admin-user": "locust-admin", "admin-password": "test-password",
                                   "session-secret": "s" * 48})
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            install(value, actor, root, root=root, fetch=fetch, start=False)
            secret = root / "run/getddo-staging/locust/admin-password-hash"
            self.assertEqual(os.stat(secret).st_mode & 0o777, 0o600)
            self.assertNotIn("test-password", secret.read_text())
            from werkzeug.security import check_password_hash
            self.assertTrue(check_password_hash(secret.read_text(), "test-password"))
            config = json.loads((root / "etc/getddo-loadtest/manifest.json").read_text())
            self.assertEqual(config["scenario"], "smoke_events")
            self.assertEqual(config["expected_workers"], 2)
        fetch.assert_called_once()

    def test_non_synthetic_actor_is_rejected_before_reading_credentials(self):
        fetch = Mock()
        with self.assertRaises(ValueError):
            install(manifest(), {"user_id": "prod", "role": "ADMIN"}, Path("/tmp"), fetch=fetch, start=False)
        fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
