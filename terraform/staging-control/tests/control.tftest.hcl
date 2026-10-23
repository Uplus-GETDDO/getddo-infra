mock_provider "aws" {
  mock_resource "aws_codebuild_project" {
    defaults = { arn = "arn:aws:codebuild:ap-northeast-2:942466928325:project/getddo-staging-test" }
  }
}
run "persistent_independent_executors" {
  command = apply
  assert {
    condition = (
      length(aws_codebuild_project.stage) == 2 &&
      aws_codebuild_project.stage["up"].service_role == "arn:aws:iam::942466928325:role/getddo-staging-codebuild-up" &&
      aws_codebuild_project.stage["cleanup"].service_role == "arn:aws:iam::942466928325:role/getddo-staging-codebuild-cleanup" &&
      alltrue([for project in aws_codebuild_project.stage : length(project.vpc_config) == 0 && !project.environment[0].privileged_mode && project.concurrent_build_limit == 1]) &&
      aws_scheduler_schedule_group.stage.name == "getddo-staging"
    )
    error_message = "Independent cleanup must persist outside workload State, without VPC NAT or privileged Docker."
  }
  assert {
    condition = (
      jsondecode(aws_cloudwatch_event_rule.up_failed.event_pattern).detail["project-name"] == ["getddo-staging-up"] &&
      aws_cloudwatch_event_target.cleanup.arn == aws_codebuild_project.stage["cleanup"].arn &&
      strcontains(aws_cloudwatch_event_target.cleanup.input_transformer[0].input_template, "FAILED_BUILD_ID")
    )
    error_message = "Only provisioning failures can trigger cleanup; cleanup failures must not recurse."
  }
}
