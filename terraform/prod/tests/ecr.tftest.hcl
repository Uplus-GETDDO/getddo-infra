mock_provider "aws" {
  alias = "edge"

  mock_resource "aws_wafv2_web_acl" {
    defaults = { arn = "arn:aws:wafv2:us-east-1:942466928325:global/webacl/getddo-dev-api-waf/12345678-1234-1234-1234-123456789abc" }
  }
  mock_resource "aws_cloudwatch_log_group" {
    defaults = { arn = "arn:aws:logs:us-east-1:942466928325:log-group:aws-waf-logs-getddo-dev-api" }
  }
}

# AWS에 자원을 만들지 않고 외부 노출 방지·비밀번호 처리 계약을 확인한다.
mock_provider "aws" {
  mock_data "aws_caller_identity" {
    defaults = { account_id = "942466928325" }
  }

  # Dashboard rendering needs the actual two-node shape, not an empty mock set.
  mock_resource "aws_elasticache_replication_group" {
    defaults = { member_clusters = ["getddo-dev-redis-001", "getddo-dev-redis-002"] }
  }

  # 새 ALB·CloudFront 참조 값도 형식이 유효한 테스트 전용 값으로 제공한다.
  mock_resource "aws_lb" {
    defaults = {
      arn      = "arn:aws:elasticloadbalancing:ap-northeast-2:942466928325:loadbalancer/app/getddo-dev-api-alb/0123456789abcdef"
      dns_name = "internal-test.ap-northeast-2.elb.amazonaws.com"
    }
  }
  mock_resource "aws_lb_target_group" {
    defaults = { arn = "arn:aws:elasticloadbalancing:ap-northeast-2:942466928325:targetgroup/getddo-dev-api-tg/0123456789abcdef" }
  }
}

variables {
  admin_ipv4_cidr    = "203.0.113.10/32"
  ami_id             = "ami-0123456789abcdef0"
  ssh_public_key     = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIB9vBqHizcQYJVtjb3VcYO3qs95kKhQuwpDVHRptYI0G test-only"
  db_master_password = "TestOnlyPasswordNotUsedInAWS123456"
  redis_auth_token   = "TestOnlyRedisPasswordNotUsed123456"
}


override_resource {
  target = aws_ecr_repository.backend
  values = {
    arn            = "arn:aws:ecr:ap-northeast-2:942466928325:repository/getddo-be"
    repository_url = "942466928325.dkr.ecr.ap-northeast-2.amazonaws.com/getddo-be"
  }
}

override_resource {
  target = aws_iam_role.github_image_publish
  values = { arn = "arn:aws:iam::942466928325:role/getddo-dev-github-image-publish" }
}

run "immutable_repository_retains_tagged_recovery_images" {
  command = apply
  assert {
    condition = (
      aws_ecr_repository.backend.name == "getddo-be" &&
      aws_ecr_repository.backend.image_tag_mutability == "IMMUTABLE" &&
      !aws_ecr_repository.backend.force_delete &&
      one(aws_ecr_repository.backend.encryption_configuration).encryption_type == "AES256" &&
      length(jsondecode(aws_ecr_lifecycle_policy.backend.policy).rules) == 1 &&
      jsondecode(aws_ecr_lifecycle_policy.backend.policy).rules[0].selection == {
        tagStatus = "untagged", countType = "sinceImagePushed", countUnit = "days", countNumber = 7
      } &&
      jsondecode(aws_ecr_lifecycle_policy.backend.policy).rules[0].action.type == "expire"
    )
    error_message = "Keep immutable encrypted images and expire only untagged images after seven days."
  }
}

run "publisher_trust_is_exact_main_and_cd_stays_production" {
  command = apply
  assert {
    condition = (
      length(jsondecode(aws_iam_role.github_image_publish.assume_role_policy).Statement) == 1 &&
      jsondecode(aws_iam_role.github_image_publish.assume_role_policy).Statement[0].Action == "sts:AssumeRoleWithWebIdentity" &&
      jsondecode(aws_iam_role.github_image_publish.assume_role_policy).Statement[0].Principal.Federated == local.github_oidc_provider_arn &&
      jsondecode(aws_iam_role.github_image_publish.assume_role_policy).Statement[0].Condition == {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          "token.actions.githubusercontent.com:sub" = "repo:Uplus-GETDDO@341824447/getddo-be@1362269492:ref:refs/heads/main"
        }
      } &&
      jsondecode(aws_iam_role.github_cd.assume_role_policy).Statement[0].Condition.StringEquals["token.actions.githubusercontent.com:sub"] == "repo:Uplus-GETDDO@341824447/getddo-be@1362269492:environment:production"
    )
    error_message = "Only the immutable main ref may publish; no wildcard, PR, dev or environment subjects."
  }
}

run "separate_upload_pull_and_metadata_permissions" {
  command = apply
  assert {
    condition = (
      toset(jsondecode(aws_iam_role_policy.github_image_publish.policy).Statement[1].Action) == toset([
        "ecr:BatchCheckLayerAvailability", "ecr:InitiateLayerUpload", "ecr:UploadLayerPart",
        "ecr:CompleteLayerUpload", "ecr:PutImage", "ecr:BatchGetImage", "ecr:DescribeImages"
      ]) &&
      toset(jsondecode(aws_iam_role_policy.api_ecr.policy).Statement[1].Action) == toset([
        "ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability"
      ]) &&
      toset(jsondecode(aws_iam_role_policy.github_cd_ecr.policy).Statement[1].Action) == toset([
        "ecr:DescribeImages", "ecr:BatchGetImage"
      ]) &&
      aws_iam_role_policy.api_ecr.role == aws_iam_role.api.id &&
      aws_iam_role_policy.github_cd_ecr.role == aws_iam_role.github_cd.id &&
      aws_iam_role_policy.github_image_publish.role == aws_iam_role.github_image_publish.id &&
      alltrue([for p in [aws_iam_role_policy.github_image_publish, aws_iam_role_policy.api_ecr, aws_iam_role_policy.github_cd_ecr] :
        length(jsondecode(p.policy).Statement) == 2 &&
        jsondecode(p.policy).Statement[0].Effect == "Allow" &&
        jsondecode(p.policy).Statement[0].Action == ["ecr:GetAuthorizationToken"] &&
        jsondecode(p.policy).Statement[0].Resource == "*" &&
        jsondecode(p.policy).Statement[1].Effect == "Allow" &&
        jsondecode(p.policy).Statement[1].Resource == "arn:aws:ecr:ap-northeast-2:942466928325:repository/getddo-be" &&
        alltrue([for s in jsondecode(p.policy).Statement : s.Condition == { StringEquals = { "aws:RequestedRegion" = "ap-northeast-2" } }])
      ])
    )
    error_message = "Keep registry authentication separate and prevent deployment roles from gaining push/delete or other repository access."
  }
  assert {
    condition = (
      output.ecr_repository_uri == "942466928325.dkr.ecr.ap-northeast-2.amazonaws.com/getddo-be" &&
      output.ecr_repository_arn == "arn:aws:ecr:ap-northeast-2:942466928325:repository/getddo-be" &&
      output.ecr_publish_role_arn == "arn:aws:iam::942466928325:role/getddo-dev-github-image-publish" &&
      output.github_repository_variables == {
        ECR_REPOSITORY_URI   = output.ecr_repository_uri
        ECR_PUBLISH_ROLE_ARN = output.ecr_publish_role_arn
        ECR_AWS_REGION       = "ap-northeast-2"
      }
    )
    error_message = "Repository variables must come from the exact non-secret Terraform outputs."
  }
}
