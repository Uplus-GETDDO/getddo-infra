#!/usr/bin/env python3
"""AWS adapters for the staging lifecycle; no secret values are logged."""
import datetime as dt
import io
import json
import os
import pwd
import re
import socket
from pathlib import Path
import subprocess
import tarfile
import time
import uuid

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from staging_lifecycle import ACCOUNT, BUCKET, LOCK, REGION, PERSISTENT_PARAMETERS, canonical, check_owner, verify_bundle

STATE_KEY = "staging/terraform.tfstate"
STATE_BUCKET = f"getddo-tfstate-{ACCOUNT}-{REGION}"
GROUP = "getddo-staging"
UP_PROJECT = "getddo-staging-up"
CLEANUP_PROJECT = "getddo-staging-cleanup"
SCHEDULE_ROLE = f"arn:aws:iam::{ACCOUNT}:role/getddo-staging-scheduler"


class CommandFailure(RuntimeError):
    """A subprocess exited unsuccessfully (diagnostics may contain secrets)."""


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
            raise CommandFailure(f"{Path(argv[0]).name} failed with exit {result.returncode}")
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
        self.record_execution(manifest, action)
        self.command(["terraform", "-chdir=terraform/staging", "init", "-input=false", "-lockfile=readonly"], timeout=180)
        from staging_lifecycle import check_state_owner
        state = json.loads(self.command(["terraform", "-chdir=terraform/staging", "show", "-json"], timeout=60))
        check_state_owner(state, manifest["run_id"])
        self.command(["terraform", "-chdir=terraform/staging", action, "-input=false", "-auto-approve",
                      "-var-file=/dev/stdin"], timeout=5400 if action == "apply" else 1800, data=terraform_stdin(variables))

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

    def build(self, identity, manifest):
        response = self.clients["codebuild"].batch_get_builds(ids=[identity])
        builds = response.get("builds", [])
        if len(builds) != 1 or response.get("buildsNotFound"):
            raise RuntimeError("Terraform build identity cannot be verified")
        build = builds[0]
        variables = {v["name"]: v["value"] for v in build["environment"]["environmentVariables"]}
        if (build["id"] != identity or build["projectName"] not in (UP_PROJECT, CLEANUP_PROJECT) or
                not identity.startswith(build["projectName"] + ":") or variables.get("RUN_ID") != manifest["run_id"]):
            raise RuntimeError("Terraform build belongs to another run or project")
        return build

    def record_execution(self, manifest, action):
        identity = os.environ.get("CODEBUILD_BUILD_ID", "")
        build = self.build(identity, manifest)
        if build.get("buildComplete") is not False or build["buildStatus"] != "IN_PROGRESS":
            raise RuntimeError("Terraform requires a verified running CodeBuild execution")
        check_owner(self.owner(), manifest)
        execution = {"build_id": identity, "run_id": manifest["run_id"], "action": action,
                     "who": pwd.getpwuid(os.getuid()).pw_name + "@" + socket.gethostname(),
                     "started_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                     "state_path": STATE_BUCKET + "/" + STATE_KEY}
        manifest.setdefault("terraform_executions", []).append(execution)
        # This write must finish before even init can create a Terraform lock.
        self.record(manifest, manifest.get("phase", "provisioning"))

    def check_remote_state(self, manifest):
        """Read only the exact backend State, including interrupted first apply State."""
        from staging_lifecycle import check_state_owner
        s3 = self.clients["s3"]
        try:
            response = s3.get_object(Bucket=STATE_BUCKET, Key=STATE_KEY)
        except s3.exceptions.NoSuchKey:
            return
        with response["Body"] as body:
            raw = json.load(body)
        resources = []
        for resource in raw.get("resources", []):
            for instance in resource.get("instances", []):
                resources.append({"mode": resource["mode"], "values": instance.get("attributes", {})})
        check_state_owner({"values": {"outputs": raw.get("outputs", {}),
                                     "root_module": {"resources": resources}}}, manifest["run_id"])
        return resources

    def recover_terraform_lock(self, manifest):
        self.check_remote_state(manifest)
        s3 = self.clients["s3"]
        try:
            response = s3.get_object(Bucket=STATE_BUCKET, Key=STATE_KEY + ".tflock")
        except s3.exceptions.NoSuchKey:
            return
        with response["Body"] as body:
            lock = json.load(body)
        created = dt.datetime.fromisoformat(lock["Created"].replace("Z", "+00:00"))
        matches = [e for e in manifest.get("terraform_executions", [])
                   if e.get("run_id") == manifest["run_id"] and e.get("who") == lock.get("Who") and
                   e.get("state_path") == lock.get("Path") == STATE_BUCKET + "/" + STATE_KEY and
                   e.get("action") in ("apply", "destroy") and
                   dt.datetime.fromisoformat(e["started_at"]) <= created]
        if not lock.get("ID") or not matches:
            raise RuntimeError("Terraform lock ownership is foreign or unproven")
        # The latest matching invocation is the narrowest creation interval.
        execution = max(matches, key=lambda e: e["started_at"])
        build = self.build(execution["build_id"], manifest)
        if (build.get("buildComplete") is not True or build["buildStatus"] == "IN_PROGRESS" or
                not build.get("endTime") or not build["startTime"] <= created <= build["endTime"]):
            raise RuntimeError("Terraform lock owner has not verifiably terminated")
        check_owner(self.owner(), manifest)
        # Never force-unlock by ID alone: a replacement lock must survive.
        s3.delete_object(Bucket=STATE_BUCKET, Key=STATE_KEY + ".tflock", IfMatch=response["ETag"])

    def quiesce(self, manifest):
        codebuild = self.clients["codebuild"]
        current = os.environ.get("CODEBUILD_BUILD_ID")
        ids = []
        for page in codebuild.get_paginator("list_builds_for_project").paginate(projectName=UP_PROJECT):
            ids.extend(page.get("ids", []))
        for identity in ids:
            response = codebuild.batch_get_builds(ids=[identity])
            if len(response.get("builds", [])) != 1 or response.get("buildsNotFound"):
                raise RuntimeError("Provisioning build identity cannot be verified")
            candidate = response["builds"][0]
            variables = {v["name"]: v["value"] for v in candidate["environment"]["environmentVariables"]}
            if variables.get("RUN_ID") != manifest["run_id"]:
                continue
            build = self.build(identity, manifest)
            if build["id"] == current:
                continue  # In-process rollback has no concurrent apply subprocess.
            if build.get("buildComplete") is True and build["buildStatus"] != "IN_PROGRESS":
                continue
            codebuild.stop_build(id=identity)
            deadline = time.monotonic() + 180
            while True:
                stopped = self.build(identity, manifest)
                if stopped.get("buildComplete") is True and stopped["buildStatus"] != "IN_PROGRESS":
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError("Provisioning build did not complete; cleanup cannot race apply")
                time.sleep(5)
        # An apply may have persisted execution/output metadata while stop was pending.
        latest = self.manifest(manifest["run_id"])
        check_owner(self.owner(), latest)
        check_owner(manifest, latest)
        manifest.clear()
        manifest.update(latest)
        self.recover_terraform_lock(manifest)

    def destroy(self, manifest):
        from staging_cleanup import Orphans
        from staging_lifecycle import check_state_owner, managed_resources
        # Ownership failures and lock recovery failures are outside the fallback catch.
        check_owner(self.owner(), manifest)
        self.recover_terraform_lock(manifest)
        self.record_execution(manifest, "destroy")
        self.command(["terraform", "-chdir=terraform/staging", "init", "-input=false", "-lockfile=readonly"], timeout=180)
        state = json.loads(self.command(["terraform", "-chdir=terraform/staging", "show", "-json"], timeout=60))
        check_state_owner(state, manifest["run_id"])
        orphans = Orphans(self.clients, manifest["run_id"])
        orphans.inventory()  # Reject unproven candidates before either deletion path.
        try:
            self.terraform(manifest, "destroy")
        except CommandFailure:
            # Only a completed local Terraform subprocess error is recoverable here.
            # A stale/live/foreign lock or State still fails before the SDK sweep.
            self.recover_terraform_lock(manifest)
            self.check_remote_state(manifest)
            orphans.sweep(timeout=1200)
            # Refresh/destroy removes owned missing objects through the provider;
            # never delete State objects or use state rm to manufacture success.
            self.terraform(manifest, "destroy")
        else:
            orphans.sweep(timeout=1200)
        state = json.loads(self.command(["terraform", "-chdir=terraform/staging", "show", "-json"], timeout=60))
        check_state_owner(state, manifest["run_id"])
        if list(managed_resources(state)):
            raise RuntimeError("Terraform still tracks staging resources")
        if any(r["mode"] == "managed" for r in (self.check_remote_state(manifest) or [])):
            raise RuntimeError("Remote Terraform State still tracks staging resources")
        if orphans.inventory():
            raise RuntimeError("AWS workload resources remain after destroy")
        self.recover_terraform_lock(manifest)

    def delete_app_parameters(self, manifest):
        ssm = self.clients["ssm"]
        from staging_cleanup import Orphans, tags
        from staging_lifecycle import cleanup_parameter_names
        for page in ssm.get_paginator("describe_parameters").paginate(ParameterFilters=[
                {"Key": "Name", "Option": "BeginsWith", "Values": ["/getddo/staging/app/"]}]):
            for name in cleanup_parameter_names([p["Name"] for p in page["Parameters"]]):
                actual = ssm.list_tags_for_resource(ResourceType="Parameter", ResourceId=name)["TagList"]
                Orphans(self.clients, manifest["run_id"]).require(tags(actual))
                try:
                    ssm.delete_parameter(Name=name)
                except ssm.exceptions.ParameterNotFound:
                    pass

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
        from staging_cleanup import Orphans
        return [identity for _, identity, _ in Orphans(self.clients, manifest["run_id"]).inventory()]

    def wait_deleted(self, manifest, timeout=600):
        deadline = time.monotonic() + timeout
        while True:
            remaining = self.residuals(manifest)
            if not remaining or time.monotonic() >= deadline:
                return remaining
            time.sleep(10)
