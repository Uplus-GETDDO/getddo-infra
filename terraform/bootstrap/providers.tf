# dev 환경에서 사용할 State 저장소를 먼저 준비하는 Terraform 구성이다.
terraform {
  # Terraform 프로그램 자체는 1.16.x 버전을 사용한다.
  required_version = "~> 1.16.0"

  # AWS API와 통신하는 Provider 플러그인을 지정한다.
  required_providers {
    aws = {
      # Provider는 Terraform 본체와 별도로 버전이 관리된다. 6.0 이상, 7.0 미만을 허용한다.
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

# AWS 자원을 구성할 리전이다. ap-northeast-2는 서울 리전이다.
# 로그인 프로필은 실행하는 터미널의 AWS_PROFILE 환경 변수로 지정한다.
provider "aws" {
  region = "ap-northeast-2"
}
