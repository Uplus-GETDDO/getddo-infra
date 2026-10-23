mock_provider "aws" {}
override_resource {
  override_during = plan
  target          = aws_iam_role.staging_control["scheduler"]
  values          = { arn = "arn:aws:iam::942466928325:role/getddo-staging-scheduler" }
}
run "control_trust_and_privilege_scope" {
  command = plan
  assert {
    condition = (
      length([for statement in jsondecode(aws_iam_group_policy.staging_control_operator.policy).Statement : statement if contains(try(tolist(statement.Action), [statement.Action]), "ssm:DescribeParameters")]) == 1 &&
      !strcontains(aws_iam_group_policy.staging_control_operator.policy, "staging-control/terraform.tfstate")
    )
    error_message = "Submissions need SSM metadata reads, while persistent control State is administrator-owned."
  }
  assert {
    condition = alltrue([for key, role in aws_iam_role.staging_control :
      jsondecode(role.assume_role_policy).Statement[0].Condition.StringEquals["aws:SourceAccount"] == "942466928325" &&
      jsondecode(role.assume_role_policy).Statement[0].Condition.ArnEquals["aws:SourceArn"] == local.staging_control_services[key].source
    ])
    error_message = "Control services must have account and exact project/group/rule trust constraints."
  }
  assert {
    condition = alltrue([for policy in aws_iam_role_policy.staging_control_start_cleanup :
      jsondecode(policy.policy).Statement[0].Action == "codebuild:StartBuild" &&
      jsondecode(policy.policy).Statement[0].Resource == "arn:aws:codebuild:ap-northeast-2:942466928325:project/getddo-staging-cleanup"
    ])
    error_message = "Scheduler and failure events may start only the cleanup project."
  }
  assert {
    condition = (
      length(jsonencode(jsondecode(aws_iam_policy.staging_control_runtime.policy))) < 6144 &&
      alltrue([for statement in jsondecode(aws_iam_policy.staging_control_runtime.policy).Statement :
        !contains(try(tolist(statement.Action), [statement.Action]), "iam:PassRole") &&
        !contains(try(tolist(statement.Action), [statement.Action]), "iam:PutRolePolicy")
      ]) &&
      [for s in jsondecode(aws_iam_policy.staging_control_runtime.policy).Statement : s if s.Sid == "PreservePersistentLoginConfiguration"][0].Effect == "Deny"
    )
    error_message = "Runtime policy must fit IAM size limits, preserve login credentials, and not grant control IAM editing."
  }
  assert {
    condition = alltrue([for path in [
      "arn:aws:ssm:ap-northeast-2:942466928325:parameter/getddo/staging",
      "arn:aws:ssm:ap-northeast-2:942466928325:parameter/getddo/staging/locust",
      "arn:aws:ssm:ap-northeast-2:942466928325:parameter/getddo/staging/locust/*"
    ] : contains(one([for s in jsondecode(aws_iam_policy.staging_control_runtime.policy).Statement : s.Resource if s.Sid == "PreservePersistentLoginConfiguration"]), path)])
    error_message = "Parent hierarchy reads must not bypass persistent Locust credential preservation."
  }
  assert {
    condition = (
      [for s in jsondecode(aws_iam_policy.staging_control_ui.policy).Statement : s if s.Sid == "CreateStageUiRule"][0].Resource == "arn:aws:elasticloadbalancing:ap-northeast-2:942466928325:listener/app/getddo-dev-api-alb/9e65e06e10492070/96403935397fdc93" &&
      [for s in jsondecode(aws_iam_policy.staging_control_ui.policy).Statement : s if s.Sid == "RevokeSharedSgEgress"][0].Resource == "arn:aws:ec2:ap-northeast-2:942466928325:security-group/sg-09e12310ecd082ffb" &&
      [for s in jsondecode(aws_iam_policy.staging_control_ui.policy).Statement : s if s.Sid == "StageMonitorSgEgress"][0].Resource == "arn:aws:ec2:ap-northeast-2:942466928325:security-group/sg-04954d4932de5fa69"
    )
    error_message = "Shared UI mutations must be limited to the reviewed listener and SG."
  }
}

run "terminal_tunnel_targets_only_fixed_stage_locust" {
  command = plan
  assert {
    condition = (
      length([for statement in jsondecode(aws_iam_group_policy.staging_terminal.policy).Statement : statement if statement.Action == "ec2-instance-connect:OpenTunnel"]) == 1 &&
      one([for statement in jsondecode(aws_iam_group_policy.staging_terminal.policy).Statement : statement if statement.Action == "ec2-instance-connect:OpenTunnel"]).Effect == "Allow" &&
      one([for statement in jsondecode(aws_iam_group_policy.staging_terminal.policy).Statement : statement if statement.Action == "ec2-instance-connect:OpenTunnel"]).Resource == "arn:aws:ec2:ap-northeast-2:942466928325:instance-connect-endpoint/eice-0faadf70e78c2a080" &&
      one([for statement in jsondecode(aws_iam_group_policy.staging_terminal.policy).Statement : statement if statement.Action == "ec2-instance-connect:OpenTunnel"]).Condition.NumericEquals["ec2-instance-connect:remotePort"] == "22" &&
      toset(one([for statement in jsondecode(aws_iam_group_policy.staging_terminal.policy).Statement : statement if statement.Action == "ec2-instance-connect:OpenTunnel"]).Condition.IpAddress["ec2-instance-connect:privateIpAddress"]) == toset(["10.20.0.250/32"])
    )
    error_message = "The Stage terminal policy must grant only port 22 on the fixed Locust /32 through the existing endpoint, excluding production subnet ranges."
  }
}
