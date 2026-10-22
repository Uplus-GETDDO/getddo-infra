resource "aws_iam_role" "monitoring" {
  name = "getddo-prod-monitoring"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow", Action = "sts:AssumeRole"
      Principal = { Service = "ec2.amazonaws.com" }
    }]
  })
}
resource "aws_iam_instance_profile" "monitoring" {
  name = "getddo-prod-monitoring"
  role = aws_iam_role.monitoring.name
}
# ManagedInstanceCore also grants GetParameter(s) on *, including app secrets.
# Session Manager connectivity only; no Run Command, parameter hierarchy or app reads.
resource "aws_iam_role_policy" "ssm" {
  name = "session-manager-connectivity"
  role = aws_iam_role.monitoring.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "ssm:UpdateInstanceInformation",
        "ssmmessages:CreateControlChannel", "ssmmessages:CreateDataChannel",
        "ssmmessages:OpenControlChannel", "ssmmessages:OpenDataChannel",
        "ec2messages:AcknowledgeMessage", "ec2messages:DeleteMessage",
        "ec2messages:FailMessage", "ec2messages:GetEndpoint",
        "ec2messages:GetMessages", "ec2messages:SendReply"
      ]
      Resource = "*"
    }]
  })
}
resource "aws_iam_role_policy" "read" {
  name = "monitoring-read"
  role = aws_iam_role.monitoring.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # CloudWatch metric read/list APIs do not support resource ARN scoping.
        Effect   = "Allow"
        Action   = ["cloudwatch:GetMetricData", "cloudwatch:GetMetricStatistics", "cloudwatch:ListMetrics"]
        Resource = "*"
      },
      {
        Effect   = "Allow", Action = ["logs:DescribeLogGroups", "logs:StopQuery"]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = ["logs:DescribeLogStreams", "logs:GetLogEvents", "logs:FilterLogEvents", "logs:StartQuery", "logs:GetQueryResults", "logs:GetLogGroupFields"]
        Resource = [
          "arn:aws:logs:ap-northeast-2:942466928325:log-group:/getddo/dev/api",
          "arn:aws:logs:ap-northeast-2:942466928325:log-group:/getddo/dev/api:*",
          "arn:aws:logs:ap-northeast-2:942466928325:log-group:/getddo/staging/*"
        ]
      },
      {
        Effect   = "Allow", Action = ["ssm:GetParameter"]
        Resource = "arn:aws:ssm:ap-northeast-2:942466928325:parameter${local.grafana_password_path}"
      }
    ]
  })
}
