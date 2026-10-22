# Existing production infrastructure retains its legacy AWS names and dev State key.
# Read individual resources; never consume the application's entire remote State.
data "aws_vpc" "existing" {
  tags = { Name = "getddo-dev-vpc" }
}
data "aws_subnet" "existing" {
  vpc_id = data.aws_vpc.existing.id
  tags   = { Name = "getddo-dev-public-a" }
}
data "aws_route_table" "existing" {
  subnet_id = data.aws_subnet.existing.id
}
data "aws_security_group" "api" {
  vpc_id = data.aws_vpc.existing.id
  name   = "getddo-dev-api"
}
data "aws_instance" "api" {
  for_each = { A = "getddo-dev-api", C = "getddo-dev-api-c" }
  filter {
    name   = "tag:Name"
    values = [each.value]
  }
  filter {
    name   = "instance-state-name"
    values = ["running"]
  }
}
data "aws_ami" "monitoring" {
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
data "aws_subnet" "api" {
  for_each = data.aws_instance.api
  id       = each.value.subnet_id
}
