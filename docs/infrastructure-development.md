# 인프라 작업과 로컬 검증

## Terraform 실행 루트

| 디렉터리 | S3 State 키 | 관리 범위 |
| --- | --- | --- |
| `terraform/bootstrap` | `bootstrap/terraform.tfstate` | State S3·운영자 및 Stage 실행 역할 IAM |
| `terraform/prod` | `dev/terraform.tfstate` | 운영 네트워크·API·데이터 저장소·배포 IAM·CloudWatch·공유 HTTPS 경로 |
| `terraform/monitoring` | `monitoring/terraform.tfstate` | 상시 monitor 서버·독립 EBS·Grafana 접근/수집 권한 |
| `terraform/staging-control` | `staging-control/terraform.tfstate` | 지속 CodeBuild·독립 Scheduler·실패 정리 경로 |
| `terraform/staging` | `staging/terraform.tfstate` | 임시 workload·공유 ALB/monitor의 Stage 전용 추가 규칙 |

기존 State 키와 AWS 자원 이름(`getddo-dev-*`), Parameter Store 경로, 로그 그룹 이름은 유지합니다. `dev` 표기는 기존 이름이며 별도 개발 환경은 없습니다.

## 로컬 검증

필수 도구: Terraform 1.16.x, Python 3.12, Bash. 문서/관측 설정 검사에는 Node.js가 필요합니다. AWS Provider 버전은 각 `.terraform.lock.hcl`에 고정합니다. 아래는 Linux/WSL에서 `uv`로 검증 환경을 준비하는 예시입니다.

```bash
uv venv --python 3.12 /tmp/getddo-infra-venv
uv pip install --python /tmp/getddo-infra-venv/bin/python PyYAML==6.0.3 boto3==1.40.59 Werkzeug==3.1.3 PyMySQL==1.1.2 requests==2.34.2
source /tmp/getddo-infra-venv/bin/activate

# Terraform·스크립트 검사
/tmp/getddo-infra-venv/bin/python scripts/validate.py

# 개별 검사
/tmp/getddo-infra-venv/bin/python scripts/validate.py terraform
/tmp/getddo-infra-venv/bin/python scripts/validate.py scripts
/tmp/getddo-infra-venv/bin/python scripts/validate.py documents
```

Terraform 검사는 backend를 제거한 임시 복사본과 mock AWS Provider를 사용하며 AWS·원격 State에 접근하지 않습니다. CI는 이 검사 외에도 Prometheus/Compose/대시보드 설정, 실제 Locust master/worker·Chromium UI 통합 시험과 Docker 이미지 빌드를 수행합니다. 전체 CI 절차와 추가 도구는 [검증 workflow](../.github/workflows/validate.yml)를 따릅니다.

## 실제 AWS 환경에서 실행

### 인증과 State 준비

이 구성은 기존 AWS 계정과 S3 State를 사용합니다. 신규 계정에서는 Provider의 `allowed_account_ids`, 각 backend의 버킷, IAM 주체와 기존 자원 조회 조건을 먼저 변경해야 합니다. 최초 State 버킷 생성은 원격 backend 연결에 앞서 준비합니다.

본인에게 설정된 AWS 프로필로 인증합니다. 프로필이 없거나 로그인이 만료됐다면 [AWS 인증 설정](staging-loadtest.md#aws-인증-설정)을 먼저 완료합니다.

```bash
export AWS_PROFILE=getddo-dev  # 본인에게 설정된 실행 프로필
aws sts get-caller-identity --region ap-northeast-2
```

STS 결과의 계정·실행 주체를 확인하고, 작업에 필요한 State 및 자원 변경 권한을 준비합니다. IAM을 변경하는 bootstrap 권한과 일반 실행 권한은 구분합니다.

### 입력값 준비

```bash
cp terraform/prod/prod.tfvars.example terraform/prod/prod.tfvars
```

| 입력 | 준비할 값 |
| --- | --- |
| `admin_ipv4_cidr` | 관리자 공인 IPv4 한 개의 `/32` 범위 |
| `ami_id` | 서울 리전의 사용 가능한 Amazon Linux 2023 x86_64 AMI |
| `ssh_public_key` | 관리자 ED25519 공개 키 |
| `TF_VAR_db_master_password` | 실행 환경으로 전달할 RDS 관리자 비밀번호 |
| `TF_VAR_redis_auth_token` | 실행 환경으로 전달할 Redis 인증 비밀번호 |
| monitoring의 `cloudfront_distribution_id` | prod의 `cloudfront_distribution_id` 출력값 |

두 비밀번호는 32~64자 영문·숫자여야 하며, 승인된 비밀 관리 경로에서 환경 변수로 주입합니다. 기존 환경에서는 현재 값과 버전을 유지하고 회전 작업일 때만 변경합니다. 비밀번호는 tfvars나 명령 인자에 넣지 않습니다. 입력의 상세 조건과 기본값은 [prod 변수](../terraform/prod/variables.tf)와 [monitoring 변수](../terraform/monitoring/variables.tf)에 정의되어 있습니다.

### 적용 순서

| 순서 | 실행 루트 | 준비·연결 항목 |
| --- | --- | --- |
| 1 | `terraform/bootstrap` | State 저장소, 운영자·서비스 역할과 IAM 정책 |
| 2 | `terraform/prod` | 운영 네트워크·API·DB·Redis·이미지 및 배포 저장소 |
| 3 | `terraform/monitoring` | prod의 네트워크·ALB·CloudFront를 참조하는 모니터링 서버 |
| 선택 | `terraform/staging-control` | bootstrap의 실행 역할과 기존 prod 자원을 사용하는 환경 제어 기능 |
| 선택 | 스테이징 생성 명령 | 검토한 bundle·설정·만료 시각으로 임시 자원 생성 |

기존 환경을 수정할 때는 변경하는 루트와 연결된 출력값을 확인해 필요한 순서로 적용합니다. 아래는 prod의 실행 예시이며, 앞서 준비한 환경 변수와 `prod.tfvars`가 필요합니다.

```bash
terraform -chdir=terraform/prod init
terraform -chdir=terraform/prod plan -var-file=prod.tfvars -out=tfplan
terraform -chdir=terraform/prod show tfplan
# 계획의 생성·수정·교체·삭제 범위를 검토한 뒤 실행
terraform -chdir=terraform/prod apply tfplan
```

각 루트는 위 State 키와 잠금을 유지합니다. monitoring의 런타임 설치·HTTPS 연결은 [Grafana 구성 절차](grafana-https-access.md), 임시 환경의 bundle 준비·생성·수집·삭제는 [스테이징 실행 절차](staging-loadtest.md#팀원이-처음-실행할-때)를 따릅니다. 운영 앱 이미지 배포는 [백엔드 Container CD](https://github.com/Uplus-GETDDO/getddo-be/blob/main/docs/06-runbook/container-cd.md)에서 이어집니다.

## 운영 변경 시 확인

- [입력 예시](../terraform/prod/prod.tfvars.example)를 참고하고 실제 tfvars·State·plan·env·자격 증명·개인 키는 Git에서 제외합니다.
- DB·Redis 비밀값은 ephemeral/write-only 입력으로 전달합니다.
- 실행 프로필은 `AWS_PROFILE`로 선택합니다. 작업 전 실제 계정과 역할을 확인합니다.
- 변경 전 State 잠금을 유지한 전체 plan에서 생성·교체·삭제 범위를 검토합니다.
- API의 시연용 사용자 선택 헤더는 사용자 인증 수단이 아닙니다.
