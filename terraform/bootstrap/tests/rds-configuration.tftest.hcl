mock_provider "aws" {
  mock_data "aws_caller_identity" { defaults = { account_id = "942466928325" } }
  mock_resource "aws_iam_policy" {
    defaults = { arn = "arn:aws:iam::942466928325:policy/test-mock" }
  }
}

run "no_standing_configuration_permission" {
  command = plan

  assert {
    condition     = length(aws_iam_user_policy.rds_configuration) == 0
    error_message = "기본 null 입력에서는 설정 적용 권한을 생성하지 않아야 합니다."
  }
}

run "temporary_configuration_permission_boundary" {
  command = apply

  variables {
    rds_configuration_expires_at = timeadd(timestamp(), "30m")
  }

  assert {
    condition = (
      length(aws_iam_user_policy.rds_configuration) == 1 &&
      aws_iam_user_policy.rds_configuration[0].user == "gyeongju" &&
      aws_iam_user_policy.rds_configuration[0].name == "getddo-rds-configuration" &&
      jsondecode(aws_iam_user_policy.rds_configuration[0].policy).Version == "2012-10-17" &&
      length(jsondecode(aws_iam_user_policy.rds_configuration[0].policy).Statement) == 2 &&
      jsondecode(aws_iam_user_policy.rds_configuration[0].policy).Statement[0].Effect == "Allow" &&
      jsondecode(aws_iam_user_policy.rds_configuration[0].policy).Statement[0].Action == ["rds:RebootDBInstance"] &&
      jsondecode(aws_iam_user_policy.rds_configuration[0].policy).Statement[0].Resource == ["arn:aws:rds:ap-northeast-2:942466928325:db:getddo-dev-mysql"] &&
      jsondecode(aws_iam_user_policy.rds_configuration[0].policy).Statement[0].Condition.StringEquals["aws:RequestedRegion"] == "ap-northeast-2" &&
      jsondecode(aws_iam_user_policy.rds_configuration[0].policy).Statement[0].Condition.DateLessThan["aws:CurrentTime"] == var.rds_configuration_expires_at &&
      jsondecode(aws_iam_user_policy.rds_configuration[0].policy).Statement[1].Action == ["rds:ModifyDBInstance"] &&
      jsondecode(aws_iam_user_policy.rds_configuration[0].policy).Statement[1].Resource == ["arn:aws:rds:ap-northeast-2:942466928325:db:getddo-staging-mysql"] &&
      jsondecode(aws_iam_user_policy.rds_configuration[0].policy).Statement[1].Condition.DateLessThan["aws:CurrentTime"] == var.rds_configuration_expires_at
    )
    error_message = "임시 정책은 기존 운영 DB 재부팅과 스테이징 DB 그룹 연결·서울·명시 UTC 만료로 제한해야 합니다."
  }
}

run "remove_configuration_permission_after_configuration" {
  command = plan

  assert {
    condition     = length(aws_iam_user_policy.rds_configuration) == 0
    error_message = "설정 적용 뒤 null 입력은 전용 정책 제거를 계획해야 합니다."
  }
}

run "reject_expired_configuration_permission" {
  command = plan
  variables {
    rds_configuration_expires_at = "2000-01-01T00:00:00Z"
  }
  expect_failures = [var.rds_configuration_expires_at]
}

run "reject_excessive_permission_window" {
  command = plan
  variables {
    rds_configuration_expires_at = timeadd(timestamp(), "61m")
  }
  expect_failures = [var.rds_configuration_expires_at]
}

run "reject_invalid_expiry" {
  command = plan
  variables {
    rds_configuration_expires_at = "invalid"
  }
  expect_failures = [var.rds_configuration_expires_at]
}

run "reject_non_z_expiry" {
  command = plan
  variables {
    rds_configuration_expires_at = replace(timeadd(timestamp(), "30m"), "Z", "+00:00")
  }
  expect_failures = [var.rds_configuration_expires_at]
}
