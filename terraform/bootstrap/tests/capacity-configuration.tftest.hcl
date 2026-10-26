mock_provider "aws" {
  mock_data "aws_caller_identity" { defaults = { account_id = "942466928325" } }
  mock_resource "aws_iam_policy" {
    defaults = { arn = "arn:aws:iam::942466928325:policy/test-mock" }
  }
}

run "no_standing_resize_permission" {
  command = plan
  assert {
    condition     = length(aws_iam_user_policy.capacity_configuration) == 0
    error_message = "기본 입력에서는 사양 변경용 임시 권한을 생성하지 않아야 합니다."
  }
}

run "temporary_resize_scope" {
  command = apply
  variables {
    capacity_configuration_expires_at = timeadd(timestamp(), "30m")
  }
  assert {
    condition = (
      aws_iam_user_policy.capacity_configuration[0].user == "gyeongju" &&
      length(jsondecode(aws_iam_user_policy.capacity_configuration[0].policy).Statement) == 2 &&
      toset(jsondecode(aws_iam_user_policy.capacity_configuration[0].policy).Statement[0].Action) == toset(["ec2:StartInstances", "ec2:StopInstances"]) &&
      toset(jsondecode(aws_iam_user_policy.capacity_configuration[0].policy).Statement[0].Resource) == toset([
        "arn:aws:ec2:ap-northeast-2:942466928325:instance/i-0c67aa33f1d5c5fd9",
        "arn:aws:ec2:ap-northeast-2:942466928325:instance/i-0dcfb0a85a548c240"
      ]) &&
      jsondecode(aws_iam_user_policy.capacity_configuration[0].policy).Statement[0].Condition.DateLessThan["aws:CurrentTime"] == var.capacity_configuration_expires_at &&
      jsondecode(aws_iam_user_policy.capacity_configuration[0].policy).Statement[0].Condition.StringEquals["aws:RequestedRegion"] == "ap-northeast-2" &&
      jsondecode(aws_iam_user_policy.capacity_configuration[0].policy).Statement[1].Action == ["rds:ModifyDBInstance"] &&
      toset(jsondecode(aws_iam_user_policy.capacity_configuration[0].policy).Statement[1].Resource) == toset([
        "arn:aws:rds:ap-northeast-2:942466928325:db:getddo-dev-mysql",
        "arn:aws:rds:ap-northeast-2:942466928325:db:getddo-staging-mysql"
      ]) &&
      jsondecode(aws_iam_user_policy.capacity_configuration[0].policy).Statement[1].Condition.DateLessThan["aws:CurrentTime"] == var.capacity_configuration_expires_at &&
      jsondecode(aws_iam_user_policy.capacity_configuration[0].policy).Statement[1].Condition.StringEquals["aws:RequestedRegion"] == "ap-northeast-2"
    )
    error_message = "권한은 지정 운영 API와 두 DB·서울·만료 시각에 한정해야 합니다."
  }
}

run "reject_expired_resize_permission" {
  command = plan
  variables {
    capacity_configuration_expires_at = "2000-01-01T00:00:00Z"
  }
  expect_failures = [var.capacity_configuration_expires_at]
}

run "reject_excessive_resize_window" {
  command = plan
  variables {
    capacity_configuration_expires_at = timeadd(timestamp(), "61m")
  }
  expect_failures = [var.capacity_configuration_expires_at]
}
