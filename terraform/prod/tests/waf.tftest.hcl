mock_provider "aws" {
  alias = "edge"

  mock_resource "aws_wafv2_web_acl" {
    defaults = { arn = "arn:aws:wafv2:us-east-1:942466928325:global/webacl/getddo-dev-api-waf/12345678-1234-1234-1234-123456789abc" }
  }
  mock_resource "aws_cloudwatch_log_group" {
    defaults = { arn = "arn:aws:logs:us-east-1:942466928325:log-group:aws-waf-logs-getddo-dev-api" }
  }
}

# AWS에 자원을 만들지 않고 외부 노출 방지·비밀번호 처리 계약을 확인한다.
mock_provider "aws" {
  mock_data "aws_caller_identity" {
    defaults = { account_id = "942466928325" }
  }

  # Dashboard rendering needs the actual single-node shape, not an empty mock set.
  mock_resource "aws_elasticache_replication_group" {
    defaults = { member_clusters = ["getddo-dev-redis-001"] }
  }

  # 새 ALB·CloudFront 참조 값도 형식이 유효한 테스트 전용 값으로 제공한다.
  mock_resource "aws_lb" {
    defaults = {
      arn      = "arn:aws:elasticloadbalancing:ap-northeast-2:942466928325:loadbalancer/app/getddo-dev-api-alb/0123456789abcdef"
      dns_name = "internal-test.ap-northeast-2.elb.amazonaws.com"
    }
  }
  mock_resource "aws_lb_target_group" {
    defaults = { arn = "arn:aws:elasticloadbalancing:ap-northeast-2:942466928325:targetgroup/getddo-dev-api-tg/0123456789abcdef" }
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
  target          = aws_wafv2_web_acl.api
  values = {
    arn = "arn:aws:wafv2:us-east-1:942466928325:global/webacl/getddo-dev-api-waf/12345678-1234-1234-1234-123456789abc"
  }
}

override_resource {
  override_during = plan
  target          = aws_cloudwatch_log_group.waf
  values          = { arn = "arn:aws:logs:us-east-1:942466928325:log-group:aws-waf-logs-getddo-dev-api" }
}

override_resource {
  override_during = plan
  target          = aws_lb_listener.api
  values          = { arn = "arn:aws:elasticloadbalancing:ap-northeast-2:942466928325:listener/app/getddo-dev-api-alb/9e65e06e10492070/1234567890abcdef" }
}

run "default_block_and_logging" {
  command = plan

  assert {
    condition = (
      aws_wafv2_web_acl.api.name == "getddo-dev-api-waf" &&
      aws_wafv2_web_acl.api.scope == "CLOUDFRONT" &&
      aws_cloudfront_distribution.api.web_acl_id == aws_wafv2_web_acl.api.arn &&
      length(aws_wafv2_web_acl.api.rule) == 1 &&
      one(aws_wafv2_web_acl.api.rule).name == "ApiIpRateLimit" &&
      one(aws_wafv2_web_acl.api.rule).priority == 10 &&
      one(aws_wafv2_web_acl.api.rule).statement[0].rate_based_statement[0].limit == 600 &&
      one(aws_wafv2_web_acl.api.rule).statement[0].rate_based_statement[0].evaluation_window_sec == 60 &&
      one(aws_wafv2_web_acl.api.rule).statement[0].rate_based_statement[0].aggregate_key_type == "IP" &&
      length(one(aws_wafv2_web_acl.api.rule).statement[0].rate_based_statement[0].forwarded_ip_config) == 0
    )
    error_message = "CloudFront must use one IP-aggregated API rule with a fixed 600 requests per 60 seconds."
  }

  assert {
    condition = (
      length(one(aws_wafv2_web_acl.api.rule).action[0].count) == 0 &&
      one(aws_wafv2_web_acl.api.rule).action[0].block[0].custom_response[0].response_code == 429 &&
      one(aws_wafv2_web_acl.api.rule).action[0].block[0].custom_response[0].custom_response_body_key == "ApiRateLimitError" &&
      one(one(aws_wafv2_web_acl.api.rule).action[0].block[0].custom_response[0].response_header).name == "Access-Control-Allow-Origin" &&
      one(one(aws_wafv2_web_acl.api.rule).action[0].block[0].custom_response[0].response_header).value == "*" &&
      length(one(aws_wafv2_web_acl.api.rule).action[0].block[0].custom_response[0].response_header) == 1 &&
      one(aws_wafv2_web_acl.api.custom_response_body).content_type == "APPLICATION_JSON" &&
      jsondecode(one(aws_wafv2_web_acl.api.custom_response_body).content) == {
        success = false, code = "COMMON-006", message = "요청이 너무 많습니다. 잠시 후 다시 시도해 주세요.", data = null
      }
    )
    error_message = "Rate-limit blocks must return the fixed COMMON-006 JSON and only the public-error CORS header."
  }

  assert {
    condition = (
      aws_cloudwatch_log_group.waf.name == "aws-waf-logs-getddo-dev-api" &&
      aws_cloudwatch_log_group.waf.retention_in_days == 7 &&
      aws_wafv2_web_acl_logging_configuration.api.resource_arn == aws_wafv2_web_acl.api.arn &&
      aws_wafv2_web_acl_logging_configuration.api.log_destination_configs == toset([aws_cloudwatch_log_group.waf.arn]) &&
      one(aws_wafv2_web_acl_logging_configuration.api.logging_filter).default_behavior == "DROP" &&
      one(one(aws_wafv2_web_acl_logging_configuration.api.logging_filter).filter).behavior == "KEEP" &&
      one(one(aws_wafv2_web_acl_logging_configuration.api.logging_filter).filter).requirement == "MEETS_ALL" &&
      one(one(one(aws_wafv2_web_acl_logging_configuration.api.logging_filter).filter).condition).action_condition[0].action == "BLOCK" &&
      !aws_wafv2_web_acl.api.visibility_config[0].sampled_requests_enabled &&
      !one(aws_wafv2_web_acl.api.rule).visibility_config[0].sampled_requests_enabled &&
      aws_wafv2_web_acl.api.visibility_config[0].cloudwatch_metrics_enabled &&
      one(aws_wafv2_web_acl.api.rule).visibility_config[0].cloudwatch_metrics_enabled &&
      length(aws_lb_listener_rule.waf_probe) == 0 && output.waf_probe_path == null
    )
    error_message = "Keep blocked-only logs for seven days, disable samples and omit the temporary probe by default."
  }

  assert {
    condition = (
      toset(flatten([for field in aws_wafv2_web_acl_logging_configuration.api.redacted_fields : [for header in field.single_header : header.name]])) ==
      toset(["authorization", "proxy-authorization", "cookie", "x-api-key", "x-user-id", "x-user-role", "x-user-membership"]) &&
      length([for field in aws_wafv2_web_acl_logging_configuration.api.redacted_fields : field if length(field.query_string) == 1]) == 1
    )
    error_message = "Redact authentication, user identity headers and query strings from request logs."
  }
}

run "api_scope_excludes_options" {
  command = plan
  assert {
    condition = (
      length(one(aws_wafv2_web_acl.api.rule).statement[0].rate_based_statement[0].scope_down_statement[0].and_statement[0].statement) == 2 &&
      length([for s in one(aws_wafv2_web_acl.api.rule).statement[0].rate_based_statement[0].scope_down_statement[0].and_statement[0].statement : s if try(
        s.byte_match_statement[0].search_string == "/api/" && s.byte_match_statement[0].positional_constraint == "STARTS_WITH" &&
        length(s.byte_match_statement[0].field_to_match[0].uri_path) == 1 &&
      one(s.byte_match_statement[0].text_transformation).type == "NONE", false)]) == 1 &&
      length([for s in one(aws_wafv2_web_acl.api.rule).statement[0].rate_based_statement[0].scope_down_statement[0].and_statement[0].statement : s if try(
        s.not_statement[0].statement[0].byte_match_statement[0].search_string == "OPTIONS" &&
        s.not_statement[0].statement[0].byte_match_statement[0].positional_constraint == "EXACTLY" &&
        length(s.not_statement[0].statement[0].byte_match_statement[0].field_to_match[0].method) == 1 &&
      one(s.not_statement[0].statement[0].byte_match_statement[0].text_transformation).type == "NONE", false)]) == 1
    )
    error_message = "Aggregate only case-preserved /api/ paths with methods other than OPTIONS."
  }
}

run "temporary_count_all_and_probe" {
  command = plan
  variables {
    waf_mode     = "count"
    waf_log_mode = "all"
    waf_probe = {
      run_id   = "12345678-1234-1234-1234-123456789abc"
      priority = 10
    }
  }
  assert {
    condition = (
      length(one(aws_wafv2_web_acl.api.rule).action[0].count) == 1 &&
      length(one(aws_wafv2_web_acl.api.rule).action[0].block) == 0 &&
      length(aws_wafv2_web_acl_logging_configuration.api.logging_filter) == 0 &&
      aws_lb_listener_rule.waf_probe[0].priority == 10 &&
      aws_lb_listener_rule.waf_probe[0].listener_arn == aws_lb_listener.api.arn &&
      aws_lb_listener_rule.waf_probe[0].action[0].fixed_response[0].message_body == "waf-probe:12345678-1234-1234-1234-123456789abc" &&
      aws_lb_listener_rule.waf_probe[0].action[0].fixed_response[0].status_code == "200" &&
      aws_lb_listener_rule.waf_probe[0].tags.TestRun == var.waf_probe.run_id &&
      output.waf_probe_path == "/api/__waf_probe/12345678-1234-1234-1234-123456789abc" &&
      length([for c in aws_lb_listener_rule.waf_probe[0].condition : c if try(c.path_pattern[0].values == toset([output.waf_probe_path]), false)]) == 1 &&
      length([for c in aws_lb_listener_rule.waf_probe[0].condition : c if try(c.http_request_method[0].values == toset(["GET"]), false)]) == 1
    )
    error_message = "Count/all must retain the same limit and offer an exact GET-only fixed response that bypasses the app."
  }
}

run "reject_count_without_logs" {
  command = plan
  variables { waf_mode = "count" }
  expect_failures = [var.waf_log_mode]
}
run "reject_unknown_mode" {
  command = plan
  variables { waf_mode = "allow" }
  expect_failures = [var.waf_mode]
}
run "reject_unknown_log_mode" {
  command = plan
  variables { waf_log_mode = "none" }
  expect_failures = [var.waf_log_mode]
}
run "reject_probe_path_injection" {
  command = plan
  variables { waf_probe = { run_id = "../../api", priority = 10 } }
  expect_failures = [var.waf_probe]
}
run "reject_probe_priority_outside_range" {
  command = plan
  variables { waf_probe = { run_id = "12345678-1234-1234-1234-123456789abc", priority = 20 } }
  expect_failures = [var.waf_probe]
}
run "reject_probe_fractional_priority" {
  command = plan
  variables { waf_probe = { run_id = "12345678-1234-1234-1234-123456789abc", priority = 10.5 } }
  expect_failures = [var.waf_probe]
}
