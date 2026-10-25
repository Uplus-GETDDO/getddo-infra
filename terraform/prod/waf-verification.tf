resource "aws_lb_listener_rule" "waf_probe" {
  count = var.waf_probe == null ? 0 : 1

  listener_arn = aws_lb_listener.api.arn
  priority     = var.waf_probe.priority

  action {
    type = "fixed-response"
    fixed_response {
      content_type = "text/plain"
      status_code  = "200"
      message_body = "waf-probe:${var.waf_probe.run_id}"
    }
  }

  condition {
    path_pattern {
      values = ["/api/__waf_probe/${var.waf_probe.run_id}"]
    }
  }

  condition {
    http_request_method {
      values = ["GET"]
    }
  }

  tags = { TestRun = var.waf_probe.run_id }
}
