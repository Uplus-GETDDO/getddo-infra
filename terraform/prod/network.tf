# 개발 환경에서 사용할 VPC
resource "aws_vpc" "main" {
  # 사설 IP 범위: 10.20.0.0 ~ 10.20.255.255
  cidr_block = "10.20.0.0/16"

  # AWS DNS 서버를 통한 이름 조회 허용
  enable_dns_support = true

  # AWS 제공 DNS 호스트 이름 기능 활성화
  enable_dns_hostnames = true

  # AWS 콘솔에서 식별할 이름표
  tags = {
    Name = "getddo-dev-vpc"
  }
}

# 첫 EC2를 배치할 서브넷
resource "aws_subnet" "public_a" {
  # 이미 생성한 VPC에 연결한다.
  vpc_id = aws_vpc.main.id

  # 이 서브넷이 사용할 사설 IP 범위
  cidr_block = "10.20.0.0/24"

  # 서울 리전의 a 가용 영역에 배치한다.
  availability_zone = "ap-northeast-2a"

  # 공인 IP는 나중에 EC2 생성 설정에서 명시적으로 할당한다.
  map_public_ip_on_launch = false

  tags = {
    Name = "getddo-dev-public-a"
  }
}

# 내부 ALB 전용 서브넷이다. 기존 EC2·RDS 서브넷을 이동하거나 수정하지 않는다.
# ALB는 서로 다른 AZ의 서브넷 두 개가 필요하며, /24로 확장용 여유 IP를 확보한다.
# 공인 IP와 인터넷 기본 경로를 두지 않고 VPC 내부 경로로 기존 EC2에 접근한다.
resource "aws_subnet" "alb" {
  for_each = {
    a = { cidr = "10.20.10.0/24", az = "ap-northeast-2a" }
    c = { cidr = "10.20.11.0/24", az = "ap-northeast-2c" }
  }

  vpc_id                  = aws_vpc.main.id
  cidr_block              = each.value.cidr
  availability_zone       = each.value.az
  map_public_ip_on_launch = false
  tags                    = { Name = "getddo-dev-alb-${each.key}" }
}

# VPC와 인터넷을 연결할 인터넷 게이트웨이
resource "aws_internet_gateway" "main" {
  # 이미 생성한 개발 VPC에 연결한다.
  vpc_id = aws_vpc.main.id

  # AWS 콘솔에서 식별할 이름표
  tags = {
    Name = "getddo-dev-igw"
  }
}

# 기존 public_a만 인터넷 게이트웨이를 기본 경로로 사용한다.
resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "getddo-dev-public-rt" }
}

resource "aws_route" "public_internet" {
  route_table_id         = aws_route_table.public.id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.main.id
}

resource "aws_route_table_association" "public_a" {
  subnet_id      = aws_subnet.public_a.id
  route_table_id = aws_route_table.public.id
}

# RDS는 Single-AZ여도 서로 다른 AZ의 서브넷을 포함한 DB subnet group이 필요하다.
# 기존 가이드에서 예약한 data-a/data-c 범위를 그대로 사용한다.
resource "aws_subnet" "data" {
  for_each = {
    a = { cidr = "10.20.20.0/24", az = "ap-northeast-2a" }
    c = { cidr = "10.20.21.0/24", az = "ap-northeast-2c" }
  }

  vpc_id                  = aws_vpc.main.id
  cidr_block              = each.value.cidr
  availability_zone       = each.value.az
  map_public_ip_on_launch = false
  tags                    = { Name = "getddo-dev-data-${each.key}" }
}

# DB 서브넷에는 VPC 내부 통신 경로만 둔다. IGW·NAT 기본 경로는 추가하지 않는다.
resource "aws_route_table" "data" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "getddo-dev-data-rt" }
}

resource "aws_route_table_association" "data" {
  for_each       = aws_subnet.data
  subnet_id      = each.value.id
  route_table_id = aws_route_table.data.id
}

# ALB 전용 사설 라우팅이다. AWS가 제공하는 VPC local 경로만 사용한다.
# VPC origin은 IGW 연결 자체를 요구하지만 ALB 서브넷에 IGW·NAT 기본 경로는 필요 없다.
resource "aws_route_table" "alb" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "getddo-dev-alb-rt" }
}

resource "aws_route_table_association" "alb" {
  for_each       = aws_subnet.alb
  subnet_id      = each.value.id
  route_table_id = aws_route_table.alb.id
}
