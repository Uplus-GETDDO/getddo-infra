resource "aws_iam_role" "stage" {
  for_each             = toset(["api", "locust"])
  name                 = "getddo-staging-${each.key}"
  permissions_boundary = "arn:aws:iam::942466928325:policy/GetddoStagingRuntimeBoundary"
  assume_role_policy   = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Action = "sts:AssumeRole", Principal = { Service = "ec2.amazonaws.com" } }] })
  tags                 = { Name = "getddo-staging-${each.key}" }
}
resource "aws_iam_instance_profile" "stage" {
  for_each = aws_iam_role.stage
  name     = each.value.name
  role     = each.value.name
  tags     = { Name = each.value.name }
}
resource "aws_iam_role_policy" "stage" {
  for_each = aws_iam_role.stage
  name     = "staging-runtime"
  role     = each.value.name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat([
      { Effect = "Allow", Action = ["ssm:UpdateInstanceInformation", "ssmmessages:CreateControlChannel", "ssmmessages:CreateDataChannel", "ssmmessages:OpenControlChannel", "ssmmessages:OpenDataChannel"], Resource = "*" },
      { Effect = "Allow", Action = ["ssm:GetParameter", "ssm:GetParameters"], Resource = "arn:aws:ssm:ap-northeast-2:942466928325:parameter/getddo/staging/${each.key == "api" ? "app" : "locust"}/*" },
      {
        Sid = "StageReadArtifacts", Effect = "Allow", Action = ["s3:GetObject"]
        Resource = each.key == "api" ? [
          "arn:aws:s3:::getddo-deploy-942466928325-ap-northeast-2/staging/${var.run_id}/releases/*",
          "arn:aws:s3:::getddo-deploy-942466928325-ap-northeast-2/staging/runs/${var.run_id}/runtime/*"
        ] : ["arn:aws:s3:::getddo-deploy-942466928325-ap-northeast-2/staging/runs/${var.run_id}/runtime/*"]
      },
      { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"], Resource = "arn:aws:logs:ap-northeast-2:942466928325:log-group:/getddo/staging/*:*" },
      { Effect = "Allow", Action = ["cloudwatch:PutMetricData"], Resource = "*", Condition = { StringEquals = {
        "cloudwatch:namespace" = each.key == "api" ? ["GETDDO/API", "CWAgent"] : ["CWAgent"], "aws:RequestedRegion" = "ap-northeast-2"
      } } }
      ], each.key == "locust" ? [{
        Sid      = "LocustWriteReports", Effect = "Allow", Action = ["s3:PutObject"]
        Resource = "arn:aws:s3:::getddo-deploy-942466928325-ap-northeast-2/staging/runs/${var.run_id}/results/locust-*"
    }] : [])
  })
}
