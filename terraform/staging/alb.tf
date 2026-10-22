resource "aws_lb" "stage" {
  name               = "getddo-staging-api"
  internal           = true
  load_balancer_type = "application"
  subnets            = [for s in data.aws_subnet.alb : s.id]
  security_groups    = [aws_security_group.stage["alb"].id]
  tags               = { Name = "getddo-staging-api" }
}
resource "aws_lb_target_group" "stage" {
  name     = "getddo-staging-api"
  port     = 8080
  protocol = "HTTP"
  vpc_id   = data.aws_vpc.existing.id
  health_check {
    path    = "/v3/api-docs"
    port    = "traffic-port"
    matcher = "200"
  }
  tags = { Name = "getddo-staging-api" }
}
resource "aws_lb_listener" "stage" {
  load_balancer_arn = aws_lb.stage.arn
  port              = 80
  protocol          = "HTTP"
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.stage.arn
  }
  tags = { Name = "getddo-staging-api" }
}
resource "aws_lb_target_group_attachment" "api" {
  for_each         = aws_instance.api
  target_group_arn = aws_lb_target_group.stage.arn
  target_id        = each.value.id
  port             = 8080
}
