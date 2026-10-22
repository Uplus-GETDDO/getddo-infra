# Root-provisioned service roles. CodeBuild cannot alter its own trust or policies.
locals {
  staging_control_services = {
    up        = { principal = "codebuild.amazonaws.com", source = "arn:aws:codebuild:ap-northeast-2:942466928325:project/getddo-staging-up", name = "getddo-staging-codebuild-up" }
    cleanup   = { principal = "codebuild.amazonaws.com", source = "arn:aws:codebuild:ap-northeast-2:942466928325:project/getddo-staging-cleanup", name = "getddo-staging-codebuild-cleanup" }
    scheduler = { principal = "scheduler.amazonaws.com", source = "arn:aws:scheduler:ap-northeast-2:942466928325:schedule-group/getddo-staging", name = "getddo-staging-scheduler" }
    events    = { principal = "events.amazonaws.com", source = "arn:aws:events:ap-northeast-2:942466928325:rule/getddo-staging-up-failed", name = "getddo-staging-failure-events" }
  }
}
resource "aws_iam_role" "staging_control" {
  for_each = local.staging_control_services
  name     = each.value.name
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "sts:AssumeRole", Principal = { Service = each.value.principal }, Condition = {
      StringEquals = { "aws:SourceAccount" = "942466928325" }
      ArnEquals    = { "aws:SourceArn" = each.value.source }
    } }]
  })
  tags = { Environment = "staging", Lifecycle = "persistent-control" }
}
resource "aws_iam_role_policy_attachment" "staging_control_workload" {
  for_each   = { for pair in setproduct(["up", "cleanup"], ["compute", "data", "operations", "identity"]) : "${pair[0]}-${pair[1]}" => pair }
  role       = aws_iam_role.staging_control[each.value[0]].name
  policy_arn = aws_iam_policy.staging[each.value[1]].arn
}
resource "aws_iam_policy" "staging_control_runtime" {
  name   = "GetddoStagingControlRuntime"
  policy = file("${path.module}/templates/iam/staging/control-runtime.json")
}
resource "aws_iam_role_policy_attachment" "staging_control_runtime" {
  for_each   = toset(["up", "cleanup"])
  role       = aws_iam_role.staging_control[each.key].name
  policy_arn = aws_iam_policy.staging_control_runtime.arn
}
resource "aws_iam_role_policy" "staging_control_start_cleanup" {
  for_each = toset(["scheduler", "events"])
  name     = "start-only-staging-cleanup"
  role     = aws_iam_role.staging_control[each.key].id
  policy   = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Action = "codebuild:StartBuild", Resource = "arn:aws:codebuild:ap-northeast-2:942466928325:project/getddo-staging-cleanup" }] })
}
resource "aws_iam_group_policy" "staging_control_operator" {
  name  = "staging-execution-control"
  group = aws_iam_group.staging.name
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject"], Resource = ["arn:aws:s3:::getddo-tfstate-942466928325-ap-northeast-2/staging-control/terraform.tfstate", "arn:aws:s3:::getddo-tfstate-942466928325-ap-northeast-2/staging-control/terraform.tfstate.tflock"] },
    { Effect = "Allow", Action = "s3:DeleteObject", Resource = "arn:aws:s3:::getddo-tfstate-942466928325-ap-northeast-2/staging-control/terraform.tfstate.tflock" },
    { Effect = "Allow", Action = ["codebuild:StartBuild", "codebuild:BatchGetBuilds", "codebuild:ListBuildsForProject"], Resource = ["arn:aws:codebuild:ap-northeast-2:942466928325:project/getddo-staging-up", "arn:aws:codebuild:ap-northeast-2:942466928325:project/getddo-staging-cleanup"] },
    { Effect = "Allow", Action = ["scheduler:CreateSchedule", "scheduler:GetSchedule", "scheduler:DeleteSchedule"], Resource = "arn:aws:scheduler:ap-northeast-2:942466928325:schedule/getddo-staging/expiry-*" },
    { Effect = "Allow", Action = "iam:PassRole", Resource = aws_iam_role.staging_control["scheduler"].arn, Condition = { StringEquals = { "iam:PassedToService" = "scheduler.amazonaws.com" } } }
  ] })
}

# Shared resources remain owned by Prod. Permit only Stage-tagged UI additions.
resource "aws_iam_policy" "staging_control_ui" {
  name = "GetddoStagingControlUI"
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    {
      Sid       = "CreateStageUiRule", Effect = "Allow", Action = "elasticloadbalancing:CreateRule",
      Resource  = "arn:aws:elasticloadbalancing:ap-northeast-2:942466928325:listener/app/getddo-dev-api-alb/9e65e06e10492070/96403935397fdc93",
      Condition = { StringEquals = { "aws:RequestTag/Environment" = "staging" } }
    },
    {
      Sid       = "TagStageUiRuleOnCreation", Effect = "Allow", Action = "elasticloadbalancing:AddTags",
      Resource  = "arn:aws:elasticloadbalancing:ap-northeast-2:942466928325:listener-rule/app/getddo-dev-api-alb/9e65e06e10492070/96403935397fdc93/*",
      Condition = { StringEquals = { "elasticloadbalancing:CreateAction" = "CreateRule", "aws:RequestTag/Environment" = "staging" } }
    },
    {
      Sid       = "DeleteStageUiRule", Effect = "Allow", Action = ["elasticloadbalancing:DeleteRule", "elasticloadbalancing:AddTags", "elasticloadbalancing:RemoveTags"],
      Resource  = "arn:aws:elasticloadbalancing:ap-northeast-2:942466928325:listener-rule/app/getddo-dev-api-alb/9e65e06e10492070/96403935397fdc93/*",
      Condition = { StringEquals = { "aws:ResourceTag/Environment" = "staging" } }
    },
    {
      Sid      = "StageUiSharedSgEgress", Effect = "Allow", Action = "ec2:AuthorizeSecurityGroupEgress",
      Resource = "arn:aws:ec2:ap-northeast-2:942466928325:security-group/sg-09e12310ecd082ffb"
    },
    {
      Sid       = "CreateStageTaggedSecurityRules", Effect = "Allow", Action = ["ec2:AuthorizeSecurityGroupIngress", "ec2:AuthorizeSecurityGroupEgress"],
      Resource  = "arn:aws:ec2:ap-northeast-2:942466928325:security-group-rule/*",
      Condition = { StringEquals = { "aws:RequestTag/Environment" = "staging" } }
    },
    {
      Sid      = "RevokeSharedSgEgress", Effect = "Allow", Action = "ec2:RevokeSecurityGroupEgress",
      Resource = "arn:aws:ec2:ap-northeast-2:942466928325:security-group/sg-09e12310ecd082ffb"
    }
  ] })
}
resource "aws_iam_role_policy_attachment" "staging_control_ui" {
  for_each   = toset(["up", "cleanup"])
  role       = aws_iam_role.staging_control[each.key].name
  policy_arn = aws_iam_policy.staging_control_ui.arn
}
