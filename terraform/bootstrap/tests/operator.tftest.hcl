mock_provider "aws" {
  mock_data "aws_caller_identity" { defaults = { account_id = "942466928325" } }
}
# Distinct valid ARNs let plan assertions detect policies attached under the wrong key.
override_resource {
  override_during = plan
  target          = aws_iam_policy.operator["GetddoDevOperatorObservability"]
  values          = { arn = "arn:aws:iam::942466928325:policy/GetddoDevOperatorObservability" }
}

override_resource {
  override_during = plan
  target          = aws_iam_policy.operator["GetddoDevOperatorElastiCache"]
  values          = { arn = "arn:aws:iam::942466928325:policy/GetddoDevOperatorElastiCache" }
}

override_resource {
  override_during = plan
  target          = aws_iam_policy.operator["GetddoDevOperatorParameters"]
  values          = { arn = "arn:aws:iam::942466928325:policy/GetddoDevOperatorParameters" }
}

override_resource {
  override_during = plan
  target          = aws_iam_policy.operator["GetddoDevOperatorLoadBalancing"]
  values          = { arn = "arn:aws:iam::942466928325:policy/GetddoDevOperatorLoadBalancing" }
}

override_resource {
  override_during = plan
  target          = aws_iam_policy.operator["GetddoDevOperatorCloudFront"]
  values          = { arn = "arn:aws:iam::942466928325:policy/GetddoDevOperatorCloudFront" }
}

override_resource {
  override_during = plan
  target          = aws_iam_policy.operator["GetddoDevOperatorNetwork"]
  values          = { arn = "arn:aws:iam::942466928325:policy/GetddoDevOperatorNetwork" }
}

override_resource {
  override_during = plan
  target          = aws_iam_policy.operator["GetddoDevOperatorDatabase"]
  values          = { arn = "arn:aws:iam::942466928325:policy/GetddoDevOperatorDatabase" }
}

override_resource {
  override_during = plan
  target          = aws_iam_policy.operator["GetddoDevOperatorStorage"]
  values          = { arn = "arn:aws:iam::942466928325:policy/GetddoDevOperatorStorage" }
}

override_resource {
  override_during = plan
  target          = aws_iam_policy.operator["GetddoDevOperatorIdentity"]
  values          = { arn = "arn:aws:iam::942466928325:policy/GetddoDevOperatorIdentity" }
}

override_resource {
  override_during = plan
  target          = aws_iam_policy.operator["GetddoDevOperatorOperations"]
  values          = { arn = "arn:aws:iam::942466928325:policy/GetddoDevOperatorOperations" }
}

run "existing_operator_policy_boundary" {
  command = plan
  assert {
    condition     = length(aws_iam_policy.operator) == 10 && aws_iam_group.operator.name == "getddo-dev-terraform-gyeongju" && aws_iam_user_group_membership.operator.user == "gyeongju" && aws_iam_user_group_membership.operator.groups == toset(["getddo-dev-terraform-gyeongju", "getddo-staging-terraform-gyeongju"])
    error_message = "Preserve ten independent policies and the dedicated gyeongju group."
  }
  assert {
    condition = (
      aws_iam_policy.operator["GetddoDevOperatorObservability"].description == "GETDDO development CloudWatch metrics, logs and dashboard management" &&
      aws_iam_policy.operator["GetddoDevOperatorElastiCache"].description == "GETDDO dev node-based ElastiCache provisioning in Seoul"
    )
    error_message = "Preserve existing descriptions: removing them would replace imported IAM policies."
  }
  assert {
    condition     = jsondecode(aws_iam_group_policy.operator_two_api.policy).Statement[0].Condition.StringEquals["ssm:resourceTag/Name"] == "getddo-dev-api-c"
    error_message = "Keep second-server management scoped to its existing Name condition."
  }
}

run "operator_attachment_mapping" {
  command = plan
  assert {
    condition = (
      toset(keys(aws_iam_group_policy_attachment.operator)) == toset(keys(aws_iam_policy.operator)) &&
      alltrue([for name, attachment in aws_iam_group_policy_attachment.operator :
        attachment.group == aws_iam_group.operator.name && attachment.policy_arn == aws_iam_policy.operator[name].arn
      ])
    )
    error_message = "Every operator policy must stay attached to its matching manifest key and dedicated group."
  }
}

run "staging_operator_boundary" {
  command = plan
  assert {
    condition     = aws_iam_group.staging.name == "getddo-staging-terraform-gyeongju" && length(aws_iam_group_policy_attachment.staging) == 5 && aws_iam_policy.staging_runtime_boundary.name == "GetddoStagingRuntimeBoundary"
    error_message = "Keep staging policies on the same user's separate group without exceeding the existing group's attachment quota."
  }
  assert {
    condition = jsondecode(aws_iam_policy.staging["identity"].policy).Statement[0].Condition.StringEquals["iam:PermissionsBoundary"] == "arn:aws:iam::942466928325:policy/GetddoStagingRuntimeBoundary" && alltrue(flatten([for policy in concat(values(aws_iam_policy.staging), values(aws_iam_policy.operator)) : [
      for statement in jsondecode(policy.policy).Statement : [
        for action in statement.Action : !anytrue([for forbidden in ["iam:DeleteRolePermissionsBoundary", "iam:PutRolePermissionsBoundary", "iam:CreatePolicyVersion", "iam:SetDefaultPolicyVersion", "iam:DeletePolicy", "iam:DeletePolicyVersion"] : can(regex("(?i)^${replace(replace(action, "*", ".*"), "?", ".")}$", forbidden))])
      ] if statement.Effect == "Allow"
    ]]))
    error_message = "Runtime role creation must require a fixed boundary that the operator cannot remove or edit."
  }
  assert {
    condition = alltrue([for s in jsondecode(aws_iam_policy.staging["operations"].policy).Statement :
      !anytrue([for action in s.Action : can(regex("(?i)^${replace(replace(action, "*", ".*"), "?", ".")}$", "s3:DeleteObject"))]) || alltrue([for arn in try(tolist(s.Resource), [tostring(s.Resource)]) : contains(["arn:aws:s3:::getddo-tfstate-942466928325-ap-northeast-2/staging/terraform.tfstate.tflock", "arn:aws:s3:::getddo-deploy-942466928325-ap-northeast-2/staging/*"], arn)])
    ])
    error_message = "Staging cleanup may release locks and artifacts but must not delete State."
  }
}

run "protect_resource_identity_tags" {
  command = plan
  assert {
    condition     = length([for s in jsondecode(aws_iam_policy.staging["compute"].policy).Statement : s if s.Effect == "Deny" && contains(s.Action, "ec2:CreateTags") && contains(s.Action, "ec2:DeleteTags") && contains(s.Condition.StringNotEqualsIfExists["ec2:CreateAction"], "RunInstances") && !contains(s.Condition.StringNotEqualsIfExists["ec2:CreateAction"], "CreateTags") && s.Condition.StringNotEqualsIfExists["ec2:ResourceTag/Environment"] == "staging"]) == 1
    error_message = "Existing production resources must not be relabeled to acquire new staging lifecycle permissions."
  }
}
