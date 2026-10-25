resource "aws_wafv2_web_acl" "api" {
  provider = aws.edge
  name     = "getddo-dev-api-waf"
  scope    = "CLOUDFRONT"

  default_action {
    allow {}
  }

  custom_response_body {
    key          = "ApiRateLimitError"
    content_type = "APPLICATION_JSON"
    content = jsonencode({
      success = false
      code    = "COMMON-006"
      message = "요청이 너무 많습니다. 잠시 후 다시 시도해 주세요."
      data    = null
    })
  }

  rule {
    name     = "ApiIpRateLimit"
    priority = 10

    action {
      dynamic "count" {
        for_each = var.waf_mode == "count" ? [1] : []
        content {}
      }
      dynamic "block" {
        for_each = var.waf_mode == "block" ? [1] : []
        content {
          custom_response {
            response_code            = 429
            custom_response_body_key = "ApiRateLimitError"
            response_header {
              name  = "Access-Control-Allow-Origin"
              value = "*"
            }
          }
        }
      }
    }

    statement {
      rate_based_statement {
        limit                 = 600
        evaluation_window_sec = 60
        aggregate_key_type    = "IP"

        scope_down_statement {
          and_statement {
            statement {
              byte_match_statement {
                positional_constraint = "STARTS_WITH"
                search_string         = "/api/"
                field_to_match {
                  uri_path {}
                }
                text_transformation {
                  priority = 0
                  type     = "NONE"
                }
              }
            }
            statement {
              not_statement {
                statement {
                  byte_match_statement {
                    positional_constraint = "EXACTLY"
                    search_string         = "OPTIONS"
                    field_to_match {
                      method {}
                    }
                    text_transformation {
                      priority = 0
                      type     = "NONE"
                    }
                  }
                }
              }
            }
          }
        }
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "ApiIpRateLimit"
      sampled_requests_enabled   = false
    }
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "getddo-dev-api-waf"
    sampled_requests_enabled   = false
  }
}
