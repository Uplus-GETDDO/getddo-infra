# AWS 관리형 정책을 공식 고정 ID로 조회한다. 정책 자체를 새로 만들거나 수정하지 않는다.
# CachingDisabled: 사용자별 API 응답을 다른 사용자에게 캐시해서 전달하지 않는다.
data "aws_cloudfront_cache_policy" "api" {
  id = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad" # Managed-CachingDisabled
}

# Host만 ALB 원본 주소로 바꾸고 나머지 요청 헤더·쿠키·쿼리 문자열은 전달한다.
# Origin/preflight, X-User-*, Idempotency-Key 등이 앱까지 도달해야 한다.
# 헤더를 전달하는 것은 사용자 신원을 인증하는 기능이 아니다.
data "aws_cloudfront_origin_request_policy" "api" {
  id = "b689b0a8-53d0-40ab-baf2-68738e2966ac" # Managed-AllViewerExceptHostHeader
}

# AWS 관리형 보안 응답 헤더를 사용한다. CORS 허용 주소는 앱 설정이 결정한다.
# CORS 정책을 *로 덮어쓰지 않으며, WAF·요청 제한·접근 로그는 이 범위에 추가하지 않는다.
data "aws_cloudfront_response_headers_policy" "api" {
  id = "67f7725c-6f97-4210-82d7-5512b31e9d03" # Managed-SecurityHeadersPolicy
}

# 공개 이미지 파일에만 캐시를 사용한다. 사용자별 API 캐시와 분리한다.
# 파일 수정 시 같은 키를 덮어쓰지 말고 새 키를 만들어 이전 캐시와 혼용되지 않게 한다.
data "aws_cloudfront_cache_policy" "images" {
  id = "658327ea-f89d-4fab-a63d-7e88639e58f6" # Managed-CachingOptimized
}

# OAC는 CloudFront → S3 요청을 SigV4로 항상 서명한다. 이 모드는 HTTPS 원본 연결을 사용한다.
# S3의 정적 웹사이트 주소 대신 일반 regional endpoint에 연결해야 OAC를 사용할 수 있다.
resource "aws_cloudfront_origin_access_control" "images" {
  name                              = "getddo-dev-images"
  description                       = "Read private GETDDO images through CloudFront"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# CloudFront가 사설망 내부 ALB에 접근할 수 있도록 서비스 관리 ENI를 준비한다.
# VPC에는 IGW가 연결되어 있어야 하지만 이 요청이 IGW·NAT를 경유하는 것은 아니다.
# 서비스 연결 역할·ENI·서비스 보안 그룹은 AWS가 생성/관리하므로 수동 리소스로 중복 선언하지 않는다.
resource "aws_cloudfront_vpc_origin" "api" {
  vpc_origin_endpoint_config {
    name = "getddo-dev-api-origin"
    arn  = aws_lb.api.arn

    # HTTPS 포트와 TLS 목록은 API 필수 필드다. http-only이므로 443 연결은 사용하지 않는다.
    http_port              = 80
    https_port             = 443
    origin_protocol_policy = "http-only"
    origin_ssl_protocols {
      items    = ["TLSv1.2"]
      quantity = 1
    }
  }

  tags = { Name = "getddo-dev-api-origin" }

  # ALB가 준비되고 리스너·대상이 연결된 뒤 원본을 생성한다.
  # ALB 인바운드는 원본 생성 후 AWS 서비스 보안 그룹을 조회해서 연결한다.
  depends_on = [
    aws_internet_gateway.main,
    aws_lb_listener.api,
    aws_lb_target_group_attachment.api,
    aws_vpc_security_group_egress_rule.alb_to_api,
  ]
}

# 이 리소스를 apply하면 기본 CloudFront 주소에 공개 HTTPS 진입점이 생긴다.
# 비공개 ALB는 원본 직접 접근을 막지만 공개 API의 인증을 대신하지 않는다.
# 실제 적용 전 시연용 사용자 헤더·Swagger 공개 범위와 예산을 검토한다.
resource "aws_cloudfront_distribution" "api" {
  enabled             = true
  is_ipv6_enabled     = true
  comment             = "GETDDO development API through a private ALB"
  wait_for_deployment = true

  # 한국을 포함한 아시아 엣지를 사용하는 요금 클래스다. 무료 사용을 보장하는 설정은 아니다.
  price_class = "PriceClass_200"

  origin {
    domain_name = aws_lb.api.dns_name
    origin_id   = "getddo-dev-api-alb"
    vpc_origin_config {
      vpc_origin_id = aws_cloudfront_vpc_origin.api.id
    }
  }

  # /images/가 S3 객체 키에도 그대로 포함된다. 예: images/events/새UUID.jpg.
  # origin_path를 추가해 images/images/...로 경로가 중복되지 않게 한다.
  origin {
    domain_name              = aws_s3_bucket.images.bucket_regional_domain_name
    origin_id                = "getddo-dev-images"
    origin_access_control_id = aws_cloudfront_origin_access_control.images.id
  }

  # 이 경로만 S3로 보낸다. API·Swagger는 아래 기본 동작을 통해 기존 ALB로 전달한다.
  # CloudFront를 통한 이미지 업로드·삭제는 허용하지 않는다.
  ordered_cache_behavior {
    path_pattern               = "/images/*"
    target_origin_id           = "getddo-dev-images"
    viewer_protocol_policy     = "https-only"
    allowed_methods            = ["GET", "HEAD"]
    cached_methods             = ["GET", "HEAD"]
    compress                   = true
    cache_policy_id            = data.aws_cloudfront_cache_policy.images.id
    response_headers_policy_id = data.aws_cloudfront_response_headers_policy.api.id
  }

  default_cache_behavior {
    target_origin_id = "getddo-dev-api-alb"

    # API 요청은 HTTPS만 허용한다. HTTP 요청 본문을 리다이렉트로 재전송시키지 않는다.
    viewer_protocol_policy = "https-only"

    # CloudFront의 전체 메서드 묶음을 허용한다. 앱이 지원하지 않는 메서드는 앱에서 거절한다.
    allowed_methods = ["DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"]
    cached_methods  = ["GET", "HEAD"]
    compress        = true

    # cached_methods는 API 필수 필드이며 실제 캐시는 아래 정책으로 비활성화한다.
    cache_policy_id            = data.aws_cloudfront_cache_policy.api.id
    origin_request_policy_id   = data.aws_cloudfront_origin_request_policy.api.id
    response_headers_policy_id = data.aws_cloudfront_response_headers_policy.api.id
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  # AWS의 *.cloudfront.net 주소와 기본 인증서를 사용한다.
  # 사용자 도메인·Route 53·별도 ACM 인증서는 만들지 않는다.
  viewer_certificate {
    cloudfront_default_certificate = true
  }

  tags = { Name = "getddo-dev-api-cloudfront" }

  # AWS 서비스 보안 그룹 → ALB 인바운드가 준비된 뒤 배포를 활성화한다.
  depends_on = [aws_vpc_security_group_ingress_rule.alb_from_cloudfront]
}
