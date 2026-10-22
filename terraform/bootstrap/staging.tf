# Pre-provisioning authority only. This root does not create any staging workload.
resource "aws_iam_group" "staging" {
  name = "getddo-staging-terraform-gyeongju"
}
resource "aws_iam_policy" "staging" {
  for_each = toset(["compute", "data", "operations", "identity", "certificate"])
  name     = "GetddoStagingOperator-${each.key}"
  policy   = file("${path.module}/templates/iam/staging/${each.key}.json")
}
resource "aws_iam_group_policy_attachment" "staging" {
  for_each   = aws_iam_policy.staging
  group      = aws_iam_group.staging.name
  policy_arn = each.value.arn
}
# Operators can use this boundary but cannot alter it or remove it from runtime roles.
resource "aws_iam_policy" "staging_runtime_boundary" {
  name   = "GetddoStagingRuntimeBoundary"
  policy = file("${path.module}/templates/iam/staging/runtime-boundary.json")
}
