"""Ownership-checked discovery and bounded deletion of temporary Stage orphans."""
import time

from botocore.exceptions import ClientError

from staging_lifecycle import ACCOUNT, REGION

VPC = "vpc-0fcf829db1692b713"
SHARED_GROUPS = {"sg-09e12310ecd082ffb", "sg-04954d4932de5fa69", "sg-0d262f6bd69709b25"}
SHARED_LISTENER = f"arn:aws:elasticloadbalancing:{REGION}:{ACCOUNT}:listener/app/getddo-dev-api-alb/9e65e06e10492070/96403935397fdc93"
ROLES = ("getddo-staging-api", "getddo-staging-locust")
# Only these service responses mean a concurrent/idempotent delete succeeded.
ABSENT = {"InvalidInstanceID.NotFound", "InvalidVolume.NotFound", "InvalidGroup.NotFound",
          "InvalidSecurityGroupRuleId.NotFound", "InvalidPermission.NotFound", "DBInstanceNotFound",
          "DBSubnetGroupNotFoundFault", "ReplicationGroupNotFoundFault", "CacheClusterNotFound",
          "CacheClusterNotFoundFault", "CacheSubnetGroupNotFoundFault", "LoadBalancerNotFound",
          "ListenerNotFound", "RuleNotFound", "TargetGroupNotFound", "NoSuchEntity",
          "ResourceNotFoundException", "ParameterNotFound"}
TRANSITIONAL = {"DependencyViolation", "VolumeInUse", "ResourceInUse", "ResourceInUseException",
                "InvalidDBInstanceState", "InvalidDBInstanceStateFault", "InvalidDBSubnetGroupStateFault",
                "InvalidReplicationGroupState", "InvalidReplicationGroupStateFault", "InvalidCacheClusterState",
                "InvalidCacheClusterStateFault", "CacheSubnetGroupInUse", "CacheSubnetGroupInUseFault",
                "DeleteConflict"}


def tags(value):
    return {t["Key"]: t["Value"] for t in value}


class Orphans:
    def __init__(self, clients, run):
        self.clients, self.run = clients, run

    def owned(self, value):
        return value.get("Environment") == "staging" and value.get("RunId") == self.run

    def require(self, value):
        if not self.owned(value):
            raise RuntimeError("Workload candidate ownership is foreign or unproven")

    def pages(self, service, operation, key, **kwargs):
        try:
            for page in self.clients[service].get_paginator(operation).paginate(**kwargs):
                yield from page.get(key, [])
        except ClientError as error:
            if error.response["Error"]["Code"] not in ABSENT:
                raise

    def inventory(self):
        """Return (kind, ID, description) only after reading actual service tags."""
        found = []
        ec2 = self.clients["ec2"]
        filters = [{"Name": "tag:Environment", "Values": ["staging"]},
                   {"Name": "tag:RunId", "Values": [self.run]}]
        for r in self.pages("ec2", "describe_instances", "Reservations", Filters=filters):
            for i in r["Instances"]:
                if i["State"]["Name"] != "terminated":
                    self.require(tags(i.get("Tags", [])))
                    if i["VpcId"] != VPC:
                        raise RuntimeError("Stage instance is outside the approved VPC")
                    found.append(("instance", i["InstanceId"], i))
        for operation, key, kind, field in (
                ("describe_volumes", "Volumes", "volume", "VolumeId"),
                ("describe_security_groups", "SecurityGroups", "sg", "GroupId")):
            for item in self.pages("ec2", operation, key, Filters=filters):
                self.require(tags(item.get("Tags", [])))
                if kind == "sg" and (item["VpcId"] != VPC or item[field] in SHARED_GROUPS):
                    raise RuntimeError("Refusing deletion of a shared or foreign security group")
                found.append((kind, item[field], item))
        groups = {identity for kind, identity, _ in found if kind == "sg"}
        for rule in self.pages("ec2", "describe_security_group_rules", "SecurityGroupRules", Filters=filters):
            self.require(tags(rule.get("Tags", [])))
            if rule["GroupId"] not in groups | SHARED_GROUPS:
                raise RuntimeError("Stage rule has an unapproved parent security group")
            found.append(("rule", rule["SecurityGroupRuleId"], rule))
        for service, operation, key, kind, field, arnfield, arntype in (
                ("rds", "describe_db_instances", "DBInstances", "db", "DBInstanceIdentifier", "DBInstanceArn", ""),
                ("rds", "describe_db_subnet_groups", "DBSubnetGroups", "db_subnet", "DBSubnetGroupName", "DBSubnetGroupArn", ""),
                ("elasticache", "describe_replication_groups", "ReplicationGroups", "redis", "ReplicationGroupId", "ARN", "replicationgroup"),
                ("elasticache", "describe_cache_clusters", "CacheClusters", "cache", "CacheClusterId", "ARN", "cluster"),
                ("elasticache", "describe_cache_subnet_groups", "CacheSubnetGroups", "cache_subnet", "CacheSubnetGroupName", "ARN", "subnetgroup"),
                ("elbv2", "describe_load_balancers", "LoadBalancers", "alb", "LoadBalancerName", "LoadBalancerArn", ""),
                ("elbv2", "describe_target_groups", "TargetGroups", "tg", "TargetGroupName", "TargetGroupArn", "")):
            client = self.clients[service]
            for item in self.pages(service, operation, key):
                if not item[field].startswith("getddo-staging-"):
                    continue
                arn = item.get(arnfield) or f"arn:aws:elasticache:{REGION}:{ACCOUNT}:{arntype}:{item[field]}"
                try:
                    if service == "elbv2":
                        actual = client.describe_tags(ResourceArns=[arn])["TagDescriptions"][0]["Tags"]
                    elif service == "rds":
                        actual = client.list_tags_for_resource(ResourceName=arn)["TagList"]
                    else:
                        actual = client.list_tags_for_resource(ResourceName=arn)["TagList"]
                except ClientError as error:
                    if error.response["Error"]["Code"] in ABSENT:
                        continue
                    raise
                self.require(tags(actual))
                if kind == "alb" and (item["Scheme"] != "internal" or item["VpcId"] != VPC):
                    raise RuntimeError("Stage ALB is outside the approved private VPC")
                found.append((kind, arn if service == "elbv2" else item[field], item))
                if kind == "alb":
                    for listener in self.pages("elbv2", "describe_listeners", "Listeners", LoadBalancerArn=arn):
                        listener_arn = listener["ListenerArn"]
                        actual = client.describe_tags(ResourceArns=[listener_arn])["TagDescriptions"][0]["Tags"]
                        self.require(tags(actual))
                        found.append(("listener", listener_arn, listener))
        elb = self.clients["elbv2"]
        for rule in self.pages("elbv2", "describe_rules", "Rules", ListenerArn=SHARED_LISTENER):
            if rule["IsDefault"] or rule["Priority"] != "30":
                continue
            actual = elb.describe_tags(ResourceArns=[rule["RuleArn"]])["TagDescriptions"][0]["Tags"]
            self.require(tags(actual))
            found.append(("ui_rule", rule["RuleArn"], rule))
        iam = self.clients["iam"]
        for name in ROLES:
            for operation, key, kind, argument in (
                    ("get_instance_profile", "InstanceProfile", "profile", "InstanceProfileName"),
                    ("get_role", "Role", "role", "RoleName")):
                try:
                    item = getattr(iam, operation)(**{argument: name})[key]
                except iam.exceptions.NoSuchEntityException:
                    continue
                self.require(tags(item.get("Tags", [])))
                found.append((kind, name, item))
        logs = self.clients["logs"]
        for item in self.pages("logs", "describe_log_groups", "logGroups", logGroupNamePrefix="/getddo/staging/api"):
            if item["logGroupName"] == "/getddo/staging/api":
                arn = item.get("logGroupArn") or item["arn"].removesuffix(":*")
                self.require(logs.list_tags_for_resource(resourceArn=arn)["tags"])
                found.append(("log", item["logGroupName"], item))
        ssm = self.clients["ssm"]
        # DescribeParameters returns metadata; cleanup never reads app secret values.
        for prefix in ("/getddo/staging/app/", "/getddo/staging/test-access/"):
            for item in self.pages("ssm", "describe_parameters", "Parameters", ParameterFilters=[
                    {"Key": "Name", "Option": "BeginsWith", "Values": [prefix]}]):
                name = item["Name"]
                if name.startswith(prefix):
                    self.require(tags(ssm.list_tags_for_resource(ResourceType="Parameter", ResourceId=name)["TagList"]))
                    found.append(("parameter", name, item))
        return found

    def delete(self, kind, identity, item):
        ec2, elb, iam = (self.clients[s] for s in ("ec2", "elbv2", "iam"))
        if kind == "instance":
            if item["State"]["Name"] != "shutting-down":
                ec2.terminate_instances(InstanceIds=[identity])
        elif kind == "volume":
            if item["State"] == "available":
                ec2.delete_volume(VolumeId=identity)
            elif item.get("Attachments"):
                # Never detach from a live or foreign host. Termination releases own volumes.
                ids = [a["InstanceId"] for a in item["Attachments"]]
                instances = [i for r in ec2.describe_instances(InstanceIds=ids)["Reservations"] for i in r["Instances"]]
                if len(instances) != len(ids) or any(not self.owned(tags(i.get("Tags", []))) for i in instances):
                    raise RuntimeError("Stage volume is attached to a foreign or unproven host")
        elif kind == "rule":
            operation = ec2.revoke_security_group_egress if item["IsEgress"] else ec2.revoke_security_group_ingress
            operation(GroupId=item["GroupId"], SecurityGroupRuleIds=[identity])
        elif kind == "sg":
            ec2.delete_security_group(GroupId=identity)
        elif kind == "ui_rule":
            elb.delete_rule(RuleArn=identity)
        elif kind == "listener":
            elb.delete_listener(ListenerArn=identity)
        elif kind == "alb":
            elb.delete_load_balancer(LoadBalancerArn=identity)
        elif kind == "tg":
            elb.delete_target_group(TargetGroupArn=identity)
        elif kind == "db":
            if item["DBInstanceStatus"] != "deleting":
                self.clients["rds"].delete_db_instance(DBInstanceIdentifier=identity, SkipFinalSnapshot=True, DeleteAutomatedBackups=True)
        elif kind == "db_subnet":
            self.clients["rds"].delete_db_subnet_group(DBSubnetGroupName=identity)
        elif kind == "redis":
            if item["Status"] != "deleting":
                self.clients["elasticache"].delete_replication_group(ReplicationGroupId=identity, RetainPrimaryCluster=False)
        elif kind == "cache":
            if not item.get("ReplicationGroupId") and item["CacheClusterStatus"] != "deleting":
                self.clients["elasticache"].delete_cache_cluster(CacheClusterId=identity)
        elif kind == "cache_subnet":
            self.clients["elasticache"].delete_cache_subnet_group(CacheSubnetGroupName=identity)
        elif kind == "profile":
            for role in item["Roles"]:
                if role["RoleName"] not in ROLES:
                    raise RuntimeError("Stage instance profile contains a foreign role")
                self.require(tags(iam.get_role(RoleName=role["RoleName"])["Role"].get("Tags", [])))
                iam.remove_role_from_instance_profile(InstanceProfileName=identity, RoleName=role["RoleName"])
            iam.delete_instance_profile(InstanceProfileName=identity)
        elif kind == "role":
            # No managed policy is installed by Stage; unexpected attachments fail closed.
            if list(self.pages("iam", "list_attached_role_policies", "AttachedPolicies", RoleName=identity)):
                raise RuntimeError("Stage role has unexpected managed policy attachments")
            for policy in self.pages("iam", "list_role_policies", "PolicyNames", RoleName=identity):
                iam.delete_role_policy(RoleName=identity, PolicyName=policy)
            iam.delete_role(RoleName=identity)
        elif kind == "log":
            self.clients["logs"].delete_log_group(logGroupName=identity)
        elif kind == "parameter":
            self.clients["ssm"].delete_parameter(Name=identity)
        else:
            raise ValueError("Unknown workload resource kind")

    def sweep(self, timeout=1800):
        deadline = time.monotonic() + timeout
        order = {kind: n for n, kind in enumerate(("ui_rule", "listener", "alb", "tg", "instance", "db", "redis", "cache",
                                                  "volume", "rule", "sg", "db_subnet", "cache_subnet", "profile", "role", "log", "parameter"))}
        while True:
            inventory = self.inventory()  # Complete ownership preflight before any deletion.
            if not inventory:
                return
            if time.monotonic() >= deadline:
                raise RuntimeError("Owned workload resources remain after bounded orphan cleanup")
            for kind, identity, item in sorted(inventory, key=lambda r: order[r[0]]):
                if time.monotonic() >= deadline:
                    raise RuntimeError("Owned workload resources remain after bounded orphan cleanup")
                try:
                    self.delete(kind, identity, item)
                except ClientError as error:
                    if error.response["Error"]["Code"] not in ABSENT | TRANSITIONAL:
                        raise
            time.sleep(10)
