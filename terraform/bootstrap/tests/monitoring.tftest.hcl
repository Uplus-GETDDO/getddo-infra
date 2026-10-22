mock_provider "aws" {}
run "monitoring_plan_permissions_only" {
  command = plan
  assert {
    condition = alltrue([for statement in jsondecode(aws_iam_group_policy.monitoring_plan.policy).Statement :
      !contains(statement.Action, "iam:PassRole") && !contains(statement.Action, "ec2:RunInstances") && !contains(statement.Action, "ssm:PutParameter")
    ])
    error_message = "Preparation must not add execution or secret-registration grants."
  }
  assert {
    condition     = jsondecode(aws_iam_group_policy.monitoring_plan.policy).Statement[1].Resource == "arn:aws:s3:::getddo-tfstate-942466928325-ap-northeast-2/monitoring/terraform.tfstate.tflock"
    error_message = "Only the lock object may be deleted, never the monitoring State."
  }
}
