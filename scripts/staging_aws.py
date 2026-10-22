#!/usr/bin/env python3
"""AWS adapters for the staging lifecycle; no secret values are logged."""
import datetime as dt
import io
import json
import os
import re
from pathlib import Path
import subprocess
import tarfile
import time
import uuid

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from staging_lifecycle import ACCOUNT, BUCKET, LOCK, REGION, PERSISTENT_PARAMETERS, canonical, check_owner, verify_bundle

STATE_BUCKET = f"getddo-tfstate-{ACCOUNT}-{REGION}"
GROUP = "getddo-staging"
UP_PROJECT = "getddo-staging-up"
CLEANUP_PROJECT = "getddo-staging-cleanup"
SCHEDULE_ROLE = f"arn:aws:iam::{ACCOUNT}:role/getddo-staging-scheduler"
SHARED_LISTENER = f"arn:aws:elasticloadbalancing:{REGION}:{ACCOUNT}:listener/app/getddo-dev-api-alb/9e65e06e10492070/96403935397fdc93"


def terraform_stdin(variables):
    # /dev/stdin has no .json suffix, so Terraform expects native HCL assignments.
    lines = []
    for key, value in variables.items():
        if not re.fullmatch(r"[a-z_]+", key) or not isinstance(value, str):
            raise ValueError("Only named string Terraform inputs are accepted")
        literal = json.dumps(value).replace("${", "$${").replace("%{", "%%{")
        lines.append(f"{key} = {literal}\n")
    return "".join(lines).encode()


class AWS:
    def __init__(self, session=None):
        self.session = session or boto3.Session(region_name=REGION)
        config = Config(retries={"mode": "standard", "total_max_attempts": 4}, connect_timeout=10, read_timeout=30)
        self.clients = {name: self.session.client(name, config=config) for name in
                        ("s3", "sts", "scheduler", "codebuild", "ssm", "ec2", "rds", "elasticache", "elbv2", "iam", "logs")}
        if self.clients["sts"].get_caller_identity()["Account"] != ACCOUNT:
            raise RuntimeError("Unexpected AWS account")

    def get(self, key):
        response = self.clients["s3"].get_object(Bucket=BUCKET, Key=key)
        with response["Body"] as body:
            return json.load(body), response["ETag"]

    def put(self, key, value, **condition):
        return self.clients["s3"].put_object(Bucket=BUCKET, Key=key, Body=canonical(value),
                                             ServerSideEncryption="AES256", **condition)

    def manifest(self, run):
        return self.get(f"staging/runs/{run}/manifest.json")[0]

    def owner(self):
        return self.get(LOCK)[0]

    def credentials_metadata(self):
        """Snapshot metadata only; the control role cannot read persistent values."""
        result = {}
        ssm = self.clients["ssm"]
        for name in sorted(PERSISTENT_PARAMETERS):
            entries = []
            for page in ssm.get_paginator("describe_parameters").paginate(
                    ParameterFilters=[{"Key": "Name", "Option": "Equals", "Values": [name]}]):
                entries.extend(page["Parameters"])
            if len(entries) != 1 or entries[0]["Name"] != name:
                raise RuntimeError("Persistent Locust credential metadata is missing")
            entry = entries[0]
            tags = {t["Key"]: t["Value"] for t in ssm.list_tags_for_resource(
                ResourceType="Parameter", ResourceId=name)["TagList"]}
            if tags.get("Lifecycle") != "persistent-configuration":
                raise RuntimeError("Locust credentials must be persistent configuration")
            result[name] = {"version": entry["Version"], "type": entry["Type"], "tags": tags}
        return result

    def verify_credentials(self, manifest):
        if self.credentials_metadata() != manifest["persistent_credentials"]:
            raise RuntimeError("Persistent Locust credential metadata changed during this run")

    def record(self, manifest, phase, **details):
        manifest.update(phase=phase, **details)
        self.put(f"staging/runs/{manifest['run_id']}/manifest.json", manifest)

    def guard(self, manifest):
        name = "expiry-" + manifest["run_id"]
        expiry = dt.datetime.fromisoformat(manifest["expires_at"]).astimezone(dt.timezone.utc)
        self.clients["scheduler"].create_schedule(
            Name=name, GroupName=GROUP, ScheduleExpression=f"at({expiry.strftime('%Y-%m-%dT%H:%M:%S')})",
            ScheduleExpressionTimezone="UTC", FlexibleTimeWindow={"Mode": "OFF"}, ActionAfterCompletion="DELETE",
            Target={"Arn": "arn:aws:scheduler:::aws-sdk:codebuild:startBuild", "RoleArn": SCHEDULE_ROLE,
                    "Input": json.dumps({"projectName": CLEANUP_PROJECT, "environmentVariablesOverride": [
                        {"name": "RUN_ID", "value": manifest["run_id"], "type": "PLAINTEXT"},
                        {"name": "ACTION", "value": "ttl", "type": "PLAINTEXT"}]}),
                    "RetryPolicy": {"MaximumEventAgeInSeconds": 86400, "MaximumRetryAttempts": 185}})
        self.verify_guard(manifest)

    def verify_guard(self, manifest):
        guard = self.clients["scheduler"].get_schedule(Name="expiry-" + manifest["run_id"], GroupName=GROUP)
        payload = json.loads(guard["Target"]["Input"])
        values = {v["name"]: v["value"] for v in payload.get("environmentVariablesOverride", [])}
        deadline = dt.datetime.fromisoformat(manifest["expires_at"]).astimezone(dt.timezone.utc)
        if (guard["State"] != "ENABLED" or guard.get("ScheduleExpressionTimezone") != "UTC" or
                guard.get("FlexibleTimeWindow", {}).get("Mode") != "OFF" or
                guard["Target"]["RoleArn"] != SCHEDULE_ROLE or
                guard["Target"]["Arn"] != "arn:aws:scheduler:::aws-sdk:codebuild:startBuild" or
                payload["projectName"] != CLEANUP_PROJECT or values.get("RUN_ID") != manifest["run_id"] or
                values.get("ACTION") != "ttl" or
                guard["ScheduleExpression"] != f"at({deadline.strftime('%Y-%m-%dT%H:%M:%S')})" or
                deadline <= dt.datetime.now(dt.timezone.utc)):
            raise RuntimeError("Independent staging expiry guard is missing or invalid")

    def start(self, manifest, action):
        return self.clients["codebuild"].start_build(
            projectName=UP_PROJECT if action == "up" else CLEANUP_PROJECT,
            environmentVariablesOverride=[{"name": "RUN_ID", "value": manifest["run_id"], "type": "PLAINTEXT"},
                                          {"name": "ACTION", "value": action, "type": "PLAINTEXT"}],
            idempotencyToken=uuid.uuid4().hex)["build"]["id"]

    def release(self, manifest):
        owner, etag = self.get(LOCK)
        check_owner(owner, manifest)
        # Expiry can already be auto-deleted by Scheduler.
        scheduler = self.clients["scheduler"]
        try:
            scheduler.delete_schedule(Name="expiry-" + manifest["run_id"], GroupName=GROUP)
        except scheduler.exceptions.ResourceNotFoundException:
            pass
        self.clients["s3"].delete_object(Bucket=BUCKET, Key=LOCK, IfMatch=etag)

    def extract(self, manifest, destination):
        response = self.clients["s3"].get_object(Bucket=BUCKET, Key=manifest["bundle_key"])
        with response["Body"] as body:
            content = body.read()
        verify_bundle(content, manifest["bundle_sha256"])
        with tarfile.open(fileobj=io.BytesIO(content), mode="r:gz") as archive:
            # No links, devices or path escapes in executable bundles.
            for member in archive.getmembers():
                if not (member.isfile() or member.isdir()) or Path(member.name).is_absolute() or ".." in Path(member.name).parts:
                    raise ValueError("Unsafe source archive member")
            archive.extractall(destination, filter="data")


class Effects(AWS):
    def __init__(self, root, session=None):
        super().__init__(session)
        self.root = Path(root).resolve()

    def command(self, argv, *, timeout=3600, data=None):
        # Capture Terraform/SSM diagnostics; errors never echo secret-bearing stdout.
        result = subprocess.run(argv, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                cwd=self.root, timeout=timeout, check=False)
        if result.returncode:
            raise RuntimeError(f"{Path(argv[0]).name} failed with exit {result.returncode}")
        return result.stdout

    def terraform(self, manifest, action):
        variables = dict(manifest["terraform"])
        variables["run_id"] = manifest["run_id"]
        # Ephemeral/write-only values use private stdin, never argv or disk.
        if action == "apply":
            for variable, parameter in (("db_master_password", "SPRING_DATASOURCE_PASSWORD"), ("redis_auth_token", "SPRING_DATA_REDIS_PASSWORD")):
                variables[variable] = self.clients["ssm"].get_parameter(
                    Name=f"/getddo/staging/app/{parameter}", WithDecryption=True)["Parameter"]["Value"]
        else:
            variables.update(db_master_password="UnusedDestroyPassword123456789", redis_auth_token="UnusedDestroyToken123456789")
        self.command(["terraform", "-chdir=terraform/staging", "init", "-input=false", "-lockfile=readonly"])
        from staging_lifecycle import check_state_owner
        state = json.loads(self.command(["terraform", "-chdir=terraform/staging", "show", "-json"]))
        check_state_owner(state, manifest["run_id"])
        self.command(["terraform", "-chdir=terraform/staging", action, "-input=false", "-auto-approve",
                      "-var-file=/dev/stdin"], timeout=5400, data=terraform_stdin(variables))

    def app_parameter(self, manifest, name, value, secret=False):
        ssm = self.clients["ssm"]
        path = f"/getddo/staging/app/{name}"
        try:
            ssm.put_parameter(Name=path, Value=value, Type="SecureString" if secret else "String",
                              Tags=[{"Key": "Environment", "Value": "staging"},
                                    {"Key": "RunId", "Value": manifest["run_id"]}])
        except ssm.exceptions.ParameterAlreadyExists:
            tags = ssm.list_tags_for_resource(ResourceType="Parameter", ResourceId=path)["TagList"]
            if {t["Key"]: t["Value"] for t in tags}.get("RunId") != manifest["run_id"]:
                raise RuntimeError("App parameter belongs to another run")
            if not secret:
                ssm.put_parameter(Name=path, Value=value, Type="String", Overwrite=True)

    def apply(self, manifest):
        import secrets
        for name in ("SPRING_DATASOURCE_PASSWORD", "SPRING_DATA_REDIS_PASSWORD"):
            self.app_parameter(manifest, name, secrets.token_hex(16), secret=True)
        self.terraform(manifest, "apply")
        deployment = json.loads(self.command(["terraform", "-chdir=terraform/staging", "output", "-json", "deployment"]))
        if deployment["account_id"] != ACCOUNT or deployment["region"] != REGION:
            raise RuntimeError("Invalid deployment outputs")
        manifest["deployment"] = deployment
        settings = {
            "SPRING_DATASOURCE_URL": f"jdbc:mysql://{deployment['db_endpoint']}:3306/getddo?sslMode=VERIFY_IDENTITY",
            "SPRING_DATASOURCE_USERNAME": "getddoadmin",
            "SPRING_JPA_HIBERNATE_DDL_AUTO": "validate", "SPRING_FLYWAY_ENABLED": "true",
            "CORS_ALLOWED_ORIGINS": manifest["public_origin"], "AWS_REGION": REGION,
            "GETDDO_IMAGES_BUCKET": BUCKET, "GETDDO_IMAGES_BASE_URL": manifest["public_origin"] + "/images/",
            "SPRING_DATA_REDIS_HOST": deployment["redis_endpoint"], "SPRING_DATA_REDIS_PORT": "6379",
            "SPRING_DATA_REDIS_SSL_ENABLED": "true", "RATE_LIMIT_STORE": "redis",
        }
        for name, value in settings.items():
            self.app_parameter(manifest, name, value)
        self.record(manifest, "infrastructure_ready")

    def hook(self, manifest, name, timeout=900):
        # Hooks are immutable bundle paths. JSON travels on stdin; hooks must propagate failure.
        hook = manifest["hooks"][name]
        path = (self.root / hook).resolve()
        if not path.is_relative_to(self.root) or not path.is_file():
            raise ValueError("Missing or unsafe staging integration hook")
        return self.command(["python3", str(path), name], data=canonical(manifest), timeout=timeout)

    def prepare(self, manifest):
        self.wait_hosts(manifest)
        self.verify_guard(manifest)
        self.hook(manifest, "prepare", timeout=1800)

    def wait_hosts(self, manifest):
        deployment = manifest["deployment"]
        ids = [i["id"] for i in deployment["api_instances"].values()] + [deployment["locust_instance"]["id"]]
        ec2 = self.clients["ec2"]
        instances = [i for r in ec2.describe_instances(InstanceIds=ids)["Reservations"] for i in r["Instances"]]
        if len(instances) != 3 or any(i["VpcId"] != deployment["vpc_id"] or
                {t["Key"]: t["Value"] for t in i.get("Tags", [])}.get("RunId") != manifest["run_id"] for i in instances):
            raise RuntimeError("Stage host ownership does not match the run")
        deadline = time.monotonic() + 900
        ssm = self.clients["ssm"]
        while True:
            online = ssm.describe_instance_information(Filters=[{"Key": "InstanceIds", "Values": ids}])["InstanceInformationList"]
            if len(online) == 3 and all(i["PingStatus"] == "Online" for i in online):
                break
            if time.monotonic() >= deadline:
                raise RuntimeError("Stage SSM agents did not become online")
            time.sleep(10)
        command = ssm.send_command(InstanceIds=ids, DocumentName="AWS-RunShellScript",
            Parameters={"commands": ["timeout 600 bash -c 'until test -f /opt/getddo/staging/host-ready; do sleep 5; done'"],
                        "executionTimeout": ["660"]})["Command"]["CommandId"]
        for instance in ids:
            while True:
                try:
                    invocation = ssm.get_command_invocation(CommandId=command, InstanceId=instance)
                except ssm.exceptions.InvocationDoesNotExist:
                    invocation = {"Status": "Pending"}
                status = invocation["Status"]
                if status == "Success":
                    break
                if status not in ("Pending", "InProgress", "Delayed") or time.monotonic() >= deadline:
                    raise RuntimeError("Stage host bootstrap failed or timed out")
                time.sleep(5)

    def collect(self, manifest, timeout):
        # The hook must stop load and return an S3 receipt with a digest for readback.
        receipt = json.loads(self.hook(manifest, "collect", timeout))
        key = receipt["key"]
        if not key.startswith(f"staging/runs/{manifest['run_id']}/results/"):
            raise RuntimeError("Collection receipt is outside this run")
        response = self.clients["s3"].get_object(Bucket=BUCKET, Key=key)
        with response["Body"] as body:
            verify_bundle(body.read(), receipt["sha256"])
        manifest["results"] = receipt
        self.record(manifest, "collected")

    def detach(self, manifest):
        # Coordinator-owned hook removes UI/monitor routing before host deletion.
        self.hook(manifest, "detach", timeout=300)

    def quiesce(self, manifest):
        codebuild = self.clients["codebuild"]
        current = os.environ.get("CODEBUILD_BUILD_ID")
        ids = []
        for page in codebuild.get_paginator("list_builds_for_project").paginate(projectName=UP_PROJECT):
            ids.extend(page.get("ids", []))
        for offset in range(0, len(ids), 100):
            for build in codebuild.batch_get_builds(ids=ids[offset:offset + 100])["builds"]:
                if build["id"] == current or build["buildStatus"] != "IN_PROGRESS":
                    continue
                variables = {v["name"]: v["value"] for v in build["environment"]["environmentVariables"]}
                if variables.get("RUN_ID") == manifest["run_id"]:
                    codebuild.stop_build(id=build["id"])
                    deadline = time.monotonic() + 180
                    while codebuild.batch_get_builds(ids=[build["id"]])["builds"][0]["buildStatus"] == "IN_PROGRESS":
                        if time.monotonic() >= deadline:
                            raise RuntimeError("Provisioning build did not stop; cleanup cannot race apply")
                        time.sleep(5)

    def destroy(self, manifest):
        self.terraform(manifest, "destroy")
        state = json.loads(self.command(["terraform", "-chdir=terraform/staging", "show", "-json"]))
        if any(r.get("mode") == "managed" for r in state.get("values", {}).get("root_module", {}).get("resources", [])):
            raise RuntimeError("Terraform still tracks staging resources")

    def delete_app_parameters(self, manifest):
        ssm = self.clients["ssm"]
        from staging_lifecycle import cleanup_parameter_names
        for page in ssm.get_paginator("get_parameters_by_path").paginate(Path="/getddo/staging/app/", Recursive=True, WithDecryption=False):
            for name in cleanup_parameter_names([p["Name"] for p in page["Parameters"]]):
                tags = ssm.list_tags_for_resource(ResourceType="Parameter", ResourceId=name)["TagList"]
                if {t["Key"]: t["Value"] for t in tags}.get("RunId") != manifest["run_id"]:
                    raise RuntimeError("App parameter belongs to another run")
                ssm.delete_parameter(Name=name)

    def delete_deployment_lock(self, manifest):
        # HeadObject uses a generic 404 rather than a typed NoSuchKey exception.
        s3 = self.clients["s3"]
        key = "staging/locks/app.json"
        listed = s3.list_objects_v2(Bucket=BUCKET, Prefix=key, MaxKeys=1)
        if not any(item['Key'] == key for item in listed.get('Contents', [])):
            return
        try:
            lock = s3.head_object(Bucket=BUCKET, Key=key)
        except ClientError as error:
            if error.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
                return
            raise
        if lock.get("Metadata", {}).get("run-id") != manifest["run_id"]:
            raise RuntimeError("Deployment lock belongs to another run")
        s3.delete_object(Bucket=BUCKET, Key=key, IfMatch=lock["ETag"])

    def residuals(self, manifest):
        # Check services independently of State, including partial apply resources.
        found = []
        ec2 = self.clients["ec2"]
        filters = [{"Name": "tag:Environment", "Values": ["staging"]}, {"Name": "tag:RunId", "Values": [manifest["run_id"]]}]
        for page in ec2.get_paginator("describe_instances").paginate(Filters=filters):
            found.extend(i["InstanceId"] for r in page["Reservations"] for i in r["Instances"] if i["State"]["Name"] != "terminated")
        for operation, result in (("describe_volumes", "Volumes"), ("describe_security_groups", "SecurityGroups")):
            for page in ec2.get_paginator(operation).paginate(Filters=filters):
                found.extend(item.get("VolumeId", item.get("GroupId")) for item in page[result])
        for page in ec2.get_paginator("describe_security_group_rules").paginate(Filters=filters):
            found.extend(rule["SecurityGroupRuleId"] for rule in page["SecurityGroupRules"])
        for service, operation, result, field in (
            ("rds", "describe_db_instances", "DBInstances", "DBInstanceIdentifier"),
            ("rds", "describe_db_subnet_groups", "DBSubnetGroups", "DBSubnetGroupName"),
            ("elasticache", "describe_replication_groups", "ReplicationGroups", "ReplicationGroupId"),
            ("elasticache", "describe_cache_clusters", "CacheClusters", "CacheClusterId"),
            ("elasticache", "describe_cache_subnet_groups", "CacheSubnetGroups", "CacheSubnetGroupName"),
            ("elbv2", "describe_load_balancers", "LoadBalancers", "LoadBalancerName"),
            ("elbv2", "describe_target_groups", "TargetGroups", "TargetGroupName")):
            for page in self.clients[service].get_paginator(operation).paginate():
                found.extend(i[field] for i in page[result] if i[field].startswith("getddo-staging-"))
        elb = self.clients["elbv2"]
        for page in elb.get_paginator("describe_rules").paginate(ListenerArn=SHARED_LISTENER):
            arns = [r["RuleArn"] for r in page["Rules"] if not r["IsDefault"]]
            for offset in range(0, len(arns), 20):
                for description in elb.describe_tags(ResourceArns=arns[offset:offset + 20])["TagDescriptions"]:
                    tags = {t["Key"]: t["Value"] for t in description["Tags"]}
                    if tags.get("Environment") == "staging" and tags.get("RunId") == manifest["run_id"]:
                        found.append(description["ResourceArn"])
        for page in self.clients["iam"].get_paginator("list_roles").paginate():
            found.extend(r["RoleName"] for r in page["Roles"] if r["RoleName"] in ("getddo-staging-api", "getddo-staging-locust"))
        for page in self.clients["iam"].get_paginator("list_instance_profiles").paginate():
            found.extend(r["InstanceProfileName"] for r in page["InstanceProfiles"] if r["InstanceProfileName"] in ("getddo-staging-api", "getddo-staging-locust"))
        for page in self.clients["logs"].get_paginator("describe_log_groups").paginate(logGroupNamePrefix="/getddo/staging/api"):
            found.extend(r["logGroupName"] for r in page["logGroups"] if r["logGroupName"] == "/getddo/staging/api")
        for page in self.clients["ssm"].get_paginator("get_parameters_by_path").paginate(
                Path="/getddo/staging/app/", Recursive=True, WithDecryption=False):
            found.extend(p["Name"] for p in page["Parameters"])
        return found

    def wait_deleted(self, manifest, timeout=600):
        deadline = time.monotonic() + timeout
        while True:
            remaining = self.residuals(manifest)
            if not remaining or time.monotonic() >= deadline:
                return remaining
            time.sleep(10)
