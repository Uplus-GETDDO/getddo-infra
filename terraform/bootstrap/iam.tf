# Existing operator permissions. These declarations do NOT import them automatically.
# Existing objects were imported on 2026-10-21; review docs/state-adoption.md before apply.
locals {
  operator_manifest = jsondecode(file("${path.module}/templates/iam/operator/manifest.json"))
  operator_policies = { for policy in local.operator_manifest.policies : policy.name => policy }
}
resource "aws_iam_group" "operator" {
  name = local.operator_manifest.group_name
}
resource "aws_iam_policy" "operator" {
  for_each    = local.operator_policies
  name        = each.key
  description = try(each.value.description, null)
  policy      = file("${path.module}/templates/iam/operator/${each.value.file}")
}
resource "aws_iam_group_policy_attachment" "operator" {
  for_each   = local.operator_policies
  group      = aws_iam_group.operator.name
  policy_arn = aws_iam_policy.operator[each.key].arn
}
# Import the existing user/group relationship without managing other group members.
# aws_iam_group_membership does not support import; permissions remain unchanged.
resource "aws_iam_user_group_membership" "operator" {
  user   = local.operator_manifest.user_name
  groups = [aws_iam_group.operator.name, aws_iam_group.staging.name]
}
resource "aws_iam_group_policy" "operator_two_api" {
  name   = "two-api-management"
  group  = aws_iam_group.operator.name
  policy = file("${path.module}/templates/iam/operator/two-api-management.json")
}
