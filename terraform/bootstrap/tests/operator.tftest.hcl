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
    condition     = length(aws_iam_policy.operator) == 10 && aws_iam_group.operator.name == "getddo-dev-terraform-gyeongju" && aws_iam_user_group_membership.operator.user == "gyeongju" && aws_iam_user_group_membership.operator.groups == toset(["getddo-dev-terraform-gyeongju"])
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
