resource "aws_security_group" "monitoring" {
  name        = "getddo-prod-monitoring"
  description = "No inbound management ports; use SSM forwarding to loopback Grafana"
  vpc_id      = data.aws_vpc.existing.id
  tags        = { Name = "getddo-prod-monitoring" }
}
# Existing API SG uses standalone rules in prod/security.tf, with no inline ingress.
# This root owns only these two new rules, including the not-yet-created source SG.
resource "aws_vpc_security_group_ingress_rule" "api_metrics" {
  for_each                     = { app = 9091, node = 9100 }
  security_group_id            = data.aws_security_group.api.id
  referenced_security_group_id = aws_security_group.monitoring.id
  ip_protocol                  = "tcp"
  from_port                    = each.value
  to_port                      = each.value
}
resource "aws_vpc_security_group_egress_rule" "api_metrics" {
  for_each                     = { app = 9091, node = 9100 }
  security_group_id            = aws_security_group.monitoring.id
  referenced_security_group_id = data.aws_security_group.api.id
  ip_protocol                  = "tcp"
  from_port                    = each.value
  to_port                      = each.value
}
resource "aws_vpc_security_group_egress_rule" "https" {
  security_group_id = aws_security_group.monitoring.id
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = "0.0.0.0/0"
}
# Scrape the monitor's private address through the local Docker bridge.
resource "aws_vpc_security_group_ingress_rule" "self_node" {
  security_group_id            = aws_security_group.monitoring.id
  referenced_security_group_id = aws_security_group.monitoring.id
  ip_protocol                  = "tcp"
  from_port                    = 9100
  to_port                      = 9100
}
resource "aws_vpc_security_group_egress_rule" "self_node" {
  security_group_id            = aws_security_group.monitoring.id
  referenced_security_group_id = aws_security_group.monitoring.id
  ip_protocol                  = "tcp"
  from_port                    = 9100
  to_port                      = 9100
}
