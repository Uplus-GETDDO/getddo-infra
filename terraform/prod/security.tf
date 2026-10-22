resource "aws_security_group" "api" {
  name        = "getddo-dev-api"
  description = "SSH and API smoke testing from the operator IP"
  vpc_id      = aws_vpc.main.id
  tags        = { Name = "getddo-dev-api-sg" }
}

# 시연용 사용자 헤더를 사용하는 현재 앱을 인터넷 전체에 공개하지 않는다.
resource "aws_vpc_security_group_ingress_rule" "api_operator" {
  for_each          = { ssh = 22, api = 8080 }
  security_group_id = aws_security_group.api.id
  cidr_ipv4         = var.admin_ipv4_cidr
  from_port         = each.value
  to_port           = each.value
  ip_protocol       = "tcp"
  tags              = { Name = "getddo-dev-api-${each.key}" }
}

# 패키지 설치와 RDS 연결에 필요한 아웃바운드를 허용한다.
resource "aws_vpc_security_group_egress_rule" "api" {
  security_group_id = aws_security_group.api.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
  tags              = { Name = "getddo-dev-api-egress" }
}

resource "aws_security_group" "db" {
  name        = "getddo-dev-db"
  description = "MySQL from the GETDDO API security group only"
  vpc_id      = aws_vpc.main.id
  tags        = { Name = "getddo-dev-db-sg" }
}

# IP 전체 대신 API 보안 그룹을 출발지로 지정한다. 응답 트래픽은 자동 허용된다.
resource "aws_vpc_security_group_ingress_rule" "db_from_api" {
  security_group_id            = aws_security_group.db.id
  referenced_security_group_id = aws_security_group.api.id
  from_port                    = 3306
  to_port                      = 3306
  ip_protocol                  = "tcp"
  tags                         = { Name = "getddo-dev-db-from-api" }
}

# Redis 전용 그룹이다. 공인 IP·관리자 IP·ALB·CloudFront에는 Redis 포트를 열지 않는다.
resource "aws_security_group" "redis" {
  name        = "getddo-dev-redis"
  description = "Redis TLS from the GETDDO API security group only"
  vpc_id      = aws_vpc.main.id
  tags        = { Name = "getddo-dev-redis-sg" }
}

# 현재 API 한 대와 나중에 같은 API 그룹을 쓰는 두 번째 서버만 6379로 연결한다.
# 기존 API 아웃바운드가 이 연결도 허용한다. Redis 응답은 stateful 규칙으로 자동 허용된다.
resource "aws_vpc_security_group_ingress_rule" "redis_from_api" {
  security_group_id            = aws_security_group.redis.id
  referenced_security_group_id = aws_security_group.api.id
  from_port                    = 6379
  to_port                      = 6379
  ip_protocol                  = "tcp"
  tags                         = { Name = "getddo-dev-redis-from-api" }
}

# ALB 전용 그룹. 인라인 규칙 대신 개별 규칙 리소스로 의존 순서를 명확하게 관리한다.
# ALB 생성 시에는 인바운드가 없고, VPC origin 생성 후 서비스 보안 그룹만 허용한다.
resource "aws_security_group" "alb" {
  name        = "getddo-dev-alb"
  description = "Private ALB from CloudFront VPC origins only"
  vpc_id      = aws_vpc.main.id
  tags        = { Name = "getddo-dev-alb-sg" }
}

# 이 그룹은 VPC origin 생성 시 AWS가 만든다. 조회만 하며 직접 생성/변경하지 않는다.
# VPC ID도 함께 지정해 다른 VPC의 동명 그룹을 선택하지 않는다.
# 서비스 그룹 생성 전 조회 실패를 피하도록 VPC origin 완료 후 조회한다.
data "aws_security_group" "cloudfront_vpc_origin" {
  filter {
    name   = "vpc-id"
    values = [aws_vpc.main.id]
  }
  filter {
    name   = "group-name"
    values = ["CloudFront-VPCOrigins-Service-SG*"]
  }

  depends_on = [aws_cloudfront_vpc_origin.api]
}

# 모든 CloudFront IP를 허용하는 prefix list 대신 우리 VPC의 서비스 그룹을 참조한다.
# 0.0.0.0/0이나 공개 ALB를 통한 우회 경로를 만들지 않는다.
resource "aws_vpc_security_group_ingress_rule" "alb_from_cloudfront" {
  security_group_id            = aws_security_group.alb.id
  referenced_security_group_id = data.aws_security_group.cloudfront_vpc_origin.id
  from_port                    = 80
  to_port                      = 80
  ip_protocol                  = "tcp"
  tags                         = { Name = "getddo-dev-alb-from-cloudfront" }
}

# ALB의 외부 통신을 전부 열지 않고 API 보안 그룹의 앱 포트만 허용한다.
# HTTP 상태 확인 요청도 동일한 경로를 사용한다. 응답은 stateful 규칙으로 자동 허용된다.
resource "aws_vpc_security_group_egress_rule" "alb_to_api" {
  security_group_id            = aws_security_group.alb.id
  referenced_security_group_id = aws_security_group.api.id
  from_port                    = 8080
  to_port                      = 8080
  ip_protocol                  = "tcp"
  tags                         = { Name = "getddo-dev-alb-to-api" }
}

# 기존 EC2 보안 그룹에 ALB → 8080만 추가한다. SSH·RDS 규칙은 변경하지 않는다.
# 운영자 /32의 기존 직접 8080 접근도 이번 전환 준비 단계에서는 유지한다.
resource "aws_vpc_security_group_ingress_rule" "api_from_alb" {
  security_group_id            = aws_security_group.api.id
  referenced_security_group_id = aws_security_group.alb.id
  from_port                    = 8080
  to_port                      = 8080
  ip_protocol                  = "tcp"
  tags                         = { Name = "getddo-dev-api-from-alb" }
}
