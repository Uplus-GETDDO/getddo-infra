# Service runtime and backend deployment roles; repository trust stays on getddo-be.
resource "aws_iam_role" "api" {
  name = "getddo-dev-api"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "ec2.amazonaws.com" }
    }]
  })
}

resource "aws_iam_instance_profile" "api" {
  name = "getddo-dev-api"
  role = aws_iam_role.api.name
}

resource "aws_iam_role_policy_attachment" "api_ssm" {
  role       = aws_iam_role.api.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy" "api_releases" {
  name = "read-deployment-files"
  role = aws_iam_role.api.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["s3:GetObject"]
      Resource = ["${aws_s3_bucket.releases.arn}/releases/*"]
    }]
  })
}

# 앱의 이미지 업로드 전용 정책이다. 기존 배포 파일 읽기 정책을 변경하지 않는다.
# EC2 역할의 임시 인증 정보를 AWS SDK 기본 인증 체인으로 사용하며 Access Key는 저장하지 않는다.
# 현재는 단일 PutObject 업로드만 허용한다. 조회·삭제·버킷 목록 권한은 기능 계약 확정 후 추가한다.
resource "aws_iam_role_policy" "api_images" {
  name = "upload-images"
  role = aws_iam_role.api.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["s3:PutObject"]
      Resource = ["${aws_s3_bucket.images.arn}/images/*"]
    }]
  })
}

resource "aws_iam_openid_connect_provider" "github" {
  count          = var.github_oidc_provider_arn == null ? 1 : 0
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}

locals {
  github_oidc_provider_arn = var.github_oidc_provider_arn != null ? var.github_oidc_provider_arn : aws_iam_openid_connect_provider.github[0].arn
  # 2026-10-14 GitHub API에서 확인한 immutable subject prefix를 사용한다.
  github_oidc_subject = "repo:Uplus-GETDDO@341824447/getddo-be@1362269492:environment:production"
}

resource "aws_iam_role" "github_cd" {
  name = "getddo-dev-github-cd"
  # 두 서버 순차 배포의 SSM 대기·복구가 한 시간을 넘을 수 있다. 신뢰 정책은 유지한다.
  max_session_duration = 7200
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRoleWithWebIdentity"
      Principal = { Federated = local.github_oidc_provider_arn }
      Condition = { StringEquals = {
        "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
        "token.actions.githubusercontent.com:sub" = local.github_oidc_subject
      } }
    }]
  })

  lifecycle {
    precondition {
      condition     = var.github_oidc_provider_arn == null ? true : split(":", var.github_oidc_provider_arn)[4] == data.aws_caller_identity.current.account_id
      error_message = "재사용할 OIDC 공급자는 현재 AWS 계정에 있어야 합니다."
    }
  }
}

resource "aws_iam_role_policy" "github_cd" {
  name = "upload-and-deploy"
  role = aws_iam_role.github_cd.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:PutObject"]
        Resource = ["${aws_s3_bucket.releases.arn}/releases/*"]
      },
      {
        Effect = "Allow"
        Action = ["ssm:SendCommand"]
        Resource = [
          aws_instance.api.arn,
          "arn:aws:ssm:${var.aws_region}::document/AWS-RunShellScript"
        ]
      },
      {
        # GetCommandInvocation은 리소스 ARN 단위 권한 제한을 지원하지 않는다.
        Effect   = "Allow"
        Action   = ["ssm:GetCommandInvocation"]
        Resource = "*"
      }
    ]
  })
}

resource "aws_iam_role_policy" "api_observability" {
  name   = "write-cloudwatch-observability"
  role   = aws_iam_role.api.id
  policy = file("${path.module}/templates/iam/runtime/write-cloudwatch-observability.json")
}


# Existing AWS inline policy, imported into dev State on 2026-10-21.
resource "aws_iam_role_policy" "github_cd_two_api" {
  name   = "two-api-deployment"
  role   = aws_iam_role.github_cd.id
  policy = file("${path.module}/templates/iam/github-cd/two-api-deployment.json")
}
