# Existing networks are lookup-only; never import them into the workload State.
data "aws_vpc" "existing" {
  filter {
    name   = "tag:Name"
    values = ["getddo-dev-vpc"]
  }
  lifecycle {
    postcondition {
      condition     = self.id == "vpc-0fcf829db1692b713"
      error_message = "Existing VPC identity changed; review network and IAM before running."
    }
  }
}
data "aws_subnet" "public" {
  for_each = toset(["a", "c"])
  filter {
    name   = "tag:Name"
    values = ["getddo-dev-public-${each.key}"]
  }
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.existing.id]
  }
  lifecycle {
    postcondition {
      condition     = self.availability_zone == "ap-northeast-2${each.key}"
      error_message = "The existing public subnet must match the API availability zone."
    }
  }
}
data "aws_subnet" "data" {
  for_each = toset(["a", "c"])
  filter {
    name   = "tag:Name"
    values = ["getddo-dev-data-${each.key}"]
  }
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.existing.id]
  }
  lifecycle {
    postcondition {
      condition     = !self.map_public_ip_on_launch && self.availability_zone == "ap-northeast-2${each.key}"
      error_message = "Existing data subnets must remain private in the expected zones."
    }
  }
}
data "aws_subnet" "alb" {
  for_each = toset(["a", "c"])
  filter {
    name   = "tag:Name"
    values = ["getddo-dev-alb-${each.key}"]
  }
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.existing.id]
  }
  lifecycle {
    postcondition {
      condition     = !self.map_public_ip_on_launch && self.availability_zone == "ap-northeast-2${each.key}"
      error_message = "Existing ALB subnets must remain private in the expected zones."
    }
  }
}
data "aws_security_group" "monitor" {
  id = var.monitoring_security_group_id
  lifecycle {
    postcondition {
      condition     = self.vpc_id == data.aws_vpc.existing.id
      error_message = "Monitor must use the same existing VPC."
    }
  }
}
data "aws_security_group" "shared_alb" {
  id = var.shared_alb_security_group_id
  lifecycle {
    postcondition {
      condition     = self.vpc_id == data.aws_vpc.existing.id
      error_message = "The existing HTTPS ALB must use the same VPC."
    }
  }
}
