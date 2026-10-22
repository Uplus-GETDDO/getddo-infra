# 현재 인증된 AWS 계정과 사용자 정보를 조회한다. AWS 자원을 생성하지는 않는다.
data "aws_caller_identity" "current" {}

# State 파일을 저장할 S3 버킷을 선언한다. 실제 생성은 terraform apply에서 수행한다.
# 코드에서 이 자원을 참조할 때는 aws_s3_bucket.terraform_state를 사용한다.
resource "aws_s3_bucket" "terraform_state" {
  # ${...} 부분에 조회한 AWS 계정 번호를 넣어 실제 버킷 이름을 만든다.
  bucket = "getddo-tfstate-${data.aws_caller_identity.current.account_id}-ap-northeast-2"

  # Terraform이 이 버킷을 삭제하거나 교체하는 계획을 거부한다.
  lifecycle {
    prevent_destroy = true
  }
}

# State 파일의 이전 버전을 보관한다.
resource "aws_s3_bucket_versioning" "terraform_state" {
  # 앞서 선언한 S3 버킷에 적용한다.
  bucket = aws_s3_bucket.terraform_state.id

  versioning_configuration {
    status = "Enabled" # 버전 관리 활성화
  }
}

# State 버킷의 공개 접근을 차단한다.
resource "aws_s3_bucket_public_access_block" "terraform_state" {
  # 앞에서 선언한 State 버킷에 적용한다.
  bucket = aws_s3_bucket.terraform_state.id

  # 공개 접근을 허용하는 ACL을 새로 설정하지 못하게 한다.
  block_public_acls = true

  # 기존 공개 ACL이 있어도 공개 접근 허용 효과를 무시한다.
  ignore_public_acls = true

  # 공개 접근을 허용하는 버킷 정책을 새로 설정하지 못하게 한다.
  block_public_policy = true

  # 공개 버킷 정책이 있더라도 익명 사용자와 외부 계정의 접근을 제한한다.
  restrict_public_buckets = true
}

# State 버킷의 기본 저장 암호화 방식을 지정한다.
resource "aws_s3_bucket_server_side_encryption_configuration" "terraform_state" {
  # 앞에서 선언한 State 버킷에 적용한다.
  bucket = aws_s3_bucket.terraform_state.id

  # 이 버킷에 적용할 암호화 규칙이다.
  rule {
    # 업로드할 때 암호화 방식을 따로 지정하지 않으면 사용할 기본값이다.
    apply_server_side_encryption_by_default {
      # S3가 키를 관리하는 SSE-S3 방식
      sse_algorithm = "AES256"
    }
  }
}
