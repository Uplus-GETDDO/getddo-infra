resource "aws_security_group" "stage" {
  for_each    = toset(["api", "locust", "alb", "db", "redis"])
  name        = "getddo-staging-${each.key}"
  description = "Dedicated staging ${each.key}"
  vpc_id      = data.aws_vpc.existing.id
  tags        = { Name = "getddo-staging-${each.key}" }
}
locals {
  ingress = {
    api   = { to = "api", from = "alb", port = 8080 }
    alb   = { to = "alb", from = "locust", port = 80 }
    db    = { to = "db", from = "api", port = 3306 }
    redis = { to = "redis", from = "api", port = 6379 }
  }
}
resource "aws_vpc_security_group_ingress_rule" "stage" {
  for_each                     = local.ingress
  security_group_id            = aws_security_group.stage[each.value.to].id
  referenced_security_group_id = aws_security_group.stage[each.value.from].id
  ip_protocol                  = "tcp"
  from_port                    = each.value.port
  to_port                      = each.value.port
  tags                         = { Name = "getddo-staging-${each.key}" }
}
resource "aws_vpc_security_group_egress_rule" "hosts" {
  for_each          = toset(["api", "locust"])
  security_group_id = aws_security_group.stage[each.key].id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
  tags              = { Name = "getddo-staging-${each.key}-out" }
}
resource "aws_vpc_security_group_egress_rule" "alb" {
  security_group_id            = aws_security_group.stage["alb"].id
  referenced_security_group_id = aws_security_group.stage["api"].id
  ip_protocol                  = "tcp"
  from_port                    = 8080
  to_port                      = 8080
  tags                         = { Name = "getddo-staging-alb-out" }
}
resource "aws_vpc_security_group_ingress_rule" "node" {
  for_each                     = toset(["api", "locust"])
  security_group_id            = aws_security_group.stage[each.key].id
  referenced_security_group_id = var.monitoring_security_group_id
  ip_protocol                  = "tcp"
  from_port                    = 9100
  to_port                      = 9100
  tags                         = { Name = "getddo-staging-${each.key}-metrics" }
}
resource "aws_vpc_security_group_ingress_rule" "app_metrics" {
  security_group_id            = aws_security_group.stage["api"].id
  referenced_security_group_id = var.monitoring_security_group_id
  ip_protocol                  = "tcp"
  from_port                    = 8080
  to_port                      = 8080
  tags                         = { Name = "getddo-staging-api-metrics" }
}
resource "aws_vpc_security_group_ingress_rule" "locust_metrics" {
  security_group_id            = aws_security_group.stage["locust"].id
  referenced_security_group_id = var.monitoring_security_group_id
  ip_protocol                  = "tcp"
  from_port                    = 9646
  to_port                      = 9646
  tags                         = { Name = "getddo-staging-locust-metrics" }
}
resource "aws_vpc_security_group_ingress_rule" "locust_ui" {
  security_group_id            = aws_security_group.stage["locust"].id
  referenced_security_group_id = var.shared_alb_security_group_id
  ip_protocol                  = "tcp"
  from_port                    = 8089
  to_port                      = 8089
  tags                         = { Name = "getddo-staging-locust-ui" }
}
