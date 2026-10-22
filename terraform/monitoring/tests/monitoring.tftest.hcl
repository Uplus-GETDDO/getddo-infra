mock_provider "aws" {
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
    condition     = aws_instance.monitoring.root_block_device[0].tags["Name"] == "getddo-prod-monitoring-root" && aws_ebs_volume.data.tags["Name"] == "getddo-prod-monitoring-data"
    error_message = "Root-only tags must not overwrite the independently attached monitoring data volume. Runtime no-drift plans verify the provider behavior."
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
