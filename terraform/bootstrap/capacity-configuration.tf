variable "capacity_configuration_expires_at" {
  type        = string
  default     = null
  description = "서버 사양 변경에 필요한 임시 권한의 UTC 만료 시각. null이면 권한을 제거한다."

  validation {
    condition = var.capacity_configuration_expires_at == null ? true : (
      can(timecmp(var.capacity_configuration_expires_at, plantimestamp())) ? (
        endswith(var.capacity_configuration_expires_at, "Z") &&
        timecmp(var.capacity_configuration_expires_at, plantimestamp()) > 0 &&
        timecmp(var.capacity_configuration_expires_at, timeadd(plantimestamp(), "60m")) <= 0
      ) : false
    )
    error_message = "임시 권한 만료는 현재부터 60분 이내의 미래 UTC 시각(Z)이거나 null이어야 합니다."
  }
}

# 운영 API의 정지·시작과 두 DB의 사양 변경에만 사용한다. 작업 후 null로 제거한다.
# IAM은 DB 변경 필드를 제한하지 못하므로 전체 계획에서 사양 변경 범위를 함께 확인한다.
resource "aws_iam_user_policy" "capacity_configuration" {
  count = var.capacity_configuration_expires_at == null ? 0 : 1
  name  = "getddo-capacity-configuration"
  user  = local.operator_manifest.user_name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid    = "TemporaryProductionApiStopStart"
      Effect = "Allow"
      Action = ["ec2:StopInstances", "ec2:StartInstances"]
      Resource = [
        "arn:aws:ec2:ap-northeast-2:942466928325:instance/i-0c67aa33f1d5c5fd9",
        "arn:aws:ec2:ap-northeast-2:942466928325:instance/i-0dcfb0a85a548c240"
      ]
      Condition = {
        StringEquals = { "aws:RequestedRegion" = "ap-northeast-2" }
        DateLessThan = { "aws:CurrentTime" = var.capacity_configuration_expires_at }
      }
      }, {
      Sid    = "TemporaryExistingDatabaseResize"
      Effect = "Allow"
      Action = ["rds:ModifyDBInstance"]
      Resource = [
        "arn:aws:rds:ap-northeast-2:942466928325:db:getddo-dev-mysql",
        "arn:aws:rds:ap-northeast-2:942466928325:db:getddo-staging-mysql"
      ]
      Condition = {
        StringEquals = { "aws:RequestedRegion" = "ap-northeast-2" }
        DateLessThan = { "aws:CurrentTime" = var.capacity_configuration_expires_at }
      }
    }]
  })

  lifecycle {
    precondition {
      condition     = timecmp(var.capacity_configuration_expires_at, timestamp()) > 0
      error_message = "저장한 계획의 임시 권한이 만료되었습니다. 새 만료 시각으로 다시 계획해야 합니다."
    }
  }
}
