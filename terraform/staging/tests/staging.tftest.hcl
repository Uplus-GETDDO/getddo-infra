mock_provider "aws" {
  mock_data "aws_caller_identity" { defaults = { account_id = "942466928325" } }
  mock_data "aws_ami" { defaults = { id = "ami-0123456789abcdef0" } }
  mock_resource "aws_lb" {
    defaults = {
      arn      = "arn:aws:elasticloadbalancing:ap-northeast-2:942466928325:loadbalancer/app/getddo-staging-api/0123456789abcdef"
      dns_name = "internal-stage.ap-northeast-2.elb.amazonaws.com"
    }
  }
  mock_resource "aws_lb_target_group" {
    defaults = { arn = "arn:aws:elasticloadbalancing:ap-northeast-2:942466928325:targetgroup/getddo-staging-test/0123456789abcdef" }
  }
  mock_resource "aws_elasticache_replication_group" {
    defaults = { member_clusters = ["getddo-staging-redis-001"] }
  }
}
override_data {
  target = data.aws_vpc.existing
  values = { id = "vpc-0fcf829db1692b713" }
}
override_data {
  target = data.aws_subnet.public["a"]
  values = { id = "subnet-public-a", availability_zone = "ap-northeast-2a" }
}
override_data {
  target = data.aws_subnet.public["c"]
  values = { id = "subnet-public-c", availability_zone = "ap-northeast-2c" }
}
override_data {
  target = data.aws_subnet.data["a"]
  values = { id = "subnet-data-a", availability_zone = "ap-northeast-2a", map_public_ip_on_launch = false }
}
override_data {
  target = data.aws_subnet.data["c"]
  values = { id = "subnet-data-c", availability_zone = "ap-northeast-2c", map_public_ip_on_launch = false }
}
override_data {
  target = data.aws_subnet.alb["a"]
  values = { id = "subnet-alb-a", availability_zone = "ap-northeast-2a", map_public_ip_on_launch = false }
}
override_data {
  target = data.aws_subnet.alb["c"]
  values = { id = "subnet-alb-c", availability_zone = "ap-northeast-2c", map_public_ip_on_launch = false }
}
override_data {
  target = data.aws_security_group.monitor
  values = { id = "sg-04954d4932de5fa69", vpc_id = "vpc-0fcf829db1692b713" }
}
override_data {
  target = data.aws_security_group.shared_alb
  values = { id = "sg-09e12310ecd082ffb", vpc_id = "vpc-0fcf829db1692b713" }
}
override_data {
  target = data.aws_security_group.ssh_endpoint
  values = { id = "sg-0d262f6bd69709b25", vpc_id = "vpc-0fcf829db1692b713" }
}
override_data {
  target = data.aws_lb.shared
  values = {
    arn             = "arn:aws:elasticloadbalancing:ap-northeast-2:942466928325:loadbalancer/app/getddo-dev-api-alb/9e65e06e10492070"
    internal        = true
    vpc_id          = "vpc-0fcf829db1692b713"
    security_groups = ["sg-09e12310ecd082ffb"]
  }
}
override_data {
  target = data.aws_lb_listener.shared
  values = { arn = "arn:aws:elasticloadbalancing:ap-northeast-2:942466928325:listener/app/getddo-dev-api-alb/9e65e06e10492070/96403935397fdc93" }
}
variables {
  run_id                       = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  ami_id                       = "ami-0123456789abcdef0"
  monitoring_security_group_id = "sg-04954d4932de5fa69"
  shared_alb_security_group_id = "sg-09e12310ecd082ffb"
  db_master_password           = "TestOnlyPasswordNotUsedInAWS123456"
  redis_auth_token             = "TestOnlyRedisTokenNotUsedInAWS123456"
}
run "dedicated_small_workload" {
  command = apply
  assert {
    condition = (
      length(aws_instance.api) == 2 &&
      alltrue([for i in aws_instance.api : i.instance_type == "t3.small" && i.metadata_options[0].http_tokens == "required" && i.root_block_device[0].encrypted]) &&
      aws_instance.locust.instance_type == "t3.medium" && aws_instance.locust.metadata_options[0].http_tokens == "required" &&
      aws_db_instance.stage.engine_version == "8.4.11" && aws_db_instance.stage.instance_class == "db.t4g.micro" &&
      aws_db_instance.stage.allocated_storage == 20 && aws_db_instance.stage.storage_type == "gp3" &&
      !aws_db_instance.stage.publicly_accessible && !aws_db_instance.stage.multi_az && aws_db_instance.stage.skip_final_snapshot &&
      aws_elasticache_replication_group.stage.node_type == "cache.t4g.micro" && aws_elasticache_replication_group.stage.engine_version == "7.1" &&
      aws_elasticache_replication_group.stage.num_cache_clusters == 1 && aws_elasticache_replication_group.stage.transit_encryption_enabled &&
      aws_elasticache_replication_group.stage.auth_token_wo_version == 1
    )
    error_message = "Keep Stage dedicated, small, encrypted, private for data, and disposable."
  }
  assert {
    condition = (
      aws_lb.stage.internal && aws_lb_listener.stage.port == 80 && aws_lb_target_group.stage.port == 8080 &&
      aws_lb_target_group.stage.health_check[0].path == "/v3/api-docs" && aws_lb_target_group.stage.health_check[0].port == "traffic-port" &&
      aws_lb_target_group.stage.health_check[0].matcher == "200" &&
      aws_lb_listener_rule.locust.priority == 30 && aws_lb_target_group.locust.port == 8089 &&
      aws_vpc_security_group_egress_rule.shared_alb_to_locust.security_group_id == var.shared_alb_security_group_id &&
      aws_vpc_security_group_egress_rule.shared_alb_to_locust.to_port == 8089 &&
      aws_vpc_security_group_ingress_rule.app_metrics.from_port == 9091 &&
      length(aws_vpc_security_group_egress_rule.monitor_to_stage) == 4 &&
      alltrue([for key, rule in aws_vpc_security_group_egress_rule.monitor_to_stage :
        rule.security_group_id == var.monitoring_security_group_id &&
        rule.referenced_security_group_id == aws_security_group.stage[local.monitor_egress[key].host].id &&
        rule.from_port == local.monitor_egress[key].port && rule.to_port == local.monitor_egress[key].port &&
        rule.cidr_ipv4 == null && rule.ip_protocol == "tcp"
      ]) &&
      alltrue([for r in aws_vpc_security_group_ingress_rule.stage : r.cidr_ipv4 == null && r.referenced_security_group_id != null])
    )
    error_message = "Use private HTTP and SG references, with only the dedicated priority30 UI rule."
  }
  assert {
    condition = alltrue([for role, policy in aws_iam_role_policy.stage :
      jsondecode(policy.policy).Statement[4].Action == ["cloudwatch:PutMetricData"] &&
      jsondecode(policy.policy).Statement[4].Condition.StringEquals["aws:RequestedRegion"] == "ap-northeast-2" &&
      toset(jsondecode(policy.policy).Statement[4].Condition.StringEquals["cloudwatch:namespace"]) == toset(role == "api" ? ["GETDDO/API", "CWAgent"] : ["CWAgent"])
    ])
    error_message = "Runtime identities need only their bounded regional app/agent metric namespaces."
  }
  assert {
    condition = (
      output.deployment.db_identifier == "getddo-staging-mysql" && output.deployment.redis_cluster_id == "getddo-staging-redis-001" &&
      length(output.deployment.api_instances) == 2 && output.deployment.account_id == "942466928325" &&
      alltrue([for i in aws_instance.api : !strcontains(i.user_data, "docker compose up") && !strcontains(i.user_data, "--with-decryption")])
    )
    error_message = "Expose non-secret integration outputs and leave hosts idle until preparation."
  }
}
run "api_health_matches_existing_production_timing" {
  command = plan
  assert {
    condition     = tonumber(aws_lb_target_group.stage.deregistration_delay) == 30 && one(aws_lb_target_group.stage.health_check).healthy_threshold == 2 && one(aws_lb_target_group.stage.health_check).unhealthy_threshold == 2 && one(aws_lb_target_group.stage.health_check).interval == 30 && one(aws_lb_target_group.stage.health_check).timeout == 5
    error_message = "Stage must preserve the production health/draining timing inside the deployment driver's wait bounds."
  }
}

run "runtime_cannot_modify_control_source_or_locks" {
  command = plan
  assert {
    condition = toset(one([for statement in jsondecode(aws_iam_role_policy.stage["api"].policy).Statement : statement.Resource if try(statement.Sid, "") == "StageReadArtifacts"])) == toset([
      "arn:aws:s3:::getddo-deploy-942466928325-ap-northeast-2/staging/${var.run_id}/releases/*",
      "arn:aws:s3:::getddo-deploy-942466928325-ap-northeast-2/staging/runs/${var.run_id}/runtime/*"
    ])
    error_message = "API role reads only its immutable release and host runtime."
  }
  assert {
    condition     = length([for statement in jsondecode(aws_iam_role_policy.stage["api"].policy).Statement : statement if contains(statement.Action, "s3:PutObject")]) == 0
    error_message = "Read-only platform smoke does not require API artifact/control writes."
  }
  assert {
    condition = toset([for statement in jsondecode(aws_iam_role_policy.stage["locust"].policy).Statement : statement.Resource if contains(statement.Action, "s3:PutObject")]) == toset([
      "arn:aws:s3:::getddo-deploy-942466928325-ap-northeast-2/staging/runs/${var.run_id}/results/locust-*",
      "arn:aws:s3:::getddo-deploy-942466928325-ap-northeast-2/staging/runs/${var.run_id}/results/operator/*"
    ])
    error_message = "Locust can write only its own report objects, never control manifests or bundles."
  }
}

run "reject_invalid_run_owner" {
  command = plan
  variables { run_id = "prod" }
  expect_failures = [var.run_id]
}

run "terminal_access_is_limited_to_endpoint_and_staging_database" {
  command = apply
  assert {
    condition = (
      aws_instance.locust.key_name == "getddo-dev-api" &&
      aws_vpc_security_group_ingress_rule.locust_ssh.referenced_security_group_id == data.aws_security_group.ssh_endpoint.id &&
      aws_vpc_security_group_ingress_rule.locust_ssh.security_group_id == aws_security_group.stage["locust"].id &&
      aws_vpc_security_group_ingress_rule.locust_ssh.from_port == 22 &&
      aws_vpc_security_group_ingress_rule.locust_ssh.to_port == 22 &&
      aws_vpc_security_group_ingress_rule.locust_ssh.cidr_ipv4 == null &&
      aws_vpc_security_group_egress_rule.ssh_endpoint_to_locust.security_group_id == data.aws_security_group.ssh_endpoint.id &&
      aws_vpc_security_group_egress_rule.ssh_endpoint_to_locust.referenced_security_group_id == aws_security_group.stage["locust"].id &&
      aws_vpc_security_group_egress_rule.ssh_endpoint_to_locust.from_port == 22 &&
      aws_vpc_security_group_egress_rule.ssh_endpoint_to_locust.to_port == 22 &&
      aws_vpc_security_group_egress_rule.ssh_endpoint_to_locust.cidr_ipv4 == null &&
      aws_vpc_security_group_ingress_rule.stage["locust_db"].referenced_security_group_id == aws_security_group.stage["locust"].id &&
      aws_vpc_security_group_ingress_rule.stage["locust_db"].security_group_id == aws_security_group.stage["db"].id &&
      aws_vpc_security_group_ingress_rule.stage["locust_db"].from_port == 3306 &&
      aws_vpc_security_group_ingress_rule.stage["locust_db"].to_port == 3306
    )
    error_message = "Only the existing EICE can SSH to Locust, and terminal SQL reaches only the dedicated Stage DB."
  }
}
