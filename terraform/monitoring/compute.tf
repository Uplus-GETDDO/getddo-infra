resource "aws_instance" "monitoring" {
  ami                         = data.aws_ami.monitoring.id
  instance_type               = "t3.medium"
  subnet_id                   = data.aws_subnet.existing.id
  associate_public_ip_address = true
  vpc_security_group_ids      = [aws_security_group.monitoring.id]
  iam_instance_profile        = aws_iam_instance_profile.monitoring.name
  # Installation is a separately approved operation; no app host user_data changes.
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 2
    instance_metadata_tags      = "disabled"
  }
  root_block_device {
    encrypted             = true
    volume_type           = "gp3"
    volume_size           = 20
    delete_on_termination = true
    tags                  = { Name = "getddo-prod-monitoring-root" }
  }
  credit_specification {
    cpu_credits = "standard"
  }
  tags = { Name = "getddo-prod-monitoring" }
  lifecycle {
    # The provider aggregates tags from attached volumes; the data EBS owns its tags independently.
    ignore_changes = [volume_tags]
    precondition {
      condition = anytrue([for r in data.aws_route_table.existing.routes :
        r.cidr_block == "0.0.0.0/0" && startswith(coalesce(r.gateway_id, "none"), "igw-")
      ])
      error_message = "The selected existing subnet must have an IGW route for public IPv4 outbound."
    }
    precondition {
      condition = alltrue([for server, host in data.aws_instance.api :
        data.aws_subnet.api[server].vpc_id == data.aws_vpc.existing.id && contains(host.vpc_security_group_ids, data.aws_security_group.api.id)
      ])
      error_message = "Both API hosts must belong to the selected VPC and API security group."
    }
  }
}
