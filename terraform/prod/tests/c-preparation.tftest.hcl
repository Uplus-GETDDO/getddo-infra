mock_provider "aws" {
  alias = "edge"

  mock_resource "aws_wafv2_web_acl" {
    defaults = { arn = "arn:aws:wafv2:us-east-1:942466928325:global/webacl/getddo-dev-api-waf/12345678-1234-1234-1234-123456789abc" }
  }
  mock_resource "aws_cloudwatch_log_group" {
    defaults = { arn = "arn:aws:logs:us-east-1:942466928325:log-group:aws-waf-logs-getddo-dev-api" }
  }
}

mock_provider "aws" {
  mock_resource "aws_lb" {
    defaults = {
      arn      = "arn:aws:elasticloadbalancing:ap-northeast-2:942466928325:loadbalancer/app/getddo-dev-api-alb/0123456789abcdef"
      dns_name = "internal-test.ap-northeast-2.elb.amazonaws.com"
    }
  }
  mock_resource "aws_lb_target_group" {
    defaults = { arn = "arn:aws:elasticloadbalancing:ap-northeast-2:942466928325:targetgroup/getddo-dev-api-tg/0123456789abcdef" }
  }
  mock_resource "aws_elasticache_replication_group" {
    defaults = { member_clusters = ["getddo-dev-redis-001", "getddo-dev-redis-002"] }
  }
  mock_data "aws_ami" { defaults = { id = "ami-0870825cefcaafcc8" } }
  mock_data "aws_caller_identity" { defaults = { account_id = "942466928325" } }
}
variables {
  admin_ipv4_cidr    = "203.0.113.10/32"
  ami_id             = "ami-0870825cefcaafcc8"
  ssh_public_key     = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIB9vBqHizcQYJVtjb3VcYO3qs95kKhQuwpDVHRptYI0G test-only"
  db_master_password = "TestOnlyPasswordNotUsedInAWS123456"
  redis_auth_token   = "TestOnlyRedisPasswordNotUsed123456"
}
run "c_bootstrap_same_as_console_artifact" {
  command = plan
  assert {
    condition     = aws_subnet.public_c.availability_zone == "ap-northeast-2c" && aws_subnet.public_c.cidr_block == "10.20.1.0/24" && !aws_subnet.public_c.map_public_ip_on_launch && aws_instance.api_c.instance_type == "t3.small" && aws_instance.api_c.metadata_options[0].http_tokens == "required"
    error_message = "Preserve the previously prepared C subnet, size and IMDSv2."
  }
  assert {
    condition     = local.api_c_user_data == file("${path.module}/templates/console-user-data.sh") && !strcontains(local.api_c_user_data, "docker compose up") && !strcontains(local.api_c_user_data, "--with-decryption")
    error_message = "Relocation must preserve the exact C bootstrap without starting the app or reading secrets."
  }
}

run "reject_invalid_c_ami_id" {
  command = plan
  variables { api_c_ami_id = "latest" }
  expect_failures = [var.api_c_ami_id]
}

run "c_not_registered_before_readiness" {
  command = plan
  assert {
    condition     = length(aws_lb_target_group_attachment.api_c) == 0
    error_message = "C must remain outside the ALB target group by default."
  }
}

run "c_registered_only_after_explicit_gate" {
  command = apply
  variables { api_c_register_in_target_group = true }
  assert {
    condition = (
      length(aws_lb_target_group_attachment.api_c) == 1 &&
      aws_lb_target_group_attachment.api_c[0].target_id == aws_instance.api_c.id &&
      aws_lb_target_group_attachment.api_c[0].target_group_arn == aws_lb_target_group.api.arn &&
      aws_lb_target_group_attachment.api_c[0].port == 8080 &&
      aws_lb_target_group_attachment.api.target_id == aws_instance.api.id
    )
    error_message = "Explicit C readiness must add only C alongside the existing A target."
  }
  assert {
    condition = (
      output.github_production_variables.EC2_INSTANCE_ID == aws_instance.api.id &&
      output.github_production_variables.EC2_INSTANCE_ID_A == aws_instance.api.id &&
      output.github_production_variables.EC2_INSTANCE_ID_C == aws_instance.api_c.id &&
      output.github_production_variables.TARGET_GROUP_ARN == aws_lb_target_group.api.arn &&
      output.api_deployment_metadata.servers.c.registered_in_terraform &&
      output.api_deployment_metadata.servers.c.private_ip == aws_instance.api_c.private_ip &&
      output.api_deployment_metadata.servers.a.private_ip == aws_instance.api.private_ip &&
      output.api_deployment_metadata.target_port == 8080
    )
    error_message = "Deployment handoff must preserve A and identify both hosts and the shared target group."
  }
}
