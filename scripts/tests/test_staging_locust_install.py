import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import subprocess
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from staging_locust_install import StageRuntimeError, install
from test_staging_lifecycle import manifest


class LocustInstallTests(unittest.TestCase):
    def runtime_install(self, results):
        value = manifest()
        value["deployment"] = {"db_endpoint": "getddo-staging-mysql.fixture.ap-northeast-2.rds.amazonaws.com", "api_url": "http://internal-stage.ap-northeast-2.elb.amazonaws.com",
                               "locust_instance": {"private_ip": "10.20.0.42"}}
        actor = {"user_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "role": "USER", "membership": "excellent"}
        fetch = Mock(return_value={"admin-user": "SECRET_ADMIN", "admin-password": "SECRET_PASSWORD",
                                   "session-secret": "SECRET_SESSION" * 4})
        with tempfile.TemporaryDirectory() as temporary, patch('staging_locust_install.subprocess.run', side_effect=results) as run:
            root = Path(temporary)
            install(value, actor, root, root=root, fetch=fetch)
            return run.call_args_list

    @staticmethod
    def command_result(code=0, output=b''):
        return subprocess.CompletedProcess([], code, stdout=output, stderr=b'SECRET_PASSWORD SECRET_SESSION scrypt:SECRET_HASH')

    def test_supported_builder_starts_only_idle_master_and_two_workers(self):
        calls = self.runtime_install([self.command_result(), self.command_result(output=b'github.com/docker/buildx v0.29.1 commit'),
                                      self.command_result(), self.command_result()])
        self.assertEqual(calls[0].args[0], ['docker', 'info'])
        self.assertEqual(calls[1].args[0], ['docker', 'buildx', 'version'])
        self.assertEqual(calls[2].args[0][-2:], ['config', '--quiet'])
        self.assertEqual(calls[3].args[0][-4:], ['up', '-d', '--scale', 'worker=2'])
        self.assertEqual(calls[3].kwargs['env']['LOCUST_BIND_IP'], '10.20.0.42')
        self.assertEqual(calls[3].kwargs['timeout'], 900)

    def test_runtime_failures_are_bounded_codes_without_command_output(self):
        ok = self.command_result
        builder = ok(output=b'github.com/docker/buildx v0.29.1 commit')
        cases = [
            ([ok(1)], 'docker', 'failed'),
            ([FileNotFoundError('SECRET_PASSWORD')], 'docker', 'unavailable'),
            ([ok(), ok(1, b'SECRET_PASSWORD: buildx is not a docker command')], 'builder', 'failed'),
            ([ok(), ok(output=b'github.com/docker/buildx 0.12.1 commit')], 'builder', 'incompatible'),
            ([ok(), ok(output=b'SECRET_SESSION')], 'builder', 'incompatible'),
            ([ok(), builder, ok(1)], 'compose_config', 'failed'),
            ([ok(), builder, ok(), ok(1)], 'compose_up', 'failed'),
            ([ok(), builder, ok(), subprocess.TimeoutExpired(['SECRET_PASSWORD'], 900, output=b'SECRET_SESSION')],
             'compose_up', 'timeout'),
        ]
        for results, stage, reason in cases:
            with self.subTest(stage=stage, reason=reason), self.assertRaises(StageRuntimeError) as caught:
                self.runtime_install(results)
            self.assertEqual(str(caught.exception), f'STAGE_RUNTIME_FAILED stage={stage} reason={reason}')
            self.assertNotIn('SECRET', str(caught.exception))
            self.assertIsNone(caught.exception.__cause__)

    def test_credentials_are_private_and_idle_install_does_not_start_load(self):
        value = manifest()
        value["deployment"] = {"db_endpoint": "getddo-staging-mysql.fixture.ap-northeast-2.rds.amazonaws.com", "api_url": "http://internal-stage.ap-northeast-2.elb.amazonaws.com",
                               "locust_instance": {"private_ip": "10.20.0.42"}}
        actor = {"user_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "role": "USER", "membership": "excellent"}
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
            saved = json.loads((root / "run/getddo-staging/locust-scenario/actor.json").read_text())
            self.assertEqual(saved["membership"], "excellent")
        fetch.assert_called_once()

    def test_non_synthetic_actor_is_rejected_before_reading_credentials(self):
        fetch = Mock()
        with self.assertRaises(ValueError):
            install(manifest(), {"user_id": "prod", "role": "ADMIN"}, Path("/tmp"), fetch=fetch, start=False)
        fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
