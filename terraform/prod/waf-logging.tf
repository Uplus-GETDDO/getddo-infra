resource "aws_cloudwatch_log_group" "waf" {
  provider          = aws.edge
  name              = "aws-waf-logs-getddo-dev-api"
  retention_in_days = 7

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_wafv2_web_acl_logging_configuration" "api" {
  provider                = aws.edge
  resource_arn            = aws_wafv2_web_acl.api.arn
  log_destination_configs = [aws_cloudwatch_log_group.waf.arn]

  redacted_fields {
    query_string {}
  }

  dynamic "redacted_fields" {
    for_each = toset([
      "authorization", "proxy-authorization", "cookie", "x-api-key",
      "x-user-id", "x-user-role", "x-user-membership",
    ])
    content {
      single_header {
        name = redacted_fields.value
      }
    }
  }

  dynamic "logging_filter" {
    for_each = var.waf_log_mode == "blocked" ? [1] : []
    content {
      default_behavior = "DROP"
      filter {
        behavior    = "KEEP"
        requirement = "MEETS_ALL"
        condition {
          action_condition {
            action = "BLOCK"
          }
        }
      }
    }
  }
}
