variable "rds_failover_test_expires_at" {
  type        = string
  default     = null
  description = "UTC expiry for an approved RDS failover test; null leaves no test permission"

  validation {
    condition = var.rds_failover_test_expires_at == null ? true : (
      can(timecmp(var.rds_failover_test_expires_at, plantimestamp())) ? (
        endswith(var.rds_failover_test_expires_at, "Z") &&
        timecmp(var.rds_failover_test_expires_at, plantimestamp()) > 0 &&
        timecmp(var.rds_failover_test_expires_at, timeadd(plantimestamp(), "60m")) <= 0
      ) : false
    )
    error_message = "시험 권한 만료는 현재부터 60분 이내의 미래 UTC RFC3339 시각(Z)이거나 null이어야 합니다."
  }
}

# 시험 직전만 생성하고 종료 후 null 입력으로 제거한다. 만료는 회수 지연을 방어한다.
# IAM은 force-failover 플래그나 호출 횟수를 제한하지 않으므로 별도 실행 게이트를 따른다.
resource "aws_iam_user_policy" "rds_failover_test" {
  count = var.rds_failover_test_expires_at == null ? 0 : 1

  name = "getddo-rds-failover-test"
  user = local.operator_manifest.user_name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid      = "TemporaryProductionFailoverTest"
      Effect   = "Allow"
      Action   = ["rds:RebootDBInstance"]
      Resource = ["arn:aws:rds:ap-northeast-2:942466928325:db:getddo-dev-mysql"]
      Condition = {
        StringEquals = { "aws:RequestedRegion" = "ap-northeast-2" }
        DateLessThan = { "aws:CurrentTime" = var.rds_failover_test_expires_at }
      }
    }]
  })

  lifecycle {
    precondition {
      condition     = timecmp(var.rds_failover_test_expires_at, timestamp()) > 0
      error_message = "저장 plan의 시험 권한이 만료되었습니다. 실행 직전에 새 만료 시각과 전체 plan을 검토해야 합니다."
    }
  }
}
