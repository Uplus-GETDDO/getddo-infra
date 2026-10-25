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

run "stage_database_modification_is_not_granted" {
  command = plan
  assert {
    condition = alltrue([for s in jsondecode(aws_iam_policy.staging["data"].policy).Statement :
      !anytrue([for action in s.Action : can(regex("(?i)^${replace(replace(action, "*", ".*"), "?", ".")}$", "rds:ModifyDBInstance"))]) if s.Effect == "Allow"
      ]) && alltrue([for sid in ["CreatePrivateStageDatabase", "RestorePrivateStageDatabase"] :
      length([for s in jsondecode(aws_iam_policy.staging["data"].policy).Statement : s if s.Sid == sid && s.Condition.Bool["rds:PubliclyAccessible"] == "false"]) == 1
    ])
    error_message = "Do not grant stage DB modification using an unsupported public-access condition; preserve private creation and restore."
  }
}

run "production_pre_multiaz_snapshot_boundary" {
  command = plan

  assert {
    condition = (
      one([for s in jsondecode(aws_iam_policy.operator["GetddoDevOperatorDatabase"].policy).Statement : s if s.Sid == "CreateProductionPreMultiAzSnapshot"]).Action == ["rds:CreateDBSnapshot"] &&
      toset(one([for s in jsondecode(aws_iam_policy.operator["GetddoDevOperatorDatabase"].policy).Statement : s if s.Sid == "CreateProductionPreMultiAzSnapshot"]).Resource) == toset([
        "arn:aws:rds:ap-northeast-2:942466928325:db:getddo-dev-mysql",
        "arn:aws:rds:ap-northeast-2:942466928325:snapshot:getddo-dev-mysql-pre-multiaz-*"
      ]) &&
      one([for s in jsondecode(aws_iam_policy.operator["GetddoDevOperatorDatabase"].policy).Statement : s if s.Sid == "CreateProductionPreMultiAzSnapshot"]).Condition.StringEquals["aws:RequestedRegion"] == "ap-northeast-2" &&
      !strcontains(aws_iam_policy.operator["GetddoDevOperatorDatabase"].policy, "rds:RebootDBInstance")
    )
    error_message = "운영 백업 생성은 기존 DB와 전용 snapshot 이름으로 제한하고 강제 전환 권한은 추가하지 않아야 합니다."
  }

  assert {
    condition = (
      one([for s in jsondecode(aws_iam_policy.operator["GetddoDevOperatorDatabase"].policy).Statement : s if s.Sid == "TagProductionPreMultiAzSnapshot"]).Action == ["rds:AddTagsToResource"] &&
      one([for s in jsondecode(aws_iam_policy.operator["GetddoDevOperatorDatabase"].policy).Statement : s if s.Sid == "TagProductionPreMultiAzSnapshot"]).Resource == ["arn:aws:rds:ap-northeast-2:942466928325:snapshot:getddo-dev-mysql-pre-multiaz-*"] &&
      one([for s in jsondecode(aws_iam_policy.operator["GetddoDevOperatorDatabase"].policy).Statement : s if s.Sid == "TagProductionPreMultiAzSnapshot"]).Condition.StringEquals["aws:RequestedRegion"] == "ap-northeast-2"
    )
    error_message = "자동 복사 태그 권한은 전용 사전 snapshot 접두사와 서울로만 제한해야 합니다."
  }
}

run "waf_operator_resource_boundaries" {
  command = plan

  assert {
    condition = alltrue([for s in jsondecode(aws_iam_policy.operator["GetddoDevOperatorCloudFront"].policy).Statement :
      s.Resource == "arn:aws:wafv2:us-east-1:942466928325:global/webacl/getddo-dev-api-waf/*" &&
      s.Condition.StringEquals["aws:RequestedRegion"] == "us-east-1" &&
      s.Condition.StringEquals["aws:PrincipalAccount"] == "942466928325"
      if contains(["ManageApiWebAcl", "ConfigureApiWebAclLogDestination"], s.Sid)
      ]) && (
      one([for s in jsondecode(aws_iam_policy.operator["GetddoDevOperatorCloudFront"].policy).Statement : s if s.Sid == "ListEdgeWebAcls"]).Action == ["wafv2:ListWebACLs"] &&
      one([for s in jsondecode(aws_iam_policy.operator["GetddoDevOperatorCloudFront"].policy).Statement : s if s.Sid == "ListEdgeWebAcls"]).Resource == "*" &&
      one([for s in jsondecode(aws_iam_policy.operator["GetddoDevOperatorCloudFront"].policy).Statement : s if s.Sid == "ListEdgeWebAcls"]).Condition.StringEquals["aws:RequestedRegion"] == "us-east-1" &&
      one([for s in jsondecode(aws_iam_policy.operator["GetddoDevOperatorCloudFront"].policy).Statement : s if s.Sid == "ConfigureApiWebAclLogDestination"]).Condition.ArnEquals["wafv2:LogDestinationResource"] == "arn:aws:logs:us-east-1:942466928325:log-group:aws-waf-logs-getddo-dev-api"
    )
    error_message = "WAF changes must exclude other names, accounts, regional ACLs and logging destinations; only list requires a wildcard resource."
  }

  assert {
    condition = (
      toset(one([for s in jsondecode(aws_iam_policy.operator["GetddoDevOperatorCloudFront"].policy).Statement : s if s.Sid == "ManageApiWebAcl"]).Action) == toset([
        "wafv2:CreateWebACL", "wafv2:GetWebACL", "wafv2:UpdateWebACL", "wafv2:DeleteWebACL",
        "wafv2:TagResource", "wafv2:UntagResource", "wafv2:ListTagsForResource",
        "wafv2:GetLoggingConfiguration", "wafv2:DeleteLoggingConfiguration", "wafv2:GetRateBasedStatementManagedKeys"
      ]) &&
      one([for s in jsondecode(aws_iam_policy.operator["GetddoDevOperatorCloudFront"].policy).Statement : s if s.Sid == "ConfigureApiWebAclLogDestination"]).Action == ["wafv2:PutLoggingConfiguration"]
    )
    error_message = "Grant only Web ACL lifecycle, tagging, logging and managed-IP inspection actions."
  }

  assert {
    condition = (
      alltrue([for s in jsondecode(aws_iam_policy.operator["GetddoDevOperatorObservability"].policy).Statement :
        s.Condition.StringEquals["aws:RequestedRegion"] == "us-east-1" &&
        s.Condition.StringEquals["aws:PrincipalAccount"] == "942466928325"
        if contains(["ManageApiWafLogGroup", "TagApiWafLogGroup", "ReadApiWafLogEvents", "ConfigureEdgeWafLogDelivery", "ReadEdgeWafMetrics"], s.Sid)
      ]) &&
      toset(one([for s in jsondecode(aws_iam_policy.operator["GetddoDevOperatorObservability"].policy).Statement : s if s.Sid == "ManageApiWafLogGroup"]).Resource) == toset(["arn:aws:logs:us-east-1:942466928325:log-group:aws-waf-logs-getddo-dev-api", "arn:aws:logs:us-east-1:942466928325:log-group:aws-waf-logs-getddo-dev-api:*"]) &&
      one([for s in jsondecode(aws_iam_policy.operator["GetddoDevOperatorObservability"].policy).Statement : s if s.Sid == "TagApiWafLogGroup"]).Resource == "arn:aws:logs:us-east-1:942466928325:log-group:aws-waf-logs-getddo-dev-api" &&
      one([for s in jsondecode(aws_iam_policy.operator["GetddoDevOperatorObservability"].policy).Statement : s if s.Sid == "ReadApiWafLogEvents"]).Resource == "arn:aws:logs:us-east-1:942466928325:log-group:aws-waf-logs-getddo-dev-api:log-stream:*" &&
      toset(one([for s in jsondecode(aws_iam_policy.operator["GetddoDevOperatorObservability"].policy).Statement : s if s.Sid == "ConfigureEdgeWafLogDelivery"]).Action) == toset([
        "logs:CreateLogDelivery", "logs:DeleteLogDelivery", "logs:PutResourcePolicy", "logs:DescribeResourcePolicies", "logs:DescribeLogGroups"
      ]) &&
      one([for s in jsondecode(aws_iam_policy.operator["GetddoDevOperatorObservability"].policy).Statement : s if s.Sid == "ConfigureEdgeWafLogDelivery"]).Resource == "*" &&
      one([for s in jsondecode(aws_iam_policy.operator["GetddoDevOperatorObservability"].policy).Statement : s if s.Sid == "ReadEdgeWafMetrics"]).Resource == "*" &&
      toset(one([for s in jsondecode(aws_iam_policy.operator["GetddoDevOperatorObservability"].policy).Statement : s if s.Sid == "ReadEdgeWafMetrics"]).Action) == toset(["cloudwatch:GetMetricData", "cloudwatch:GetMetricStatistics", "cloudwatch:ListMetrics"])
    )
    error_message = "Log access must exclude other groups and regions; legacy delivery APIs and metric reads require region-constrained wildcard resources."
  }
}

run "preserve_cloudfront_statements" {
  command = plan
  assert {
    condition = sha256(jsonencode([
      for s in jsondecode(aws_iam_policy.operator["GetddoDevOperatorCloudFront"].policy).Statement : s
      if !contains(["ManageApiWebAcl", "ConfigureApiWebAclLogDestination", "ListEdgeWebAcls"], s.Sid)
    ])) == "b771004aae0ae2887e006d79cf9b5146d7ebf54b3f021e392eb286da9bfcfdb2"
    error_message = "Preserve all existing cloudfront statements when adding WAF permissions."
  }
}

run "preserve_observability_statements" {
  command = plan
  assert {
    condition = sha256(jsonencode([
      for s in jsondecode(aws_iam_policy.operator["GetddoDevOperatorObservability"].policy).Statement : s
      if !contains(["ManageApiWafLogGroup", "TagApiWafLogGroup", "ReadApiWafLogEvents", "ConfigureEdgeWafLogDelivery", "ReadEdgeWafMetrics"], s.Sid)
    ])) == "fd60460f28f0851d3b649e865828e8b8327b4bf71eee2f65afb6d961ef23b57f"
    error_message = "Preserve all existing observability statements when adding WAF permissions."
  }
}

run "redis_replica_permission_boundary" {
  command = plan
  assert {
    condition = (
      one([for s in jsondecode(aws_iam_policy.operator["GetddoDevOperatorElastiCache"].policy).Statement : s if s.Sid == "IncreaseProductionRedisReplicaCount"]).Action == ["elasticache:IncreaseReplicaCount"] &&
      one([for s in jsondecode(aws_iam_policy.operator["GetddoDevOperatorElastiCache"].policy).Statement : s if s.Sid == "IncreaseProductionRedisReplicaCount"]).Resource == ["arn:aws:elasticache:ap-northeast-2:942466928325:replicationgroup:getddo-dev-redis"] &&
      one([for s in jsondecode(aws_iam_policy.operator["GetddoDevOperatorElastiCache"].policy).Statement : s if s.Sid == "IncreaseProductionRedisReplicaCount"]).Condition.StringEquals["aws:RequestedRegion"] == "ap-northeast-2" &&
      alltrue([for p in values(aws_iam_policy.operator) : !strcontains(p.policy, "elasticache:TestFailover") && !strcontains(p.policy, "elasticache:DecreaseReplicaCount")])
    )
    error_message = "복제본 증가만 정확한 운영 그룹과 서울에 허용하고 상시 전환·감소 권한을 추가하지 않아야 합니다."
  }
}
