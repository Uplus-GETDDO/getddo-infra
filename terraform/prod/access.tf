# 로컬 22번 포트 접속이 막힌 환경에서도 AWS HTTPS 터널로 SSH를 사용한다.
resource "aws_security_group" "ssh_endpoint" {
  name        = "getddo-dev-eice"
  description = "EC2 Instance Connect Endpoint to the API SSH port only"
  vpc_id      = aws_vpc.main.id
  tags        = { Name = "getddo-dev-eice-sg" }
}

resource "aws_vpc_security_group_egress_rule" "ssh_endpoint_to_api" {
  security_group_id            = aws_security_group.ssh_endpoint.id
  referenced_security_group_id = aws_security_group.api.id
  ip_protocol                  = "tcp"
  from_port                    = 22
  to_port                      = 22
  tags                         = { Name = "getddo-dev-eice-to-api" }
}

resource "aws_vpc_security_group_ingress_rule" "api_from_ssh_endpoint" {
  security_group_id            = aws_security_group.api.id
  referenced_security_group_id = aws_security_group.ssh_endpoint.id
  ip_protocol                  = "tcp"
  from_port                    = 22
  to_port                      = 22
  tags                         = { Name = "getddo-dev-api-from-eice" }
}

resource "aws_ec2_instance_connect_endpoint" "ssh" {
  subnet_id          = aws_subnet.public_a.id
  security_group_ids = [aws_security_group.ssh_endpoint.id]
  # 서버의 인바운드는 Endpoint 보안 그룹을 출발지로 검사한다.
  preserve_client_ip = false
  tags               = { Name = "getddo-dev-eice" }

  depends_on = [
    aws_vpc_security_group_egress_rule.ssh_endpoint_to_api,
    aws_vpc_security_group_ingress_rule.api_from_ssh_endpoint,
  ]
}

output "ssh_endpoint_id" {
  description = "EC2 Instance Connect Endpoint for the SSH HTTPS tunnel"
  value       = aws_ec2_instance_connect_endpoint.ssh.id
}

output "api_private_ip" {
  description = "Private destination of the SSH HTTPS tunnel"
  value       = aws_instance.api.private_ip
}
