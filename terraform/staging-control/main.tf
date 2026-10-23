# IAM service roles are provisioned by bootstrap/root, never by the runner itself.
locals {
  account = "942466928325"
  region  = "ap-northeast-2"
  buildspec = yamlencode({
    version = "0.2"
    phases = {
      install = { "runtime-versions" = { python = "3.12" }, commands = [
        "set -eu",
        "curl -fsSLo /tmp/terraform_1.16.0_linux_amd64.zip https://releases.hashicorp.com/terraform/1.16.0/terraform_1.16.0_linux_amd64.zip",
        "curl -fsSLo /tmp/terraform.sha https://releases.hashicorp.com/terraform/1.16.0/terraform_1.16.0_SHA256SUMS",
        "cd /tmp && grep ' terraform_1.16.0_linux_amd64.zip$' terraform.sha | sha256sum -c -",
        "unzip -o /tmp/terraform_1.16.0_linux_amd64.zip -d /usr/local/bin",
        "python3 -m pip install --quiet boto3==1.40.59",
      ] }
      build = { commands = ["printf '%s' '${base64encode(file("${path.module}/bootstrap.py"))}' | base64 -d > /tmp/staging-bootstrap.py", "python3 /tmp/staging-bootstrap.py"] }
    }
  })
}
resource "aws_scheduler_schedule_group" "stage" { name = "getddo-staging" }
resource "aws_cloudwatch_log_group" "build" {
  for_each          = toset(["up", "cleanup"])
  name              = "/getddo/staging/control/${each.key}"
  retention_in_days = 14
}
resource "aws_codebuild_project" "stage" {
  for_each               = toset(["up", "cleanup"])
  name                   = "getddo-staging-${each.key}"
  service_role           = "arn:aws:iam::942466928325:role/getddo-staging-codebuild-${each.key}"
  build_timeout          = 120
  queued_timeout         = 15
  concurrent_build_limit = 1
  artifacts { type = "NO_ARTIFACTS" }
  source {
    type      = "NO_SOURCE"
    buildspec = local.buildspec
  }
  environment {
    type            = "LINUX_CONTAINER"
    compute_type    = "BUILD_GENERAL1_SMALL"
    image           = "aws/codebuild/standard:7.0"
    privileged_mode = false
    environment_variable {
      name  = "ACTION"
      value = each.key == "up" ? "up" : "rollback"
    }
    environment_variable {
      name  = "RUN_ID"
      value = ""
    }
  }
  logs_config {
    cloudwatch_logs {
      group_name = aws_cloudwatch_log_group.build[each.key].name
      status     = "ENABLED"
    }
  }
}
# Match only provisioning failures; cleanup failures cannot recursively spawn builds.
resource "aws_cloudwatch_event_rule" "up_failed" {
  name = "getddo-staging-up-failed"
  event_pattern = jsonencode({
    source        = ["aws.codebuild"]
    "detail-type" = ["CodeBuild Build State Change"]
    detail        = { "project-name" = [aws_codebuild_project.stage["up"].name], "build-status" = ["FAILED", "FAULT", "STOPPED", "TIMED_OUT"] }
  })
}
resource "aws_cloudwatch_event_target" "cleanup" {
  rule      = aws_cloudwatch_event_rule.up_failed.name
  target_id = "staging-cleanup"
  arn       = aws_codebuild_project.stage["cleanup"].arn
  role_arn  = "arn:aws:iam::942466928325:role/getddo-staging-failure-events"
  input_transformer {
    input_paths    = { build = "$.detail.build-id" }
    input_template = "{\"environmentVariablesOverride\":[{\"name\":\"ACTION\",\"value\":\"rollback\",\"type\":\"PLAINTEXT\"},{\"name\":\"FAILED_BUILD_ID\",\"value\":<build>,\"type\":\"PLAINTEXT\"}]}"
  }
  retry_policy {
    maximum_event_age_in_seconds = 86400
    maximum_retry_attempts       = 185
  }
}
output "execution" {
  value = {
    up_project      = aws_codebuild_project.stage["up"].name
    cleanup_project = aws_codebuild_project.stage["cleanup"].name
    schedule_group  = aws_scheduler_schedule_group.stage.name
  }
}
