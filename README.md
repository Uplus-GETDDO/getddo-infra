# GETDDO 인프라

GETDDO의 AWS 인프라, 모니터링, 임시 부하 테스트 환경을 관리하는 Terraform 저장소입니다. 애플리케이션 코드와 컨테이너 배포 스크립트는 [백엔드 저장소](https://github.com/Uplus-GETDDO/getddo-be)에서 관리합니다.

## 시스템 아키텍처

![GETDDO 시스템 아키텍처](docs/architecture/production.png)

## 주요 설계

| 선택 | 이유 | 고려한 점 |
| --- | --- | --- |
| 두 AZ의 API 서버와 ALB | 한 서버를 제외해도 다른 서버로 요청을 보내고 순차 배포 | 서버 두 대의 비용과 배포·상태 관리 필요 |
| RDS Multi-AZ 동기 복제 | 주 DB 장애 시 다른 AZ의 대기 DB로 자동 전환 | 대기 DB 비용이 추가되고 전환 중 요청이 일시 실패할 수 있음 |
| Redis 복제본과 자동 장애 전환 | 두 API가 요청 제한 카운터를 공유하고 장애 후 기능 복구 | 비동기 복제로 최근 카운터가 일부 유실될 수 있음 |
| ECR 이미지와 SSM 순차 배포 | 같은 이미지로 배포하고 앱 기동 실패 시 이전 버전 복구 | 이미지 보관과 서버별 준비·검증·복구 절차 필요 |

두 API는 같은 주 DB와 Redis에 연결합니다. RDS 대기 DB와 Redis 복제본은 장애 시 주 역할을 이어받습니다.

## 저장소 구조

```text
terraform/
├── bootstrap/        # State 저장소·운영자 IAM
├── prod/             # 운영 네트워크·API·DB·Redis·배포 권한
├── monitoring/       # Prometheus·Grafana 서버
├── staging-control/  # 부하 테스트 환경 생성·만료 정리
└── staging/          # 임시 API·DB·Redis·ALB·Locust
observability/        # 모니터링 설정·대시보드
loadtest/             # Locust 시나리오·실행 환경
scripts/              # 검증·환경 관리 도구
docs/                 # 구성·운영 절차·검증 기록
```

## 실제 테스트 결과

| 테스트 케이스 | 확인한 결과 | 상세 기록 |
| --- | --- | --- |
| RDS 강제 장애 전환 | 일시 장애 응답 후 앱 재시작 없이 재연결. 전환 전 데이터와 잔액·원장 유지 | [RDS 장애 전환](docs/rds-failover-test-results.md) |
| Redis 강제 장애 전환 | 복제본 승격 후 앱 재시작 없이 A/C 공유 요청 제한과 만료 해제 복구 | [Redis 장애 전환](docs/redis-failover-test-results.md) |
| 배포 이미지 준비·앱 기동 실패 | 이미지 준비 실패 시 기존 앱 유지. A 서버 기동 실패 시 이전 이미지·설정으로 자동 복구하고 C 배포 중단 | [ECR 배포·복구](docs/ecr-deployment-test-results.md) |
| WAF 요청 제한 | 초과 요청 429, 요청 감소 후 자동 해제, 다른 IP·제외 경로 통과와 차단 로그 확인 | [WAF 차단·해제](docs/waf-rate-limit-test-results.md) |
| 모니터링 수집·복구 | A/C의 API·JVM·호스트 지표와 AWS 지표 조회. 모니터링 서버 재부팅·백업 복원 후 데이터 확인 | [모니터링 검증](docs/monitoring-runtime-verification.md) |

## 시작하기

### 1. 실행 환경 준비

Linux/WSL에서 Terraform 1.16.x, Python 3.12, Bash, Node.js, `uv`를 사용합니다. 실제 AWS 구성에는 AWS CLI v2와 해당 계정의 실행 권한이 필요합니다.

```bash
git clone https://github.com/Uplus-GETDDO/getddo-infra.git
cd getddo-infra
uv venv --python 3.12 /tmp/getddo-infra-venv
uv pip install --python /tmp/getddo-infra-venv/bin/python \
  PyYAML==6.0.3 boto3==1.40.59 Werkzeug==3.1.3 \
  PyMySQL==1.1.2 requests==2.34.2
source /tmp/getddo-infra-venv/bin/activate
```

### 2. 로컬 검증

```bash
python scripts/validate.py            # Terraform·스크립트 검사
python scripts/validate.py documents  # 문서 링크·회귀 검사
```

Terraform 검사는 mock AWS Provider를 사용하므로 AWS 로그인이나 실제 자원 생성 없이 실행할 수 있습니다. Docker·브라우저를 사용하는 추가 CI 검사는 [검증 workflow](.github/workflows/validate.yml)에 정의되어 있습니다.

### 3. AWS 인프라 구성

인증된 AWS 프로필, 원격 State 접근 권한, 환경별 입력값을 준비합니다. 기본 구성 순서는 `bootstrap` → `prod` → `monitoring`입니다. 임시 부하 테스트 환경은 `staging-control` 준비 후 생성합니다.

[인프라 실행 안내](docs/infrastructure-development.md#실제-aws-환경에서-실행)에 AWS 인증, 필수 입력값과 `init` → `plan` → `apply` 명령을 정리했습니다. 현재 코드는 기존 AWS 계정과 자원을 참조하므로, 새 계정에서 재현할 때는 계정 제한·State 저장소·기존 자원 참조를 먼저 맞춰야 합니다.

## 구성·운영 문서

| 작업 | 문서 |
| --- | --- |
| 앱 배포 | [ECR·SSM 배포](docs/ecr-deployment.md) |
| 장애 전환 | [RDS](docs/rds-multiaz.md) · [Redis](docs/redis-ha.md) |
| 요청 제한 | [WAF](docs/waf-rate-limit.md) |
| 모니터링 | [Grafana 구성·접속·복구](docs/grafana-https-access.md) |
| 부하 테스트 | [스테이징·Locust 사용과 결과 수집](docs/staging-loadtest.md) |
| 서버·DB 사양 | [사양 변경](docs/capacity-resize.md) · [DB 연결 수](docs/database-connections.md) |
