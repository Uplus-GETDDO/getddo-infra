# CloudWatch resources, existing IDs and retention unchanged.
resource "aws_cloudwatch_log_group" "api" {
  name              = "/getddo/dev/api"
  retention_in_days = 14
  tags              = { Name = "getddo-dev-api-logs" }

  # 로그 삭제는 보관 정책과 별도로 명시적인 검토 후 진행한다.
  lifecycle {
    prevent_destroy = true
  }
}

# 기존 콘솔 설정을 관리한다. 관련 자원은 2026-10-21 State 등록을 완료했다.
resource "aws_cloudwatch_dashboard" "dev" {
  dashboard_name = "getddo-dev-overview"
  dashboard_body = templatefile("${path.module}/templates/cloudwatch-dashboard.json.tftpl", {
    region              = var.aws_region
    instance_id         = aws_instance.api.id
    instance_c_id       = aws_instance.api_c.id
    alb_suffix          = aws_lb.api.arn_suffix
    target_group_suffix = aws_lb_target_group.api.arn_suffix
    db_identifier       = aws_db_instance.main.identifier
    # ID 순서는 역할을 뜻하지 않으며, 멤버 집합의 순서 변화가 대시보드를 바꾸지 않게 한다.
    redis_cluster_ids = sort(tolist(aws_elasticache_replication_group.redis.member_clusters))
  })
}

output "cloudwatch_dashboard_name" {
  value = aws_cloudwatch_dashboard.dev.dashboard_name
}

output "cloudwatch_app_log_group" {
  value = aws_cloudwatch_log_group.api.name
}
