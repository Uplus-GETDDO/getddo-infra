# Existing S3 resource addresses are preserved during repository relocation.
resource "aws_s3_bucket" "releases" {
  bucket        = "getddo-deploy-${data.aws_caller_identity.current.account_id}-${var.aws_region}"
  force_destroy = false
  tags          = { Name = "getddo-dev-releases" }

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_public_access_block" "releases" {
  bucket                  = aws_s3_bucket.releases.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "releases" {
  bucket = aws_s3_bucket.releases.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "releases" {
  bucket = aws_s3_bucket.releases.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_policy" "releases" {
  bucket = aws_s3_bucket.releases.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyInsecureTransport"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource  = [aws_s3_bucket.releases.arn, "${aws_s3_bucket.releases.arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })
}

# 배포 파일만 14일 뒤 만료한다. 서버의 직전 성공 이미지에는 영향이 없다.
resource "aws_s3_bucket_lifecycle_configuration" "releases" {
  bucket = aws_s3_bucket.releases.id
  rule {
    id     = "expire-release-files"
    status = "Enabled"
    filter {
      prefix = "releases/"
    }
    expiration {
      days = 14
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}


# 이벤트·배너 이미지 전용 저장소다. 배포 파일·Terraform State 버킷과 분리한다.
# 이 버킷은 VPC 서브넷에 배치하지 않는다. 현재 EC2의 기존 인터넷 경로로 HTTPS 접근한다.
# Gateway Endpoint·추가 서브넷·라우팅·보안 그룹 변경은 이번 단계에 포함하지 않는다.
resource "aws_s3_bucket" "images" {
  bucket        = "getddo-images-${data.aws_caller_identity.current.account_id}-${var.aws_region}"
  force_destroy = false
  tags          = { Name = "getddo-dev-images" }

  # 실수로 버킷과 이미지를 삭제하지 않게 한다. 실제 정리는 데이터 보존 검토 후 별도 수행한다.
  lifecycle {
    prevent_destroy = true
  }
}

# 브라우저가 S3 공개 주소로 직접 읽거나 쓰는 방식은 허용하지 않는다.
# 이미지는 CloudFront OAC로 읽고, 업로드는 EC2 역할로 서명한 백엔드 요청만 사용한다.
resource "aws_s3_bucket_public_access_block" "images" {
  bucket                  = aws_s3_bucket.images.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ACL 대신 IAM·버킷 정책으로 관리한다. 업로드 코드에서 public-read ACL을 지정하지 않는다.
resource "aws_s3_bucket_ownership_controls" "images" {
  bucket = aws_s3_bucket.images.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

# 저장 암호화는 SSE-S3를 사용한다. 별도 KMS 키·키 사용 권한은 만들지 않는다.
resource "aws_s3_bucket_server_side_encryption_configuration" "images" {
  bucket = aws_s3_bucket.images.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# 미완료 multipart 조각만 정리한다. 정상 업로드된 이벤트·배너 이미지는 자동 만료하지 않는다.
resource "aws_s3_bucket_lifecycle_configuration" "images" {
  bucket = aws_s3_bucket.images.id
  rule {
    id     = "abort-incomplete-image-uploads"
    status = "Enabled"
    filter {
      prefix = "images/"
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

resource "aws_s3_bucket_policy" "images" {
  bucket = aws_s3_bucket.images.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "DenyInsecureTransport"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource  = [aws_s3_bucket.images.arn, "${aws_s3_bucket.images.arn}/*"]
        Condition = { Bool = { "aws:SecureTransport" = "false" } }
      },
      {
        Sid       = "AllowThisCloudFrontReadImages"
        Effect    = "Allow"
        Principal = { Service = "cloudfront.amazonaws.com" }
        Action    = ["s3:GetObject"]
        Resource  = "${aws_s3_bucket.images.arn}/images/*"
        # 다른 사람의 CloudFront가 이 버킷을 읽지 못하도록 우리 배포 ARN으로 제한한다.
        Condition = { StringEquals = { "AWS:SourceArn" = aws_cloudfront_distribution.api.arn } }
      },
    ]
  })
}

# S3 CORS는 만들지 않는다. 프론트 업로드는 S3가 아닌 기존 API에 보내기 때문이다.
# 이미지의 브라우저 canvas 사용 등 별도 CORS 요구가 생기면 조회 정책을 검토한다.
