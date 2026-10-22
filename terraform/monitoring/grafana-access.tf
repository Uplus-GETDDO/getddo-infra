# Reuse the existing private ALB; this root owns only Grafana's new routing resources.
data "aws_lb" "api" {
  name = "getddo-dev-api-alb"
}
data "aws_lb_listener" "api" {
  load_balancer_arn = data.aws_lb.api.arn
  port              = 80
}
data "aws_security_group" "alb" {
  vpc_id = data.aws_vpc.existing.id
  name   = "getddo-dev-alb"
}
data "aws_cloudfront_distribution" "api" {
  id = var.cloudfront_distribution_id
}

resource "aws_lb_target_group" "grafana" {
  # The existing operator policy scopes ALB resources to the legacy getddo-dev prefix.
  name                 = "getddo-dev-grafana-tg"
  vpc_id               = data.aws_vpc.existing.id
  target_type          = "instance"
  protocol             = "HTTP"
  port                 = 3000
  deregistration_delay = 30
  health_check {
    path                = "/grafana/api/health"
    matcher             = "200"
    interval            = 30
    healthy_threshold   = 2
    unhealthy_threshold = 2
  }
  lifecycle {
    precondition {
      condition     = data.aws_lb.api.internal && data.aws_lb.api.vpc_id == data.aws_vpc.existing.id
      error_message = "Grafana must use the existing private ALB in the monitoring VPC."
    }
  }
}
resource "aws_lb_target_group_attachment" "grafana" {
  target_group_arn = aws_lb_target_group.grafana.arn
  target_id        = aws_instance.monitoring.id
  port             = 3000
  depends_on       = [aws_vpc_security_group_ingress_rule.grafana_from_alb, aws_vpc_security_group_egress_rule.alb_to_grafana]
}
resource "aws_lb_listener_rule" "grafana" {
  listener_arn = data.aws_lb_listener.api.arn
  priority     = 20
  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.grafana.arn
  }
  condition {
    path_pattern { values = ["/grafana", "/grafana/*"] }
  }
  depends_on = [aws_lb_target_group_attachment.grafana]
}
resource "aws_vpc_security_group_ingress_rule" "grafana_from_alb" {
  security_group_id            = aws_security_group.monitoring.id
  referenced_security_group_id = data.aws_security_group.alb.id
  ip_protocol                  = "tcp"
  from_port                    = 3000
  to_port                      = 3000
}
resource "aws_vpc_security_group_egress_rule" "alb_to_grafana" {
  security_group_id            = data.aws_security_group.alb.id
  referenced_security_group_id = aws_security_group.monitoring.id
  ip_protocol                  = "tcp"
  from_port                    = 3000
  to_port                      = 3000
}
output "grafana_url" {
  value = "https://${data.aws_cloudfront_distribution.api.domain_name}/grafana/"
}
output "grafana_runtime_config" {
  description = "Non-secret Compose .env values; passwords remain in the dedicated SecureString."
  value = {
    GRAFANA_BIND_IP  = aws_instance.monitoring.private_ip
    GRAFANA_ROOT_URL = "https://${data.aws_cloudfront_distribution.api.domain_name}/grafana/"
  }
}
