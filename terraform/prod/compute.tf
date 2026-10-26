# 공개 키만 AWS와 State에 보관한다.
resource "aws_key_pair" "api" {
  key_name   = "getddo-dev-api"
  public_key = var.ssh_public_key
  tags       = { Name = "getddo-dev-api-key" }
}

resource "aws_instance" "api" {
  ami                         = var.ami_id
  instance_type               = "t3.medium"
  subnet_id                   = aws_subnet.public_a.id
  associate_public_ip_address = true
  vpc_security_group_ids      = [aws_security_group.api.id]
  key_name                    = aws_key_pair.api.key_name

  # SSM 접속과 배포 파일 읽기는 인스턴스 역할의 임시 자격 증명을 사용한다.
  iam_instance_profile        = aws_iam_instance_profile.api.name
  user_data                   = file("${path.module}/templates/api-user-data.sh")
  user_data_replace_on_change = true

  # 현재 서버는 설치 복구·앱 설정·배포가 끝난 상태다. 초기 설치 스크립트의
  # curl 수정만으로 기존 EC2와 서버 안의 app.env·배포 파일을 삭제하지 않는다.
  # 기존 EC2의 user_data 차이는 반영하지 않고, 새 EC2 생성에는 최신 스크립트를 쓴다.
  # 기존 서버의 도구 변경은 SSM 등으로 별도 수행한다. AMI·서브넷 등 다른
  # 속성에 의한 교체까지 막는 설정은 아니므로 apply 전 전체 plan은 계속 검토한다.
  lifecycle {
    ignore_changes = [user_data]
  }

  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required"
    # Docker bridge를 거쳐 앱이 IMDSv2 토큰 응답을 받을 수 있도록 1 → 2로 조정한다.
    # 토큰 필수는 유지한다. 컨테이너들이 EC2 역할을 사용할 수 있으므로 신뢰하는 앱만 실행한다.
    # 메타데이터 설정은 실제 plan에서 in-place 변경인지 확인하고, 컨테이너 인증은 별도 검증한다.
    http_put_response_hop_limit = 2
  }

  root_block_device {
    volume_type           = "gp3"
    volume_size           = 20
    encrypted             = true
    delete_on_termination = true
  }

  # 개발 서버의 장시간 부하로 추가 CPU 크레딧 요금이 발생하지 않게 한다.
  credit_specification {
    cpu_credits = "standard"
  }

  tags        = { Name = "getddo-dev-api" }
  volume_tags = { Name = "getddo-dev-api-root" }

  # cloud-init의 패키지 설치 전에 인터넷 경로와 외부 통신 허용을 완성한다.
  depends_on = [
    aws_route.public_internet,
    aws_route_table_association.public_a,
    aws_vpc_security_group_egress_rule.api,
    aws_iam_role_policy_attachment.api_ssm,
    aws_iam_role_policy.api_releases,
  ]
}

# C bootstrap only: no application deployment or target registration.
locals {
  # A의 초기 설치 파일은 변경하지 않고 C에서만 Agent 설치 단계를 덧붙인다.
  api_c_user_data = join("\n", [
    file("${path.module}/templates/api-user-data.sh"),
    templatefile("${path.module}/templates/cloudwatch-agent.sh.tftpl", {
      cloudwatch_agent_config = file("${path.module}/templates/cloudwatch-agent.json")
    }),
  ])
}

resource "aws_subnet" "public_c" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = "10.20.1.0/24"
  availability_zone       = "ap-northeast-2c"
  map_public_ip_on_launch = false
  tags                    = { Name = "getddo-dev-public-c" }

}

# 기존 A 연결·IGW 경로는 관리하지 않고 새 C 연결 하나만 추가한다.
resource "aws_route_table_association" "public_c" {
  subnet_id      = aws_subnet.public_c.id
  route_table_id = aws_route_table.public.id
}

# Preserve Amazon ownership, architecture and image state checks from the old C root.
data "aws_ami" "api_c" {
  owners = ["amazon"]
  filter {
    name   = "image-id"
    values = [var.api_c_ami_id]
  }
  filter {
    name   = "architecture"
    values = ["x86_64"]
  }
  filter {
    name   = "state"
    values = ["available"]
  }
}
resource "aws_instance" "api_c" {
  ami                         = data.aws_ami.api_c.id
  instance_type               = "t3.medium"
  subnet_id                   = aws_subnet.public_c.id
  associate_public_ip_address = true
  vpc_security_group_ids      = [aws_security_group.api.id]
  key_name                    = aws_key_pair.api.key_name
  iam_instance_profile        = aws_iam_instance_profile.api.name
  monitoring                  = false
  user_data                   = local.api_c_user_data
  user_data_replace_on_change = true

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 2
  }

  root_block_device {
    volume_type           = "gp3"
    volume_size           = 20
    encrypted             = true
    delete_on_termination = true
  }

  credit_specification {
    cpu_credits = "standard"
  }

  tags        = { Name = "getddo-dev-api-c" }
  volume_tags = { Name = "getddo-dev-api-c-root" }

  # import 후 초기 설치 파일 차이만으로 준비한 서버를 재생성하지 않는다.
  # 이후 Agent·도구 변경은 별도 관리한다. AMI·서브넷 차이에 의한 교체는 숨기지 않는다.
  lifecycle {
    ignore_changes = [user_data]
  }

  # Bootstrap downloads and Agent startup require networking and runtime permissions.
  depends_on = [
    aws_route.public_internet,
    aws_route_table_association.public_c,
    aws_vpc_security_group_egress_rule.api,
    aws_iam_role_policy_attachment.api_ssm,
    aws_iam_role_policy.api_releases,
    aws_iam_role_policy.api_observability,
    aws_cloudwatch_log_group.api,
  ]
}
