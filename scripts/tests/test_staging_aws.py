import io
import json
from pathlib import Path
import sys
import tempfile
import tarfile
import shutil
import subprocess
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from staging_aws import AWS, BUCKET, Effects, LOCK, terraform_stdin
from test_staging_lifecycle import manifest


class AdapterTests(unittest.TestCase):
    def effects(self):
        effects = object.__new__(Effects)
        effects.root = Path(tempfile.gettempdir())
        effects.clients = {name: Mock() for name in ("s3", "ssm", "codebuild", "scheduler")}
        return effects

    def test_secret_inputs_use_private_stdin_not_arguments_or_disk(self):
        effects = self.effects()
        value = manifest()
        value["terraform"] = {"ami_id": "ami-test"}
        effects.clients["ssm"].get_parameter.return_value = {"Parameter": {"Value": "secret-test-value"}}
        calls = []
        def command(argv, **kwargs):
            calls.append(argv)
            if "show" in argv:
                return b'{}'
            if "apply" in argv:
                self.assertIn(b'db_master_password = "secret-test-value"', kwargs["data"])
                self.assertNotIn("secret-test-value", str(argv))
                self.assertEqual(argv[-1], "-var-file=/dev/stdin")
        effects.command = command
        effects.terraform(value, "apply")
        self.assertIn("-lockfile=readonly", calls[0])

    def test_generated_credentials_match_mysql_and_redis_service_constraints(self):
        effects = self.effects()
        deployment = {'account_id': '942466928325', 'region': 'ap-northeast-2',
                      'db_endpoint': 'getddo-staging-mysql.example.ap-northeast-2.rds.amazonaws.com',
                      'redis_endpoint': 'getddo-staging-redis.example.cache.amazonaws.com'}
        effects.command = Mock(return_value=json.dumps(deployment).encode())
        effects.terraform = Mock()
        effects.record = Mock()
        effects.app_parameter = Mock()
        value = manifest()
        effects.apply(value)
        generated = {call.args[1]: call.args[2] for call in effects.app_parameter.call_args_list if call.kwargs.get('secret')}
        password = generated['SPRING_DATASOURCE_PASSWORD']
        self.assertGreaterEqual(len(password), 8)
        self.assertLessEqual(len(password), 41)
        self.assertRegex(password, r'^[A-Za-z0-9]+$')
        token = generated['SPRING_DATA_REDIS_PASSWORD']
        self.assertGreaterEqual(len(token), 16)
        self.assertLessEqual(len(token), 128)
        self.assertNotEqual(password, token)

    def test_foreign_state_prevents_apply_and_destroy(self):
        for action in ('apply', 'destroy'):
            with self.subTest(action=action):
                effects = self.effects()
                effects.clients['ssm'].get_parameter.return_value = {'Parameter':{'Value':'test-secret'}}
                foreign = {'values':{'outputs':{'run_id':{'value':'another-run'}}}}
                effects.command = Mock(side_effect=[b'', json.dumps(foreign).encode()])
                with self.assertRaisesRegex(RuntimeError, 'another staging run'):
                    effects.terraform(manifest(), action)
                self.assertEqual(len(effects.command.call_args_list), 2)
                self.assertFalse(any(action in call.args[0] for call in effects.command.call_args_list))

    def test_destroy_never_reads_passwords(self):
        effects = self.effects()
        effects.command = Mock(return_value=b'{}')
        effects.terraform(manifest(), "destroy")
        effects.clients["ssm"].get_parameter.assert_not_called()

    @unittest.skipUnless(shutil.which("terraform"), "Terraform is needed to verify its stdin parser")
    def test_terraform_accepts_private_stdin_and_literal_template_characters(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "main.tf").write_text('variable "check_value" { type = string }\n')
            result = subprocess.run(["terraform", "plan", "-input=false", "-no-color", "-var-file=/dev/stdin"],
                cwd=root, input=terraform_stdin({"check_value": 'test-${literal}-%{text}-"quoted"'}),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertEqual(result.returncode, 0, result.stderr.decode())

    def test_output_from_failed_secret_bearing_command_is_not_echoed(self):
        effects = self.effects()
        result = Mock(returncode=1, stdout=b"secret-output", stderr=b"secret-error")
        with patch("staging_aws.subprocess.run", return_value=result), self.assertRaises(RuntimeError) as error:
            effects.command(["terraform", "apply"])
        self.assertNotIn("secret", str(error.exception))

    def test_collection_receipt_requires_run_prefix_and_digest_readback(self):
        effects = self.effects()
        effects.hook = Mock(return_value=b'{"key":"staging/other/results/report","sha256":"bad"}')
        with self.assertRaisesRegex(RuntimeError, "outside"):
            effects.collect(manifest(), 180)
        effects.clients["s3"].get_object.assert_not_called()
        value = manifest()
        effects.hook.return_value = json.dumps({"key": f"staging/runs/{value['run_id']}/results/report", "sha256": "0" * 64}).encode()
        effects.clients["s3"].get_object.return_value = {"Body": io.BytesIO(b"tampered")}
        with self.assertRaisesRegex(ValueError, "mismatch"):
            effects.collect(value, 180)

    def test_lock_release_uses_matching_owner_and_conditional_delete(self):
        effects = self.effects()
        effects.get = Mock(return_value=(manifest(), '"etag"'))
        effects.release(manifest())
        effects.clients["s3"].delete_object.assert_called_once_with(Bucket=BUCKET, Key=LOCK, IfMatch='"etag"')
        effects.get.return_value = ({"run_id": "another-run"}, '"new-etag"')
        with self.assertRaises(RuntimeError):
            effects.release(manifest())
        self.assertEqual(effects.clients["s3"].delete_object.call_count, 1)

    def test_app_parameter_collision_cannot_overwrite_another_run(self):
        effects = self.effects()
        class Exists(Exception):
            pass
        ssm = effects.clients["ssm"]
        ssm.exceptions.ParameterAlreadyExists = Exists
        ssm.put_parameter.side_effect = Exists
        ssm.list_tags_for_resource.return_value = {"TagList": [{"Key": "RunId", "Value": "another-run"}]}
        with self.assertRaisesRegex(RuntimeError, "another run"):
            effects.app_parameter(manifest(), "SPRING_DATASOURCE_PASSWORD", "secret", secret=True)
        self.assertEqual(ssm.put_parameter.call_count, 1)

    def test_repeated_actions_have_fresh_request_tokens(self):
        effects = self.effects()
        effects.clients["codebuild"].start_build.return_value = {"build": {"id": "build"}}
        effects.start(manifest(), "stop")
        effects.start(manifest(), "stop")
        calls = effects.clients["codebuild"].start_build.call_args_list
        self.assertNotEqual(calls[0].kwargs["idempotencyToken"], calls[1].kwargs["idempotencyToken"])

    def test_destroy_allows_retained_data_lookups_but_rejects_managed_state(self):
        effects = self.effects()
        effects.terraform = Mock()
        effects.command = Mock(return_value=b'{"values":{"root_module":{"resources":[{"mode":"data"}]}}}')
        effects.destroy(manifest())
        effects.command.return_value = b'{"values":{"root_module":{"resources":[{"mode":"managed"}]}}}'
        with self.assertRaises(RuntimeError):
            effects.destroy(manifest())

    def test_guard_rejects_wrong_deadline_role_or_target(self):
        effects = self.effects()
        from staging_aws import CLEANUP_PROJECT, SCHEDULE_ROLE
        value = manifest()
        import datetime as dt
        deadline = dt.datetime.fromisoformat(value["expires_at"])
        guard = {"State": "ENABLED", "ScheduleExpressionTimezone": "UTC", "FlexibleTimeWindow": {"Mode": "OFF"},
                 "ScheduleExpression": f"at({deadline.strftime('%Y-%m-%dT%H:%M:%S')})",
                 "Target": {"RoleArn": SCHEDULE_ROLE, "Arn": "arn:aws:scheduler:::aws-sdk:codebuild:startBuild",
                    "Input": json.dumps({"projectName": CLEANUP_PROJECT, "environmentVariablesOverride": [
                        {"name": "RUN_ID", "value": value["run_id"]}, {"name": "ACTION", "value": "ttl"}]})}}
        effects.clients["scheduler"].get_schedule.return_value = guard
        effects.verify_guard(value)
        for key in ("State", "ScheduleExpression", "ScheduleExpressionTimezone"):
            original = guard[key]
            guard[key] = "tampered"
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                effects.verify_guard(value)
            guard[key] = original
        guard["Target"]["RoleArn"] = "arn:aws:iam::942466928325:role/prod"
        with self.assertRaises(RuntimeError):
            effects.verify_guard(value)

    def test_residual_wait_is_bounded_and_allows_eventual_deletion(self):
        effects = self.effects()
        effects.residuals = Mock(side_effect=[["i-stopping"], []])
        with patch("staging_aws.time.sleep"):
            self.assertEqual(effects.wait_deleted(manifest()), [])
        effects.residuals = Mock(return_value=["i-stuck"])
        with patch("staging_aws.time.monotonic", side_effect=[0, 601]):
            self.assertEqual(effects.wait_deleted(manifest()), ["i-stuck"])

    def test_credential_metadata_does_not_read_values_and_detects_changes(self):
        effects = self.effects()
        from staging_lifecycle import PERSISTENT_PARAMETERS
        ssm = effects.clients["ssm"]
        names = sorted(PERSISTENT_PARAMETERS)
        ssm.get_paginator.return_value.paginate.side_effect = [
            [{"Parameters": [{"Name": name, "Type": "SecureString", "Version": 1}]}] for name in names]
        ssm.list_tags_for_resource.return_value = {"TagList": [{"Key": "Lifecycle", "Value": "persistent-configuration"}]}
        metadata = effects.credentials_metadata()
        self.assertEqual(set(metadata), PERSISTENT_PARAMETERS)
        ssm.get_parameter.assert_not_called()
        ssm.get_parameters.assert_not_called()
        effects.credentials_metadata = Mock(return_value={})
        with self.assertRaisesRegex(RuntimeError, "changed"):
            effects.verify_credentials(dict(manifest(), persistent_credentials=metadata))

    def test_archive_links_and_traversal_are_rejected_before_extracting(self):
        effects = self.effects()
        import hashlib
        for name, kind in (("../escape", tarfile.REGTYPE), ("linked", tarfile.SYMTYPE)):
            content = io.BytesIO()
            with tarfile.open(fileobj=content, mode="w:gz") as archive:
                entry = tarfile.TarInfo(name)
                entry.type = kind
                entry.linkname = "/etc/passwd" if kind == tarfile.SYMTYPE else ""
                archive.addfile(entry)
            value = dict(manifest(), bundle_sha256=hashlib.sha256(content.getvalue()).hexdigest())
            effects.clients["s3"].get_object.return_value = {"Body": io.BytesIO(content.getvalue())}
            with tempfile.TemporaryDirectory() as directory, self.assertRaisesRegex(ValueError, "Unsafe"):
                effects.extract(value, Path(directory))

    def test_app_cleanup_never_touches_persistent_credentials(self):
        effects = self.effects()
        ssm = effects.clients["ssm"]
        value = manifest()
        app = "/getddo/staging/app/SPRING_DATASOURCE_PASSWORD"
        ssm.get_paginator.return_value.paginate.return_value = [{"Parameters": [
            {"Name": app}, {"Name": "/getddo/staging/locust/admin-password"}]}]
        ssm.list_tags_for_resource.return_value = {"TagList": [{"Key": "RunId", "Value": value["run_id"]}]}
        effects.delete_app_parameters(value)
        ssm.delete_parameter.assert_called_once_with(Name=app)

    def test_missing_deployment_lock_does_not_require_whole_bucket_metadata(self):
        effects = self.effects()
        effects.clients['s3'].list_objects_v2.return_value = {'Contents': []}
        effects.delete_deployment_lock(manifest())
        effects.clients['s3'].head_object.assert_not_called()
        effects.clients['s3'].delete_object.assert_not_called()

    def test_deployment_lock_cleanup_is_run_owned_and_conditional(self):
        effects = self.effects()
        s3 = effects.clients["s3"]
        value = manifest()
        s3.list_objects_v2.return_value = {"Contents": [{"Key":"staging/locks/app.json"}]}
        s3.head_object.return_value = {"Metadata": {"run-id": value["run_id"]}, "ETag": '"lock-etag"'}
        effects.delete_deployment_lock(value)
        s3.delete_object.assert_called_once_with(Bucket=BUCKET, Key="staging/locks/app.json", IfMatch='"lock-etag"')
        s3.head_object.return_value["Metadata"]["run-id"] = "another-run"
        with self.assertRaisesRegex(RuntimeError, "another run"):
            effects.delete_deployment_lock(value)
        self.assertEqual(s3.delete_object.call_count, 1)
        from botocore.exceptions import ClientError
        s3.head_object.side_effect = ClientError({"Error": {"Code": "404"}}, "HeadObject")
        effects.delete_deployment_lock(value)
        s3.head_object.side_effect = ClientError({"Error": {"Code": "AccessDenied"}}, "HeadObject")
        with self.assertRaises(ClientError):
            effects.delete_deployment_lock(value)


if __name__ == "__main__":
    unittest.main()
