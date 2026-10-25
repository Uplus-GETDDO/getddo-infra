mock_provider "aws" {
  mock_data "aws_caller_identity" { defaults = { account_id = "942466928325" } }
  mock_resource "aws_iam_policy" {
    defaults = { arn = "arn:aws:iam::942466928325:policy/test-mock" }
  }
}

run "no_standing_failover_permission" {
  command = plan

  assert {
    condition     = length(aws_iam_user_policy.redis_failover_test) == 0
    error_message = "기본 null 입력에서는 시험 권한을 생성하지 않아야 합니다."
  }
}

run "temporary_failover_permission_boundary" {
  command = apply

  variables {
    redis_failover_test_expires_at = timeadd(timestamp(), "30m")
  }

  assert {
    condition = (
      length(aws_iam_user_policy.redis_failover_test) == 1 &&
      aws_iam_user_policy.redis_failover_test[0].user == "gyeongju" &&
      aws_iam_user_policy.redis_failover_test[0].name == "getddo-redis-failover-test" &&
      jsondecode(aws_iam_user_policy.redis_failover_test[0].policy).Version == "2012-10-17" &&
      length(jsondecode(aws_iam_user_policy.redis_failover_test[0].policy).Statement) == 1 &&
      jsondecode(aws_iam_user_policy.redis_failover_test[0].policy).Statement[0].Effect == "Allow" &&
      jsondecode(aws_iam_user_policy.redis_failover_test[0].policy).Statement[0].Action == ["elasticache:TestFailover"] &&
      jsondecode(aws_iam_user_policy.redis_failover_test[0].policy).Statement[0].Resource == ["arn:aws:elasticache:ap-northeast-2:942466928325:replicationgroup:getddo-dev-redis"] &&
      jsondecode(aws_iam_user_policy.redis_failover_test[0].policy).Statement[0].Condition.StringEquals["aws:RequestedRegion"] == "ap-northeast-2" &&
      jsondecode(aws_iam_user_policy.redis_failover_test[0].policy).Statement[0].Condition.DateLessThan["aws:CurrentTime"] == var.redis_failover_test_expires_at
    )
    error_message = "임시 정책은 gyeongju의 기존 Redis 그룹 한 개·TestFailover 단일 동작·서울·명시 UTC 만료로 제한해야 합니다."
  }
}

run "remove_failover_permission_after_test" {
  command = plan

  assert {
    condition     = length(aws_iam_user_policy.redis_failover_test) == 0
    error_message = "시험 뒤 null 입력은 전용 정책 제거를 계획해야 합니다."
  }
}

run "reject_expired_failover_permission" {
  command = plan
  variables {
    redis_failover_test_expires_at = "2000-01-01T00:00:00Z"
  }
  expect_failures = [var.redis_failover_test_expires_at]
}

run "reject_excessive_permission_window" {
  command = plan
  variables {
    redis_failover_test_expires_at = timeadd(timestamp(), "61m")
  }
  expect_failures = [var.redis_failover_test_expires_at]
}

run "reject_invalid_expiry" {
  command = plan
  variables {
    redis_failover_test_expires_at = "invalid"
  }
  expect_failures = [var.redis_failover_test_expires_at]
}

run "reject_non_z_expiry" {
  command = plan
  variables {
    redis_failover_test_expires_at = replace(timeadd(timestamp(), "30m"), "Z", "+00:00")
  }
  expect_failures = [var.redis_failover_test_expires_at]
}
