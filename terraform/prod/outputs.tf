output "api_instance_id" {
  value = aws_instance.api.id
}

output "api_public_ip" {
  value = aws_instance.api.public_ip
}

output "api_url" {
  # 기존 운영자용 직접 HTTP 주소를 보존한다. 프론트에는 cloudfront_api_url을 사용한다.
  value = "http://${aws_instance.api.public_ip}:8080"
}

# 아래 값은 apply 후에만 실제 값으로 확정된다. 내부 ALB DNS는 프론트 전달용이 아니다.
output "alb_dns_name" {
  description = "Private ALB DNS name; reachable through the VPC origin"
  value       = aws_lb.api.dns_name
}

output "api_target_group_arn" {
  description = "Target group ARN for checking API target health"
  value       = aws_lb_target_group.api.arn
}

output "cloudfront_distribution_id" {
  description = "CloudFront distribution ID for deployment status checks"
  value       = aws_cloudfront_distribution.api.id
}

output "cloudfront_vpc_origin_id" {
  description = "CloudFront VPC origin connected to the private ALB"
  value       = aws_cloudfront_vpc_origin.api.id
}

output "cloudfront_api_url" {
  description = "Public HTTPS API URL to configure in the frontend after verification"
  value       = "https://${aws_cloudfront_distribution.api.domain_name}"
}

# 후속 백엔드 업로드 구현에 전달할 값이다. 이 출력만으로 앱에 설정이 주입되지는 않는다.
output "images_bucket_name" {
  description = "Private S3 bucket for backend image uploads"
  value       = aws_s3_bucket.images.id
}

# 객체 키 images/events/UUID.jpg의 공개 URL은 CloudFront 주소 + / + 객체 키다.
output "images_base_url" {
  description = "Public image URL prefix through the existing CloudFront distribution"
  value       = "https://${aws_cloudfront_distribution.api.domain_name}/images/"
}

output "db_address" {
  value = aws_db_instance.main.address
}

output "db_port" {
  value = aws_db_instance.main.port
}

# apply 후 연결 담당자에게 전달할 사설 DNS다. 주소 출력만으로 앱 연결이 완료되지는 않는다.
# IP를 고정하지 말고 이 DNS를 사용한다. 인증 비밀번호는 output으로 제공하지 않는다.
output "redis_primary_endpoint" {
  description = "Private Redis primary endpoint shared by API servers; TLS and AUTH required"
  value       = aws_elasticache_replication_group.redis.primary_endpoint_address
}

output "redis_port" {
  description = "Redis TLS port; accessible only from the API security group"
  value       = aws_elasticache_replication_group.redis.port
}

# 자원 생성 후 이 네 값을 GitHub production 환경의 Variables에 등록한다.
output "github_production_variables" {
  value = {
    AWS_REGION        = var.aws_region
    AWS_ROLE_ARN      = aws_iam_role.github_cd.arn
    DEPLOY_BUCKET     = aws_s3_bucket.releases.id
    EC2_INSTANCE_ID   = aws_instance.api.id
    EC2_INSTANCE_ID_A = aws_instance.api.id
    EC2_INSTANCE_ID_C = aws_instance.api_c.id
    TARGET_GROUP_ARN  = aws_lb_target_group.api.arn
  }
}

output "api_c_instance_id" {
  value = aws_instance.api_c.id
}

output "api_c_subnet_id" {
  value = aws_subnet.public_c.id
}

output "api_c_private_ip" {
  value = aws_instance.api_c.private_ip
}

# 공개 CA와 Agent 설정만 포함한다. 앱 env·비밀번호는 조회하거나 출력하지 않는다.
output "api_c_user_data" {
  description = "Public bootstrap script, identical to the console copy in the HTML guide"
  value       = local.api_c_user_data
}

output "api_c_bootstrap_sha256" {
  value = sha256(local.api_c_user_data)
}

output "api_deployment_metadata" {
  description = "Non-secret deployment handoff; C registration is an explicit readiness gate"
  value = {
    environment      = "dev"
    target_group_arn = aws_lb_target_group.api.arn
    target_port      = aws_lb_target_group.api.port
    servers = {
      a = {
        instance_id             = aws_instance.api.id
        private_ip              = aws_instance.api.private_ip
        registered_in_terraform = true
      }
      c = {
        instance_id             = aws_instance.api_c.id
        private_ip              = aws_instance.api_c.private_ip
        registered_in_terraform = var.api_c_register_in_target_group
      }
    }
  }
}

output "waf_web_acl_id" {
  value = aws_wafv2_web_acl.api.id
}

output "waf_web_acl_arn" {
  value = aws_wafv2_web_acl.api.arn
}

output "waf_rule_name" {
  value = "ApiIpRateLimit"
}

output "waf_mode" {
  value = var.waf_mode
}

output "waf_log_mode" {
  value = var.waf_log_mode
}

output "waf_log_group_name" {
  value = aws_cloudwatch_log_group.waf.name
}

output "waf_probe_path" {
  value = var.waf_probe == null ? null : "/api/__waf_probe/${var.waf_probe.run_id}"
}

output "waf_rate_limit_settings" {
  value = {
    limit          = 600
    window_seconds = 60
    aggregation    = "IP"
  }
}
