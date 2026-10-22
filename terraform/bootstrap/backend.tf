terraform {
  backend "s3" {
    # 이미 만들어 둔 State 저장용 버킷
    bucket = "getddo-tfstate-942466928325-ap-northeast-2"

    # 버킷 안에서 bootstrap의 State를 저장할 경로
    key = "bootstrap/terraform.tfstate"

    # 버킷이 있는 서울 리전
    region = "ap-northeast-2"

    # State를 저장할 때 암호화 사용
    encrypt = true

    # 같은 State를 동시에 수정하지 못하도록 잠금 사용
    use_lockfile = true
  }
}
