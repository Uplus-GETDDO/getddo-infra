# Tagged deployment and recovery images remain available; only untagged images expire.
resource "aws_ecr_repository" "backend" {
  name                 = "getddo-be"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = false

  encryption_configuration {
    encryption_type = "AES256"
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_ecr_lifecycle_policy" "backend" {
  repository = aws_ecr_repository.backend.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Expire only untagged images after seven days"
      selection = {
        tagStatus   = "untagged"
        countType   = "sinceImagePushed"
        countUnit   = "days"
        countNumber = 7
      }
      action = { type = "expire" }
    }]
  })
}

locals {
  # Registry authentication has no repository resource; all image operations stay scoped below.
  ecr_auth_statement = {
    Effect    = "Allow"
    Action    = ["ecr:GetAuthorizationToken"]
    Resource  = "*"
    Condition = { StringEquals = { "aws:RequestedRegion" = var.aws_region } }
  }
  github_image_publish_subject = "repo:Uplus-GETDDO@341824447/getddo-be@1362269492:ref:refs/heads/main"
}

resource "aws_iam_role" "github_image_publish" {
  name = "getddo-dev-github-image-publish"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRoleWithWebIdentity"
      Principal = { Federated = local.github_oidc_provider_arn }
      Condition = { StringEquals = {
        "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
        "token.actions.githubusercontent.com:sub" = local.github_image_publish_subject
      } }
    }]
  })
}

resource "aws_iam_role_policy" "github_image_publish" {
  name = "publish-backend-image"
  role = aws_iam_role.github_image_publish.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [local.ecr_auth_statement, {
      Effect = "Allow"
      Action = [
        "ecr:BatchCheckLayerAvailability", "ecr:InitiateLayerUpload", "ecr:UploadLayerPart",
        "ecr:CompleteLayerUpload", "ecr:PutImage", "ecr:BatchGetImage", "ecr:DescribeImages"
      ]
      Resource  = aws_ecr_repository.backend.arn
      Condition = { StringEquals = { "aws:RequestedRegion" = var.aws_region } }
    }]
  })
}

resource "aws_iam_role_policy" "api_ecr" {
  name = "pull-backend-image"
  role = aws_iam_role.api.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [local.ecr_auth_statement, {
      Effect    = "Allow"
      Action    = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability"]
      Resource  = aws_ecr_repository.backend.arn
      Condition = { StringEquals = { "aws:RequestedRegion" = var.aws_region } }
    }]
  })
}

resource "aws_iam_role_policy" "github_cd_ecr" {
  name = "read-backend-image"
  role = aws_iam_role.github_cd.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [local.ecr_auth_statement, {
      Effect    = "Allow"
      Action    = ["ecr:DescribeImages", "ecr:BatchGetImage"]
      Resource  = aws_ecr_repository.backend.arn
      Condition = { StringEquals = { "aws:RequestedRegion" = var.aws_region } }
    }]
  })
}
