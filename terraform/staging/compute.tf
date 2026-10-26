data "aws_ami" "stage" {
  owners = ["amazon"]
  filter {
    name   = "image-id"
    values = [var.ami_id]
  }
  filter {
    name   = "architecture"
    values = ["x86_64"]
  }
  filter {
    name   = "state"
    values = ["available"]
  }
}
resource "aws_cloudwatch_log_group" "api" {
  name              = "/getddo/staging/api"
  retention_in_days = 7
  tags              = { Name = "getddo-staging-api" }
}
resource "aws_instance" "api" {
  for_each                    = toset(["a", "c"])
  ami                         = data.aws_ami.stage.id
  instance_type               = "t3.medium"
  subnet_id                   = data.aws_subnet.public[each.key].id
  associate_public_ip_address = true
  vpc_security_group_ids      = [aws_security_group.stage["api"].id]
  iam_instance_profile        = aws_iam_instance_profile.stage["api"].name
  user_data                   = join("\n", [file("${path.module}/../prod/templates/api-user-data.sh"), file("${path.module}/templates/host.sh")])
  user_data_replace_on_change = true
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 2
  }
  root_block_device {
    volume_type           = "gp3"
    volume_size           = 20
    encrypted             = true
    delete_on_termination = true
  }
  credit_specification { cpu_credits = "standard" }
  tags        = { Name = "getddo-staging-api-${each.key}" }
  volume_tags = { Name = "getddo-staging-api-${each.key}-root", Environment = "staging", RunId = var.run_id }
  depends_on  = [aws_iam_role_policy.stage, aws_cloudwatch_log_group.api]
}
resource "aws_instance" "locust" {
  private_ip                  = "10.20.0.250"
  key_name                    = "getddo-dev-api"
  ami                         = data.aws_ami.stage.id
  instance_type               = "t3.medium"
  subnet_id                   = data.aws_subnet.public["a"].id
  associate_public_ip_address = true
  vpc_security_group_ids      = [aws_security_group.stage["locust"].id]
  iam_instance_profile        = aws_iam_instance_profile.stage["locust"].name
  user_data                   = join("\n", [file("${path.module}/../prod/templates/api-user-data.sh"), file("${path.module}/templates/host.sh")])
  user_data_replace_on_change = true
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 2
  }
  root_block_device {
    volume_type           = "gp3"
    volume_size           = 20
    encrypted             = true
    delete_on_termination = true
  }
  credit_specification { cpu_credits = "standard" }
  tags        = { Name = "getddo-staging-locust" }
  volume_tags = { Name = "getddo-staging-locust-root", Environment = "staging", RunId = var.run_id }
  depends_on  = [aws_iam_role_policy.stage, aws_cloudwatch_log_group.api]
}
