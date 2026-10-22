import datetime as dt
import io
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

from botocore.exceptions import ClientError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from staging_aws import Effects, STATE_BUCKET, STATE_KEY, UP_PROJECT, CommandFailure
from staging_cleanup import Orphans, VPC, SHARED_GROUPS
from test_staging_lifecycle import manifest


def error(code):
    return ClientError({"Error": {"Code": code}}, "Test")


def tagged(run):
    return [{"Key": "Environment", "Value": "staging"}, {"Key": "RunId", "Value": run}]


class LockTests(unittest.TestCase):
    def setUp(self):
        self.value = manifest()
        self.now = dt.datetime.now(dt.timezone.utc)
        self.identity = UP_PROJECT + ":trusted-id"
        self.build = {"id": self.identity, "projectName": UP_PROJECT, "buildStatus": "STOPPED", "buildComplete": True,
                      "startTime": self.now - dt.timedelta(minutes=2), "endTime": self.now + dt.timedelta(minutes=1),
                      "environment": {"environmentVariables": [{"name": "RUN_ID", "value": self.value["run_id"]}]}}
        self.value["terraform_executions"] = [{"build_id": self.identity, "run_id": self.value["run_id"],
            "who": "root@unique-host", "state_path": STATE_BUCKET + "/" + STATE_KEY, "action": "apply",
            "started_at": (self.now - dt.timedelta(minutes=1)).isoformat()}]
        self.lock = {"ID": "lock-id", "Who": "root@unique-host", "Path": STATE_BUCKET + "/" + STATE_KEY,
                     "Created": self.now.isoformat(), "Operation": "OperationTypeApply"}
        self.effects = object.__new__(Effects)
        self.effects.clients = {name: Mock() for name in ("s3", "codebuild")}
        self.effects.owner = Mock(return_value=dict(self.value))
        self.effects.clients["codebuild"].batch_get_builds.return_value = {"builds": [self.build]}
        self.effects.clients["s3"].exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})
        self.effects.clients["s3"].get_object.side_effect = self.get
        self.raw = {}

    def get(self, **kwargs):
        self.assertEqual(kwargs["Bucket"], STATE_BUCKET)
        if kwargs["Key"] == STATE_KEY:
            return {"Body": io.BytesIO(json.dumps(self.raw).encode())}
        self.assertEqual(kwargs["Key"], STATE_KEY + ".tflock")
        return {"Body": io.BytesIO(json.dumps(self.lock).encode()), "ETag": '"old-lock"'}

    def test_stale_lock_exact_identity_and_conditional_delete(self):
        self.effects.recover_terraform_lock(self.value)
        self.effects.clients["s3"].delete_object.assert_called_once_with(
            Bucket=STATE_BUCKET, Key=STATE_KEY + ".tflock", IfMatch='"old-lock"')

    def test_live_foreign_and_unproven_locks_never_unlock(self):
        for field, value in (("Who", "root@other-host"), ("Path", STATE_BUCKET + "/prod/terraform.tfstate"),
                             ("Created", (self.now - dt.timedelta(days=1)).isoformat()), ("ID", "")):
            original = self.lock[field]
            self.lock[field] = value
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                self.effects.recover_terraform_lock(self.value)
            self.lock[field] = original
        for status, complete in (("IN_PROGRESS", False), ("STOPPED", False)):
            self.build.update(buildStatus=status, buildComplete=complete)
            with self.subTest(status=status), self.assertRaises(RuntimeError):
                self.effects.recover_terraform_lock(self.value)
        self.effects.clients["s3"].delete_object.assert_not_called()

    def test_foreign_build_or_state_prevents_unlock(self):
        self.build["environment"]["environmentVariables"][0]["value"] = "other-run"
        with self.assertRaisesRegex(RuntimeError, "another run"):
            self.effects.recover_terraform_lock(self.value)
        self.build["environment"]["environmentVariables"][0]["value"] = self.value["run_id"]
        for raw in ({"outputs": {"run_id": {"value": "other-run"}}},
                    {"resources": [{"mode": "managed", "instances": [{"attributes": {}}]}]}):
            self.raw = raw
            with self.assertRaises(RuntimeError):
                self.effects.recover_terraform_lock(self.value)
        self.effects.clients["s3"].delete_object.assert_not_called()

    def test_missing_lock_allowed_access_denied_and_replacement_propagate(self):
        missing = self.effects.clients["s3"].exceptions.NoSuchKey
        self.effects.clients["s3"].get_object.side_effect = missing
        self.effects.recover_terraform_lock(self.value)
        self.effects.clients["s3"].get_object.side_effect = error("AccessDenied")
        with self.assertRaises(ClientError):
            self.effects.recover_terraform_lock(self.value)
        self.effects.clients["s3"].get_object.side_effect = self.get
        self.effects.clients["s3"].delete_object.side_effect = error("PreconditionFailed")
        with self.assertRaises(ClientError):
            self.effects.recover_terraform_lock(self.value)

    def test_record_is_written_before_any_terraform_subprocess(self):
        self.build.update(buildStatus="IN_PROGRESS", buildComplete=False)
        self.effects.record = Mock()
        self.effects.command = Mock(return_value=b'{}')
        with patch.dict(os.environ, CODEBUILD_BUILD_ID=self.identity):
            self.effects.terraform(self.value, "destroy")
        execution = self.value["terraform_executions"][-1]
        self.assertEqual(execution["build_id"], self.identity)
        self.assertIn("@", execution["who"])
        self.effects.record.assert_called_once()
        # A failed manifest write prevents every Terraform subprocess.
        self.effects.command.reset_mock()
        self.effects.record.side_effect = RuntimeError("manifest failed")
        with patch.dict(os.environ, CODEBUILD_BUILD_ID=self.identity), self.assertRaises(RuntimeError):
            self.effects.terraform(self.value, "destroy")
        self.effects.command.assert_not_called()

    def test_quiesce_waits_past_stopped_status_then_reloads_latest_manifest(self):
        cb = self.effects.clients["codebuild"]
        cb.get_paginator.return_value.paginate.return_value = [{"ids": [self.identity]}]
        pending = dict(self.build, buildStatus="IN_PROGRESS", buildComplete=False)
        stopping = dict(self.build, buildStatus="STOPPED", buildComplete=False)
        cb.batch_get_builds.side_effect = [{"builds": [b]} for b in (pending, pending, stopping, self.build)]
        self.effects.manifest = Mock(return_value=dict(self.value, phase="infrastructure_ready"))
        self.effects.recover_terraform_lock = Mock()
        with patch("staging_aws.time.sleep") as sleep:
            self.effects.quiesce(self.value)
        sleep.assert_called_once_with(5)
        cb.stop_build.assert_called_once_with(id=self.identity)
        self.assertEqual(self.value["phase"], "infrastructure_ready")
        self.effects.recover_terraform_lock.assert_called_once_with(self.value)

    def test_unknown_build_and_stop_timeout_never_recover_lock(self):
        cb = self.effects.clients["codebuild"]
        cb.get_paginator.return_value.paginate.return_value = [{"ids": [self.identity]}]
        cb.batch_get_builds.return_value = {"builds": []}
        self.effects.recover_terraform_lock = Mock()
        with self.assertRaises(RuntimeError):
            self.effects.quiesce(self.value)
        cb.batch_get_builds.return_value = {"builds": [dict(self.build, buildComplete=False)]}
        with patch("staging_aws.time.monotonic", side_effect=[0, 181]), self.assertRaises(RuntimeError):
            self.effects.quiesce(self.value)
        self.effects.recover_terraform_lock.assert_not_called()


class CleanupFixture(unittest.TestCase):
    def setUp(self):
        self.run = manifest()["run_id"]
        self.clients = {s: Mock() for s in ("ec2", "rds", "elasticache", "elbv2", "iam", "logs", "ssm")}
        self.data = {}
        for name, client in self.clients.items():
            client.get_paginator.side_effect = lambda operation, service=name: Mock(
                paginate=lambda **kwargs: self.data.get((service, operation), [{}]))
        missing = type("NoEntity", (Exception,), {})
        self.clients["iam"].exceptions.NoSuchEntityException = missing
        self.clients["iam"].get_role.side_effect = missing
        self.clients["iam"].get_instance_profile.side_effect = missing
        self.cleaner = Orphans(self.clients, self.run)


class CleanupTests(CleanupFixture):
    def test_empty_inventory_and_repeated_cleanup_never_touch_persistent_controls(self):
        self.assertEqual(self.cleaner.inventory(), [])
        self.cleaner.sweep()
        self.cleaner.sweep()
        self.clients["iam"].delete_role.assert_not_called()
        self.clients["ssm"].delete_parameter.assert_not_called()
        self.clients["logs"].delete_log_group.assert_not_called()
        for call in self.clients["iam"].get_role.call_args_list:
            self.assertIn(call.kwargs["RoleName"], ("getddo-staging-api", "getddo-staging-locust"))

    def test_untracked_instance_discovery_requires_tags_vpc_and_termination(self):
        instance = {"InstanceId": "i-orphan", "VpcId": VPC, "State": {"Name": "running"}, "Tags": tagged(self.run)}
        self.data["ec2", "describe_instances"] = [{"Reservations": [{"Instances": [instance]}]}]
        resources = self.cleaner.inventory()
        self.assertEqual(resources, [("instance", "i-orphan", instance)])
        self.cleaner.delete(*resources[0])
        self.clients["ec2"].terminate_instances.assert_called_once_with(InstanceIds=["i-orphan"])
        instance["Tags"] = tagged("other-run")
        with self.assertRaisesRegex(RuntimeError, "ownership"):
            self.cleaner.inventory()
        instance["Tags"] = tagged(self.run)
        instance["VpcId"] = "vpc-other"
        with self.assertRaisesRegex(RuntimeError, "VPC"):
            self.cleaner.inventory()

    def test_names_alone_or_other_run_tags_never_authorize_database_delete(self):
        self.data["rds", "describe_db_instances"] = [{"DBInstances": [{"DBInstanceIdentifier": "getddo-staging-mysql", "DBInstanceArn": "db-arn"}]}]
        for actual in ([], tagged("other-run"), [{"Key": "RunId", "Value": self.run}]):
            self.clients["rds"].list_tags_for_resource.return_value = {"TagList": actual}
            with self.assertRaisesRegex(RuntimeError, "ownership"):
                self.cleaner.sweep()
        self.clients["rds"].delete_db_instance.assert_not_called()

    def test_shared_rule_deletes_only_exact_owned_rule_and_preserves_shared_group(self):
        rule = {"SecurityGroupRuleId": "sgr-owned", "GroupId": sorted(SHARED_GROUPS)[0], "IsEgress": True, "Tags": tagged(self.run)}
        self.data["ec2", "describe_security_group_rules"] = [{"SecurityGroupRules": [rule]}]
        self.cleaner.delete(*self.cleaner.inventory()[0])
        self.clients["ec2"].revoke_security_group_egress.assert_called_once_with(
            GroupId=rule["GroupId"], SecurityGroupRuleIds=["sgr-owned"])
        self.clients["ec2"].delete_security_group.assert_not_called()
        rule["GroupId"] = "sg-foreign"
        with self.assertRaisesRegex(RuntimeError, "unapproved parent"):
            self.cleaner.inventory()

    def test_ui_only_priority_30_with_both_tags(self):
        rules = [{"IsDefault": True, "Priority": "default"}, {"IsDefault": False, "Priority": "31", "RuleArn": "other"},
                 {"IsDefault": False, "Priority": "30", "RuleArn": "owned"}]
        self.data["elbv2", "describe_rules"] = [{"Rules": rules}]
        self.clients["elbv2"].describe_tags.return_value = {"TagDescriptions": [{"Tags": tagged(self.run)}]}
        self.assertEqual(self.cleaner.inventory()[0][:2], ("ui_rule", "owned"))
        self.clients["elbv2"].describe_tags.return_value["TagDescriptions"][0]["Tags"] = tagged("another-run")
        with self.assertRaises(RuntimeError):
            self.cleaner.inventory()

    def test_dependency_order_retry_absence_and_timeout(self):
        resources = [("sg", "sg-owned", {}), ("instance", "i-owned", {}), ("rule", "sgr-owned", {}),
                     ("profile", "profile", {}), ("role", "role", {}), ("db_subnet", "subnet", {}), ("db", "db", {})]
        self.cleaner.inventory = Mock(side_effect=[resources, resources, []])
        calls = []
        def delete(kind, identity, item):
            calls.append(kind)
            if len(calls) == 1:
                raise error("DependencyViolation")
        self.cleaner.delete = Mock(side_effect=delete)
        with patch("staging_cleanup.time.sleep"):
            self.cleaner.sweep()
        first = calls[:len(resources)]
        for before, after in (("instance", "sg"), ("rule", "sg"), ("db", "db_subnet"), ("profile", "role")):
            self.assertLess(first.index(before), first.index(after))
        self.cleaner.inventory = Mock(side_effect=[resources, []])
        self.cleaner.delete.side_effect = error("InvalidGroup.NotFound")
        with patch("staging_cleanup.time.sleep"):
            self.cleaner.sweep()
        self.cleaner.inventory = Mock(return_value=resources)
        with patch("staging_cleanup.time.monotonic", side_effect=[0, 1801]), self.assertRaisesRegex(RuntimeError, "remain"):
            self.cleaner.sweep()

    def test_access_denied_and_unexpected_delete_errors_propagate(self):
        self.cleaner.inventory = Mock(return_value=[("sg", "sg-owned", {})])
        self.clients["ec2"].delete_security_group.side_effect = error("AccessDenied")
        with self.assertRaises(ClientError):
            self.cleaner.sweep()

    def test_iam_profile_foreign_role_or_managed_policy_blocks_deletion(self):
        with self.assertRaisesRegex(RuntimeError, "foreign role"):
            self.cleaner.delete("profile", "getddo-staging-api", {"Roles": [{"RoleName": "getddo-staging-control"}]})
        self.clients["iam"].remove_role_from_instance_profile.assert_not_called()
        self.data["iam", "list_attached_role_policies"] = [{"AttachedPolicies": [{"PolicyArn": "unexpected"}]}]
        with self.assertRaisesRegex(RuntimeError, "unexpected"):
            self.cleaner.delete("role", "getddo-staging-api", {})
        self.clients["iam"].delete_role.assert_not_called()

    def test_parameter_discovery_reads_metadata_only_and_excludes_locust_auth(self):
        self.data["ssm", "describe_parameters"] = [{"Parameters": [{"Name": "/getddo/staging/locust/admin-password"},
                                                                       {"Name": "/getddo/staging/app/DB_PASSWORD"}]}]
        self.clients["ssm"].list_tags_for_resource.return_value = {"TagList": tagged(self.run)}
        resources = self.cleaner.inventory()
        self.assertEqual(len(resources), 1)
        self.cleaner.delete(*resources[0])
        self.clients["ssm"].delete_parameter.assert_called_once_with(Name="/getddo/staging/app/DB_PASSWORD")
        self.clients["ssm"].get_parameter.assert_not_called()
        self.clients["ssm"].get_parameters_by_path.assert_not_called()


class DestroyTests(unittest.TestCase):
    def effects(self):
        effects = object.__new__(Effects)
        effects.clients = {}
        effects.owner = Mock(return_value=manifest())
        effects.recover_terraform_lock = Mock()
        effects.check_remote_state = Mock(return_value=[])
        effects.record_execution = Mock()
        effects.command = Mock(return_value=b'{}')
        effects.terraform = Mock()
        return effects

    def test_failed_terraform_requires_sweep_then_successful_reconciliation(self):
        effects = self.effects()
        effects.terraform.side_effect = [CommandFailure("destroy failed"), None]
        with patch("staging_cleanup.Orphans") as cleaner:
            cleaner.return_value.inventory.return_value = []
            effects.destroy(manifest())
            cleaner.return_value.sweep.assert_called_once()
        self.assertEqual(effects.terraform.call_count, 2)
        effects.terraform.side_effect = CommandFailure("still failed")
        with patch("staging_cleanup.Orphans"), self.assertRaises(CommandFailure):
            effects.destroy(manifest())

    def test_foreign_state_and_live_lock_never_fall_back(self):
        effects = self.effects()
        effects.command.return_value = b'{"values":{"outputs":{"run_id":{"value":"foreign-run"}}}}'
        with patch("staging_cleanup.Orphans") as cleaner, self.assertRaises(RuntimeError):
            effects.destroy(manifest())
        cleaner.return_value.sweep.assert_not_called()
        effects.recover_terraform_lock.side_effect = RuntimeError("live lock")
        with patch("staging_cleanup.Orphans") as cleaner, self.assertRaises(RuntimeError):
            effects.destroy(manifest())
        cleaner.return_value.sweep.assert_not_called()

    def test_remote_deposed_resources_and_remaining_lock_prevent_success(self):
        effects = self.effects()
        effects.check_remote_state.return_value = [{"mode": "managed", "values": {}}]
        with patch("staging_cleanup.Orphans") as cleaner, self.assertRaisesRegex(RuntimeError, "Remote Terraform State"):
            cleaner.return_value.inventory.return_value = []
            effects.destroy(manifest())
        effects.check_remote_state.return_value = []
        effects.recover_terraform_lock.side_effect = [None, RuntimeError("live lock")]
        with patch("staging_cleanup.Orphans") as cleaner, self.assertRaisesRegex(RuntimeError, "live lock"):
            cleaner.return_value.inventory.return_value = []
            effects.destroy(manifest())

    def test_nested_managed_state_prevents_success_after_sweep(self):
        effects = self.effects()
        state = {"values": {"outputs": {"run_id": {"value": manifest()["run_id"]}}, "root_module": {
            "child_modules": [{"resources": [{"mode": "managed", "values": {}}]}]}}}
        effects.command.side_effect = [b'', b'{}', json.dumps(state).encode()]
        with patch("staging_cleanup.Orphans") as cleaner, self.assertRaisesRegex(RuntimeError, "still tracks"):
            cleaner.return_value.inventory.return_value = []
            effects.destroy(manifest())

class ServiceContractTests(CleanupFixture):
    def test_discovery_checks_every_workload_service_tags(self):
        cases = (
            ("rds", "describe_db_instances", "DBInstances", "db", {"DBInstanceIdentifier": "getddo-staging-mysql", "DBInstanceArn": "db-arn"}),
            ("rds", "describe_db_subnet_groups", "DBSubnetGroups", "db_subnet", {"DBSubnetGroupName": "getddo-staging-db", "DBSubnetGroupArn": "subnet-arn"}),
            ("elasticache", "describe_replication_groups", "ReplicationGroups", "redis", {"ReplicationGroupId": "getddo-staging-redis", "ARN": "redis-arn"}),
            ("elasticache", "describe_cache_clusters", "CacheClusters", "cache", {"CacheClusterId": "getddo-staging-redis-001", "ARN": "cache-arn"}),
            ("elasticache", "describe_cache_subnet_groups", "CacheSubnetGroups", "cache_subnet", {"CacheSubnetGroupName": "getddo-staging-redis"}),
            ("elbv2", "describe_load_balancers", "LoadBalancers", "alb", {"LoadBalancerName": "getddo-staging-api", "LoadBalancerArn": "alb-arn", "Scheme": "internal", "VpcId": VPC}),
            ("elbv2", "describe_target_groups", "TargetGroups", "tg", {"TargetGroupName": "getddo-staging-locust", "TargetGroupArn": "tg-arn"}),
        )
        for service, operation, key, kind, item in cases:
            with self.subTest(service=service, kind=kind):
                self.data.clear()
                self.data[service, operation] = [{key: [item]}]
                client = self.clients[service]
                client.list_tags_for_resource.return_value = {"TagList": tagged(self.run)}
                client.describe_tags.return_value = {"TagDescriptions": [{"Tags": tagged(self.run)}]}
                self.assertEqual(self.cleaner.inventory()[0][0], kind)
                client.list_tags_for_resource.return_value = {"TagList": tagged("foreign")}
                client.describe_tags.return_value = {"TagDescriptions": [{"Tags": tagged("foreign")}]}
                with self.assertRaisesRegex(RuntimeError, "ownership"):
                    self.cleaner.inventory()

    def test_deletion_arguments_match_pinned_sdk_models(self):
        # Static credentials and model validation only: no SDK request is sent.
        import boto3
        from botocore.validate import validate_parameters
        session = boto3.Session(aws_access_key_id="testing", aws_secret_access_key="testing", region_name="ap-northeast-2")
        models = {s: session.client(s, endpoint_url="http://127.0.0.1:1") for s in self.clients}
        cases = (
            ("instance", "i-0123456789abcdef0", {"State": {"Name": "running"}}, "ec2", "terminate_instances"),
            ("volume", "vol-0123456789abcdef0", {"State": "available"}, "ec2", "delete_volume"),
            ("rule", "sgr-0123456789abcdef0", {"GroupId": "sg-0123456789abcdef0", "IsEgress": True}, "ec2", "revoke_security_group_egress"),
            ("rule", "sgr-0123456789abcdef0", {"GroupId": "sg-0123456789abcdef0", "IsEgress": False}, "ec2", "revoke_security_group_ingress"),
            ("sg", "sg-0123456789abcdef0", {}, "ec2", "delete_security_group"),
            ("ui_rule", "rule-arn", {}, "elbv2", "delete_rule"),
            ("listener", "listener-arn", {}, "elbv2", "delete_listener"),
            ("alb", "alb-arn", {}, "elbv2", "delete_load_balancer"),
            ("tg", "tg-arn", {}, "elbv2", "delete_target_group"),
            ("db", "getddo-staging-mysql", {"DBInstanceStatus": "available"}, "rds", "delete_db_instance"),
            ("db_subnet", "getddo-staging-db", {}, "rds", "delete_db_subnet_group"),
            ("redis", "getddo-staging-redis", {"Status": "available"}, "elasticache", "delete_replication_group"),
            ("cache", "getddo-staging-redis-001", {"CacheClusterStatus": "available"}, "elasticache", "delete_cache_cluster"),
            ("cache_subnet", "getddo-staging-redis", {}, "elasticache", "delete_cache_subnet_group"),
            ("profile", "getddo-staging-api", {"Roles": []}, "iam", "delete_instance_profile"),
            ("role", "getddo-staging-api", {}, "iam", "delete_role"),
            ("log", "/getddo/staging/api", {}, "logs", "delete_log_group"),
            ("parameter", "/getddo/staging/app/PASSWORD", {}, "ssm", "delete_parameter"),
        )
        for kind, identity, item, service, method in cases:
            with self.subTest(kind=kind):
                self.cleaner.delete(kind, identity, item)
                kwargs = getattr(self.clients[service], method).call_args.kwargs
                model = models[service].meta.service_model.operation_model(models[service].meta.method_to_api_mapping[method])
                validate_parameters(kwargs, model.input_shape)

    def test_delete_cache_members_waits_for_replication_group_and_inflight_states(self):
        self.cleaner.delete("cache", "member", {"ReplicationGroupId": "getddo-staging-redis", "CacheClusterStatus": "available"})
        self.cleaner.delete("redis", "getddo-staging-redis", {"Status": "deleting"})
        self.cleaner.delete("db", "getddo-staging-mysql", {"DBInstanceStatus": "deleting"})
        self.clients["elasticache"].delete_cache_cluster.assert_not_called()
        self.clients["elasticache"].delete_replication_group.assert_not_called()
        self.clients["rds"].delete_db_instance.assert_not_called()

    def test_volume_never_detaches_from_foreign_host(self):
        item = {"State": "in-use", "Attachments": [{"InstanceId": "i-foreign"}]}
        self.clients["ec2"].describe_instances.return_value = {"Reservations": [{"Instances": [{"Tags": tagged("foreign-run")}]}]}
        with self.assertRaisesRegex(RuntimeError, "foreign"):
            self.cleaner.delete("volume", "vol-owned", item)
        self.clients["ec2"].detach_volume.assert_not_called()
        self.clients["ec2"].delete_volume.assert_not_called()

    def test_profile_role_inline_policy_dependency_order(self):
        iam = self.clients["iam"]
        iam.get_role.side_effect = None
        iam.get_role.return_value = {"Role": {"Tags": tagged(self.run)}}
        self.data["iam", "list_role_policies"] = [{"PolicyNames": ["staging-runtime"]}]
        self.cleaner.delete("profile", "getddo-staging-api", {"Roles": [{"RoleName": "getddo-staging-api"}]})
        self.cleaner.delete("role", "getddo-staging-api", {})
        names = [call[0] for call in iam.mock_calls]
        self.assertLess(names.index("remove_role_from_instance_profile"), names.index("delete_instance_profile"))
        self.assertLess(names.index("delete_role_policy"), names.index("delete_role"))

    def test_full_inventory_preflight_prevents_partial_deletion_on_foreign_candidate(self):
        instance = {"InstanceId": "i-owned", "VpcId": VPC, "State": {"Name": "running"}, "Tags": tagged(self.run)}
        self.data["ec2", "describe_instances"] = [{"Reservations": [{"Instances": [instance]}]}]
        self.data["rds", "describe_db_instances"] = [{"DBInstances": [{"DBInstanceIdentifier": "getddo-staging-mysql", "DBInstanceArn": "db-arn"}]}]
        self.clients["rds"].list_tags_for_resource.return_value = {"TagList": []}
        with self.assertRaises(RuntimeError):
            self.cleaner.sweep()
        self.clients["ec2"].terminate_instances.assert_not_called()
