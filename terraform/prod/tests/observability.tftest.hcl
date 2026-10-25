mock_provider "aws" {
  alias = "edge"

  mock_resource "aws_wafv2_web_acl" {
    defaults = { arn = "arn:aws:wafv2:us-east-1:942466928325:global/webacl/getddo-dev-api-waf/12345678-1234-1234-1234-123456789abc" }
  }
  mock_resource "aws_cloudwatch_log_group" {
    defaults = { arn = "arn:aws:logs:us-east-1:942466928325:log-group:aws-waf-logs-getddo-dev-api" }
  }
}

# 모의 공급자로 plan만 실행한다. AWS 호출·자원 생성은 없다.
mock_provider "aws" {
  mock_data "aws_caller_identity" {
    defaults = { account_id = "942466928325" }
  }
}

variables {
  admin_ipv4_cidr    = "203.0.113.10/32"
  ami_id             = "ami-0123456789abcdef0"
  ssh_public_key     = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIB9vBqHizcQYJVtjb3VcYO3qs95kKhQuwpDVHRptYI0G test-only"
  db_master_password = "TestOnlyPasswordNotUsedInAWS123456"
  redis_auth_token   = "TestOnlyRedisPasswordNotUsed123456"
}

override_resource {
  override_during = plan
  target          = aws_instance.api
  values          = { id = "i-0c67aa33f1d5c5fd9" }
}
override_resource {
  override_during = plan
  target          = aws_instance.api_c
  values          = { id = "REPLACE_WITH_API_C_INSTANCE_ID" }
}
override_resource {
  override_during = plan
  target          = aws_lb.api
  values = {
    arn_suffix = "app/getddo-dev-api-alb/9e65e06e10492070"
    arn        = "arn:aws:elasticloadbalancing:ap-northeast-2:942466928325:loadbalancer/app/getddo-dev-api-alb/9e65e06e10492070"
    dns_name   = "internal-test.ap-northeast-2.elb.amazonaws.com"
  }
}
override_resource {
  override_during = plan
  target          = aws_lb_target_group.api
  values = {
    arn_suffix = "targetgroup/getddo-dev-api-tg/5821861b8038bff0"
    arn        = "arn:aws:elasticloadbalancing:ap-northeast-2:942466928325:targetgroup/getddo-dev-api-tg/5821861b8038bff0"
  }
}
override_resource {
  override_during = plan
  target          = aws_elasticache_replication_group.redis
  values          = { member_clusters = ["getddo-dev-redis-001", "getddo-dev-redis-002"] }
}

run "console_dashboard_matches_terraform" {
  command = plan

  assert {
    condition     = jsondecode(aws_cloudwatch_dashboard.dev.dashboard_body) == jsondecode(file("${path.module}/templates/dashboard.dev.json"))
    error_message = "콘솔용 JSON과 Terraform 대시보드가 동일한 지표·차원을 사용해야 합니다."
  }
  assert {
    condition = alltrue([
      for widget in jsondecode(aws_cloudwatch_dashboard.dev.dashboard_body).widgets :
      length(widget.properties.metrics) == 2
      if startswith(try(widget.properties.title, ""), "EC2")
    ])
    error_message = "Each host widget must compare A and C, including Agent memory and root disk."
  }
  assert {
    condition     = aws_cloudwatch_log_group.api.retention_in_days == 14 && aws_cloudwatch_log_group.api.name == "/getddo/dev/api"
    error_message = "앱 로그 경로와 14일 보관 설정이 콘솔 안내와 일치해야 합니다."
  }
  assert {
    condition = (toset(jsondecode(aws_iam_role_policy.api_observability.policy).Statement[0].Condition.StringEquals["cloudwatch:namespace"]) == toset(["CWAgent", "GETDDO/API"]) &&
      jsondecode(aws_iam_role_policy.api_observability.policy).Statement[0].Condition.StringEquals["aws:RequestedRegion"] == "ap-northeast-2" &&
    toset(jsondecode(aws_iam_role_policy.api_observability.policy).Statement[0].Action) == toset(["cloudwatch:PutMetricData"]))
    error_message = "메트릭 쓰기는 서울 리전의 CWAgent와 GETDDO/API에만 허용해야 합니다."
  }
}

run "app_dashboard_matches_exported_meter_names" {
  command = plan
  assert {
    condition = alltrue(flatten([
      for name in [
        "hikaricp.connections.active.value", "hikaricp.connections.idle.value",
        "hikaricp.connections.max.value", "hikaricp.connections.pending.value",
        "hikaricp.connections.acquire.max", "hikaricp.connections.timeout.count",
        "tomcat.threads.busy.value", "tomcat.threads.current.value", "tomcat.threads.config.max.value",
        "jvm.memory.used.value", "jvm.memory.max.value", "jvm.gc.pause.max"
        ] : [for server in ["a", "c"] : anytrue(flatten([
          for widget in jsondecode(aws_cloudwatch_dashboard.dev.dashboard_body).widgets : [
            for metric in try(widget.properties.metrics, []) :
            try(strcontains(metric[0].expression, "Namespace=\"GETDDO/API\"") &&
              strcontains(metric[0].expression, "MetricName=\"${name}\"") &&
              strcontains(metric[0].expression, "environment=\"dev\"") &&
            strcontains(metric[0].expression, "server=\"${server}\""), false)
          ]
      ]))]
    ]))
    error_message = "Exported Hikari, Tomcat and JVM meters must be queried separately for dev A/C."
  }
}

run "redis_metrics_include_both_nodes" {
  command = plan
  assert {
    condition = alltrue(flatten([
      for name in ["EngineCPUUtilization", "CPUUtilization", "DatabaseMemoryUsagePercentage", "Evictions", "CurrConnections", "ReplicationLag"] : [
        for id in ["getddo-dev-redis-001", "getddo-dev-redis-002"] : anytrue(flatten([
          for w in jsondecode(aws_cloudwatch_dashboard.dev.dashboard_body).widgets : [
            for m in try(w.properties.metrics, []) : try(m[0] == "AWS/ElastiCache" && m[1] == name && m[2] == "CacheClusterId" && m[3] == id && m[4] == "CacheNodeId" && m[5] == "0001", false)
          ]
        ]))
      ]
    ]))
    error_message = "각 Redis 지표는 두 노드의 명시적 차원을 포함해야 합니다."
  }
}

run "redis_member_order_does_not_change_dashboard" {
  command = plan
  override_resource {
    override_during = plan
    target          = aws_elasticache_replication_group.redis
    values          = { member_clusters = ["getddo-dev-redis-002", "getddo-dev-redis-001"] }
  }
  assert {
    condition     = jsondecode(aws_cloudwatch_dashboard.dev.dashboard_body) == jsondecode(file("${path.module}/templates/dashboard.dev.json"))
    error_message = "멤버 순서가 바뀌어도 대시보드 출력은 동일해야 합니다."
  }
}
