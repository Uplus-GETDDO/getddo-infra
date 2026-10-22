# 요청 흐름: CloudFront → VPC origin → 내부 ALB:80 → 기존 EC2:8080.
# internal=true이므로 ALB에는 공인 IP가 없고 인터넷에서 직접 접근할 수 없다.
# 두 AZ의 ALB 노드를 사용해도 앱 대상은 아직 기존 EC2 한 대다.
resource "aws_lb" "api" {
  name               = "getddo-dev-api-alb"
  internal           = true
  load_balancer_type = "application"
  ip_address_type    = "ipv4"
  subnets            = [for subnet in aws_subnet.alb : subnet.id]
  security_groups    = [aws_security_group.alb.id]

  # 현재 API의 일반 응답 대기 시간. 장시간 작업을 위한 타임아웃 확장은 별도 검토한다.
  idle_timeout = 60
  tags         = { Name = "getddo-dev-api-alb" }

  # ALB를 먼저 만들고 VPC origin을 만들기 때문에 CloudFront 인바운드 규칙은
  # 여기서 의존하지 않는다. 의존하면 ALB ↔ VPC origin 사이 순환이 생긴다.
  depends_on = [aws_route_table_association.alb]
}

# ALB가 요청을 전달할 대상 목록이다. 새 서버를 만들지 않고 기존 EC2를 등록한다.
resource "aws_lb_target_group" "api" {
  name        = "getddo-dev-api-tg"
  vpc_id      = aws_vpc.main.id
  target_type = "instance"
  protocol    = "HTTP"
  port        = 8080

  # 등록 해제 후 처리 중인 요청을 기다리는 시간이다.
  # 현재 CD가 자동으로 대상을 등록 해제하는 것은 아니며, 두 대 순차 배포는 후속 작업이다.
  deregistration_delay = 30

  health_check {
    enabled             = true
    protocol            = "HTTP"
    port                = "traffic-port"
    path                = "/v3/api-docs"
    matcher             = "200"
    interval            = 30
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 2
  }

  # 위 경로는 기존 CD와 같은 임시 기동 검사다. DB·업무 기능의 정상 여부를 보장하지 않는다.
  # 모든 대상이 비정상이면 ALB의 fail-open 동작이 있으므로 상태 검사만을 접근 차단으로 쓰지 않는다.
  tags = { Name = "getddo-dev-api-tg" }
}

resource "aws_lb_target_group_attachment" "api" {
  target_group_arn = aws_lb_target_group.api.arn
  target_id        = aws_instance.api.id
  port             = 8080

  # 대상 등록 전에 ALB → 앱 포트 접근을 허용한다.
  depends_on = [aws_vpc_security_group_ingress_rule.api_from_alb]
}

# 외부 HTTPS는 CloudFront에서 끝낸다. 내부 ALB에는 HTTP 리스너만 둔다.
# 이 구성은 모든 구간의 TLS 암호화를 제공하지 않는다.
resource "aws_lb_listener" "api" {
  load_balancer_arn = aws_lb.api.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }

  tags = { Name = "getddo-dev-api-listener" }
}

# Provisioning C alone must never put an unverified application into service.
resource "aws_lb_target_group_attachment" "api_c" {
  count = var.api_c_register_in_target_group ? 1 : 0

  target_group_arn = aws_lb_target_group.api.arn
  target_id        = aws_instance.api_c.id
  port             = 8080

  depends_on = [aws_vpc_security_group_ingress_rule.api_from_alb]
}
