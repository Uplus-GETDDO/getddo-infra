# API 서버들이 공유할 요청 빈도 제한 저장소다. 업무 원본 데이터는 기존 RDS에 둔다.
# 이 파일을 작성하는 것만으로 생성되지 않으며, 실제 생성은 전체 plan 검토 후 apply한다.
resource "aws_elasticache_subnet_group" "redis" {
  name        = "getddo-dev-redis"
  description = "Private data subnets for the shared Redis rate limiter"

  # 기존 RDS용 data-a/data-c를 재사용한다. 서브넷 이동·신규 NAT·인터넷 경로는 없다.
  subnet_ids = [for subnet in aws_subnet.data : subnet.id]
  tags       = { Name = "getddo-dev-redis-subnets" }
}

resource "aws_elasticache_replication_group" "redis" {
  replication_group_id = "getddo-dev-redis"
  description          = "Shared Redis OSS for GETDDO request rate limiting"

  # 사용자가 선택한 Redis OSS다. Valkey나 Serverless가 아닌 노드 기반 구성이다.
  # 서울에서 7.1/redis7 지원을 확인했다. 버전·사양 변경 전 호환성과 비용을 확인한다.
  engine               = "redis"
  engine_version       = "7.1"
  parameter_group_name = "default.redis7"
  node_type            = "cache.t4g.micro"
  port                 = 6379

  # 원본 1대와 복제본 1대가 기존 메인 주소를 공유하며 장애 시 복제본을 승격한다.
  # cluster mode는 끄고, 원본이 쓰기와 읽기를 모두 처리한다.
  cluster_mode               = "disabled"
  num_cache_clusters         = 2
  automatic_failover_enabled = true
  multi_az_enabled           = true
  preferred_cache_cluster_azs = [
    aws_subnet.data["a"].availability_zone,
    aws_subnet.data["c"].availability_zone,
  ]

  subnet_group_name  = aws_elasticache_subnet_group.redis.name
  security_group_ids = [aws_security_group.redis.id]

  # VPC 내부에서도 TLS와 인증을 요구한다. 앱 클라이언트도 TLS·서버 인증서 검증을 켠다.
  # 암호화 키는 AWS 기본 키를 사용하며 별도 고객 관리 KMS 키를 생성하지 않는다.
  at_rest_encryption_enabled = true
  transit_encryption_enabled = true
  transit_encryption_mode    = "required"

  # ephemeral 입력 + write-only 속성으로 비밀번호 원문을 plan·State에 남기지 않는다.
  # 앱에 자동 주입되지는 않는다. 생성 때 쓴 동일 비밀번호를 Parameter Store에 별도 등록한다.
  # 회전은 앱 전환 절차를 검토한 뒤 비밀번호·version과 apply_immediately=true를 함께 준비한다.
  auth_token_wo              = var.redis_auth_token
  auth_token_wo_version      = var.redis_auth_token_version
  auth_token_update_strategy = "ROTATE"

  # 비동기 복제이므로 최근 카운터·차단 상태·DB 미반영 저널은 유실될 수 있다.
  # 자동 스냅샷은 보관하지 않으며, 이미 RDS에 저장된 이력은 별도로 유지한다.
  snapshot_retention_limit   = 0
  auto_minor_version_upgrade = true
  apply_immediately          = var.redis_apply_immediately

  # 실수로 삭제·교체하는 계획을 막는다. 정리나 교체는 보호 해제와 별도 검토가 필요하다.
  lifecycle {
    prevent_destroy = true
  }

  tags = { Name = "getddo-dev-redis" }

  depends_on = [
    aws_route_table_association.data,
    aws_vpc_security_group_ingress_rule.redis_from_api,
  ]
}
