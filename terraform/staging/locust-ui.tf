# Only this temporary path rule and target group belong to the workload State.
data "aws_lb" "shared" { name = "getddo-dev-api-alb" }
data "aws_lb_listener" "shared" {
  load_balancer_arn = data.aws_lb.shared.arn
  port              = 80
}
resource "aws_lb_target_group" "locust" {
  name                 = "getddo-staging-locust"
  vpc_id               = data.aws_vpc.existing.id
  target_type          = "instance"
  protocol             = "HTTP"
  port                 = 8089
  deregistration_delay = 10
  health_check {
    path    = "/locust/login"
    matcher = "200-399"
  }
  tags = { Name = "getddo-staging-locust" }
  lifecycle {
    precondition {
      condition     = data.aws_lb.shared.internal && data.aws_lb.shared.vpc_id == data.aws_vpc.existing.id && contains(data.aws_lb.shared.security_groups, var.shared_alb_security_group_id)
      error_message = "The existing private ALB and security group must match the staging VPC."
    }
  }
}
resource "aws_lb_target_group_attachment" "locust" {
  target_group_arn = aws_lb_target_group.locust.arn
  target_id        = aws_instance.locust.id
  port             = 8089
}
resource "aws_lb_listener_rule" "locust" {
  listener_arn = data.aws_lb_listener.shared.arn
  priority     = 30
  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.locust.arn
  }
  condition {
    path_pattern { values = ["/locust", "/locust/*"] }
  }
  tags = { Name = "getddo-staging-locust" }
}
resource "aws_vpc_security_group_egress_rule" "shared_alb_to_locust" {
  security_group_id            = var.shared_alb_security_group_id
  referenced_security_group_id = aws_security_group.stage["locust"].id
  ip_protocol                  = "tcp"
  from_port                    = 8089
  to_port                      = 8089
  tags                         = { Name = "getddo-staging-locust-ui-out" }
}
