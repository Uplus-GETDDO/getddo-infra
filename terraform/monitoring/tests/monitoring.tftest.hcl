variables {
  cloudfront_distribution_id = "ETESTONLY"
}
mock_provider "aws" {
  mock_resource "aws_lb_target_group" {
    defaults = { arn = "arn:aws:elasticloadbalancing:ap-northeast-2:942466928325:targetgroup/getddo-dev-grafana-tg/0123456789abcdef" }
  }
  mock_data "aws_lb" {
    defaults = { arn = "arn:aws:elasticloadbalancing:ap-northeast-2:942466928325:loadbalancer/app/getddo-dev-api-alb/0123456789abcdef", internal = true, vpc_id = "vpc-mock" }
  }
  mock_data "aws_lb_listener" {
    defaults = { arn = "arn:aws:elasticloadbalancing:ap-northeast-2:942466928325:listener/app/getddo-dev-api-alb/0123456789abcdef/0123456789abcdef" }
  }
  mock_data "aws_vpc" {
    defaults = { id = "vpc-mock" }
  }
  mock_data "aws_subnet" {
    defaults = { id = "subnet-mock", availability_zone = "ap-northeast-2a", vpc_id = "vpc-mock" }
  }
  mock_data "aws_security_group" {
    defaults = { id = "sg-api-mock" }
  }
  mock_data "aws_route_table" {
    defaults = {
      routes = [{ cidr_block = "0.0.0.0/0", gateway_id = "igw-mock" }]
    }
  }
  mock_data "aws_instance" {
    defaults = { subnet_id = "subnet-mock", vpc_security_group_ids = ["sg-api-mock"] }
  }
}
run "private_and_persistent" {
  command = plan
  assert {
    condition     = aws_instance.monitoring.instance_type == "t3.medium" && aws_instance.monitoring.metadata_options[0].http_tokens == "required" && aws_instance.monitoring.metadata_options[0].http_put_response_hop_limit == 2
    error_message = "Size and IMDSv2 must support trusted bridge containers."
  }
  assert {
    condition     = aws_instance.monitoring.volume_tags["Name"] == "getddo-prod-monitoring-root" && aws_ebs_volume.data.tags["Name"] == "getddo-prod-monitoring-data"
    error_message = "Launch volume tags and the separately managed data EBS must use distinct names; this mock checks planned values only."
  }
  assert {
    condition     = aws_ebs_volume.data.encrypted && aws_ebs_volume.data.size == 30 && !aws_volume_attachment.data.force_detach
    error_message = "Data disk must be encrypted, independent and never force-detached."
  }
  assert {
    condition     = alltrue([for rule in aws_vpc_security_group_ingress_rule.api_metrics : rule.security_group_id == "sg-api-mock" && contains([9091, 9100], rule.from_port) && rule.cidr_ipv4 == null])
    error_message = "API rules must target the existing API SG without CIDR exposure."
  }
  assert {
    condition     = aws_instance.monitoring.user_data == null
    error_message = "Installation is separate and SSH keys are unnecessary."
  }
  assert {
    condition     = alltrue([for s in jsondecode(aws_iam_role_policy.ssm.policy).Statement : !contains(s.Action, "ssm:GetParameter") && !contains(s.Action, "ssm:GetParameters") && !contains(s.Action, "ssm:GetParametersByPath")])
    error_message = "SSM agent connectivity must not leak application parameter read permissions."
  }
  assert {
    condition     = length([for s in jsondecode(aws_iam_role_policy.read.policy).Statement : s if contains(s.Action, "ssm:GetParameter") && s.Resource == "arn:aws:ssm:ap-northeast-2:942466928325:parameter/getddo/monitoring/grafana/admin-password"]) == 1
    error_message = "Only the dedicated Grafana parameter may be read."
  }
}
run "reject_other_vpc" {
  command = plan
  override_data {
    target = data.aws_subnet.api["A"]
    values = { vpc_id = "vpc-other" }
  }
  expect_failures = [aws_instance.monitoring]
}
run "reject_missing_egress" {
  command = plan
  override_data {
    target = data.aws_route_table.existing
    values = { routes = [] }
  }
  expect_failures = [aws_instance.monitoring]
}

run "grafana_private_alb_path" {
  command = apply
  assert {
    condition     = aws_vpc_security_group_ingress_rule.grafana_from_alb.referenced_security_group_id == data.aws_security_group.alb.id && aws_vpc_security_group_ingress_rule.grafana_from_alb.cidr_ipv4 == null && aws_vpc_security_group_ingress_rule.grafana_from_alb.from_port == 3000 && aws_vpc_security_group_egress_rule.alb_to_grafana.referenced_security_group_id == aws_security_group.monitoring.id
    error_message = "Grafana ingress must allow only the existing ALB security group, never a public CIDR."
  }
  assert {
    condition     = toset(one(aws_lb_listener_rule.grafana.condition).path_pattern[0].values) == toset(["/grafana", "/grafana/*"]) && aws_lb_target_group_attachment.grafana.target_id == aws_instance.monitoring.id && aws_lb_target_group.grafana.health_check[0].path == "/grafana/api/health"
    error_message = "Only the Grafana subpath may route to the monitoring instance."
  }
}

run "stage_data_only_monitoring_requests" {
  command = plan
  assert {
    condition     = length([for s in jsondecode(aws_iam_role_policy.staging_targets.policy).Statement : s if s.Action == "s3:GetObject" && s.Resource == "arn:aws:s3:::getddo-deploy-942466928325-ap-northeast-2/staging/monitoring/request.json"]) == 1
    error_message = "Monitor may consume only the fixed Stage monitoring request."
  }
  assert {
    condition     = length([for s in jsondecode(aws_iam_role_policy.staging_targets.policy).Statement : s if s.Action == "s3:PutObject" && try(contains(s.Resource, "arn:aws:s3:::getddo-deploy-942466928325-ap-northeast-2/staging/monitoring/status.json"), false) && length(s.Resource) == 2]) == 1
    error_message = "Monitor may acknowledge only the fixed Stage monitoring status object."
  }
}
