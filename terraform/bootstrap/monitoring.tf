# Preparation only: enable the existing operator to plan the independent root.
# Applying this policy and provisioning/session/secret-registration permissions are GD-299 gates.
resource "aws_iam_group_policy" "monitoring_plan" {
  name  = "monitoring-plan"
  group = aws_iam_group.operator.name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = ["s3:GetObject", "s3:PutObject"]
        Resource = [
          "arn:aws:s3:::getddo-tfstate-942466928325-ap-northeast-2/monitoring/terraform.tfstate",
          "arn:aws:s3:::getddo-tfstate-942466928325-ap-northeast-2/monitoring/terraform.tfstate.tflock"
        ]
      },
      {
        Effect   = "Allow", Action = ["s3:DeleteObject"]
        Resource = "arn:aws:s3:::getddo-tfstate-942466928325-ap-northeast-2/monitoring/terraform.tfstate.tflock"
      },
      {
        Effect   = "Allow"
        Action   = ["iam:GetRole", "iam:GetRolePolicy", "iam:ListRolePolicies", "iam:ListAttachedRolePolicies", "iam:ListInstanceProfilesForRole"]
        Resource = "arn:aws:iam::942466928325:role/getddo-prod-monitoring"
      },
      {
        Effect   = "Allow", Action = ["iam:GetInstanceProfile", "iam:ListInstanceProfileTags"]
        Resource = "arn:aws:iam::942466928325:instance-profile/getddo-prod-monitoring"
      }
    ]
  })
}
