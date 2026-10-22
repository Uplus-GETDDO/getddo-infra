terraform {
  backend "s3" {
    # bootstrap에서 이미 만든 State 저장용 버킷
    bucket = "getddo-tfstate-942466928325-ap-northeast-2"

    # Prod의 기존 State 경로: 폴더를 옮겨도 legacy dev key를 유지한다.
    key = "dev/terraform.tfstate"

    # 버킷이 있는 서울 리전
    region = "ap-northeast-2"

    # State 저장 암호화와 동시 수정 방지
    encrypt      = true
    use_lockfile = true
  }
}
