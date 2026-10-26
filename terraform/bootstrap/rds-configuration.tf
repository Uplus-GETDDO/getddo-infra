variable "rds_configuration_expires_at" {
  type        = string
  default     = null
  description = "UTC expiry for observed DB configuration association and reboot; null leaves no configuration permission"

  validation {
    condition = var.rds_configuration_expires_at == null ? true : (
      can(timecmp(var.rds_configuration_expires_at, plantimestamp())) ? (
        endswith(var.rds_configuration_expires_at, "Z") &&
        timecmp(var.rds_configuration_expires_at, plantimestamp()) > 0 &&
        timecmp(var.rds_configuration_expires_at, timeadd(plantimestamp(), "60m")) <= 0
      ) : false
    )
    error_message = "설정 적용 권한 만료는 현재부터 60분 이내의 미래 UTC RFC3339 시각(Z)이거나 null이어야 합니다."
  }
}

# 설정 적용 직전만 생성하고 종료 후 null 입력으로 제거한다. 만료는 회수 지연을 방어한다.
# 운영 재부팅과 기존 스테이징 DB의 설정 그룹 연결에만 사용하고 종료 후 제거한다.
# IAM으로 변경 필드를 제한할 수 없으므로 지정 DB·만료와 검토한 전체 plan을 함께 확인한다.
resource "aws_iam_user_policy" "rds_configuration" {
  count = var.rds_configuration_expires_at == null ? 0 : 1

  name = "getddo-rds-configuration"
  user = local.operator_manifest.user_name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid      = "TemporaryProductionConfigurationReboot"
      Effect   = "Allow"
      Action   = ["rds:RebootDBInstance"]
      Resource = ["arn:aws:rds:ap-northeast-2:942466928325:db:getddo-dev-mysql"]
      Condition = {
        StringEquals = { "aws:RequestedRegion" = "ap-northeast-2" }
        DateLessThan = { "aws:CurrentTime" = var.rds_configuration_expires_at }
      }
      }, {
      Sid      = "TemporaryStageParameterGroupAssociation"
      Effect   = "Allow"
      Action   = ["rds:ModifyDBInstance"]
      Resource = ["arn:aws:rds:ap-northeast-2:942466928325:db:getddo-staging-mysql"]
      Condition = {
        StringEquals = { "aws:RequestedRegion" = "ap-northeast-2" }
        DateLessThan = { "aws:CurrentTime" = var.rds_configuration_expires_at }
      }
    }]
  })

  lifecycle {
    precondition {
      condition     = timecmp(var.rds_configuration_expires_at, timestamp()) > 0
      error_message = "저장 plan의 설정 적용 권한이 만료되었습니다. 실행 직전에 새 만료 시각과 전체 plan을 검토해야 합니다."
    }
  }
}
