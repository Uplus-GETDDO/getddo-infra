# GETDDO 인프라

GETDDO 운영 인프라·공용 모니터링·임시 스테이징 부하 테스트 환경을 관리하는 Terraform 저장소.
서울 리전(`ap-northeast-2`)에서 CloudFront·ALB·EC2·RDS·Redis·S3·ECR을 사용합니다.

## 운영 아키텍처

![GETDDO 운영 아키텍처](docs/architecture/production.svg)

[크게 보기 · 이미지 내보내기](docs/architecture/production.html) · [편집 원본](docs/architecture/production.architecture.json)

| 구성 | 역할 |
| --- | --- |
| CloudFront · WAF | 공개 HTTPS 진입점. `/api/`는 접속 IP별 600회/60초 기준으로 초과 요청에 429 반환(OPTIONS 제외). 통과한 API는 캐시 없이 내부 ALB로 전달하고, `/images/*`는 OAC로 비공개 S3에서 조회 |
| 내부 ALB | 두 AZ의 사설 서브넷에 배치. HTTP 80 → API 서버 8080으로 요청 분산 |
| API A / C | `ap-northeast-2a` / `ap-northeast-2c`의 `t3.small` EC2. Docker 앱 실행, 공인 IPv4·IGW로 외부 통신, 보안 그룹으로 인바운드 제한 |
| RDS | 비공개 MySQL 8.4, `db.t4g.micro`. 메인·대기 DB를 다른 AZ에 배치해 동기 복제·자동 전환. API 보안 그룹에서만 3306 접속 허용 |
| Redis | 다른 AZ의 메인·복제본에 비동기 복제·자동 전환 적용. TLS·AUTH 공유 요청 제한 저장소. 최근 제한 값 일부 유실 가능 |
| S3 | 이미지·배포 파일 저장소 분리. API 역할로 이미지 업로드, CloudFront OAC로 조회 |
| Parameter Store | 앱 설정·비밀값 저장. 서버 역할로 조회 |
| ECR | 비공개 운영 컨테이너 이미지. 불변 태그, digest 고정 배포, 태그 있는 복구 이미지 보존 |
| GitHub Actions · SSM | main 전용 OIDC로 ECR 업로드, S3 배포 파일 전달, SSM으로 A/C 사전 pull·순차 배포 |
| CloudWatch | API·ALB·RDS·Redis 지표, 앱 로그, 대시보드. WAF 차단 로그는 7일 보관 |
| Prometheus · Grafana | 상시 모니터링 EC2. 기존 CloudFront → 내부 ALB → `/grafana/`로 접속하며 로그인 필요 |

- 실선: 서비스 요청 / 점선: 설정·배포·모니터링
- 외부 HTTPS는 CloudFront에서 종료. ALB·API 구간은 사설망 HTTP
- DB 관리 접속은 EC2 Instance Connect Endpoint·SSH 터널로 API 서버 경유
- RDS 대기 DB와 Redis 복제본은 조회 분산용이 아니라 장애 대비용이다. 두 API 서버는 같은 DB·Redis 메인을 사용하며, 전환 후에도 기존 접속 주소를 유지한다.
- DB 연결 실패는 앱에서 `503 / COMMON-007`로 응답한다. Redis 판정 실패는 기존 앱의 요청 제한을 건너뛰고 업무 검증을 계속한다.

| 운영 검증 | 핵심 결과 | 상세 |
| --- | --- | --- |
| ECR 이미지 배포 | 기존 이미지 이전·동일 digest 순차 배포·준비 실패 시 유지·실제 기동 실패 자동 복구 | [시험 결과](docs/ecr-deployment-test-results.md) · [운영 절차](docs/ecr-deployment.md) |
| RDS 장애 전환 | 실제 전환·503·앱 재시작 없는 재연결·데이터 보존·동일 요청 중복 방지 | [시험 결과](docs/rds-failover-test-results.md) · [운영 절차](docs/rds-multiaz.md) |
| Redis 장애 전환 | 실제 전환·A/C 공유 제한·만료 해제·응모 평가·합성 차단 이력 저장 | [시험 결과](docs/redis-failover-test-results.md) · [운영 절차](docs/redis-ha.md) |
| WAF 요청 제한 | IP별 차단·다른 IP 통과·자동 해제·로그 마스킹·차단 로그 보관 | [시험 결과](docs/waf-rate-limit-test-results.md) · [운영 절차](docs/waf-rate-limit.md) |

## 레포 구조

```text
getddo-infra/
├── .github/workflows/validate.yml   # Terraform / 스크립트·설정 CI 검증
├── terraform/
│   ├── bootstrap/                  # State S3 보호 설정·운영자 IAM
│   │   ├── templates/iam/operator/ # 운영자 권한 정책
│   │   └── tests/                  # bootstrap 모의 테스트
│   ├── monitoring/                 # 상시 Prometheus·Grafana 서버·EBS·접근 권한
│   ├── staging/                    # 시험할 때만 만드는 API·DB·Redis·ALB·Locust
│   ├── staging-control/            # 지속 CodeBuild 실행기·Scheduler 만료 정리
│   └── prod/                       # 운영 서비스 인프라
│       ├── network.tf              # VPC·서브넷·라우팅
│       ├── security.tf, access.tf  # 보안 그룹·관리 접속 Endpoint
│       ├── compute.tf              # API A/C EC2·초기 설치
│       ├── alb.tf, cloudfront.tf   # 요청 분산·HTTPS·이미지 조회
│       ├── database.tf, redis.tf   # RDS·Redis
│       ├── storage.tf              # 사용자 이미지·배포 파일 S3
│       ├── ecr.tf                  # 운영 컨테이너 이미지·publisher/pull 권한
│       ├── iam.tf, parameters.tf   # 서버·배포 역할·앱 설정 조회 권한
│       ├── monitoring.tf           # CloudWatch 로그 그룹·대시보드
│       ├── outputs.tf              # 배포·접속 출력값
│       ├── prod.tfvars.example     # 입력 예시
│       ├── templates/              # 초기 설치·IAM·모니터링 설정
│       └── tests/                  # 운영 구성 모의 테스트
├── observability/                  # Prometheus·Grafana·대시보드와 Stage 대상 설정
├── loadtest/                       # Locust master/worker·로그인·통계·UI/CLI 제어
├── scripts/                        # 검증·모니터 설치·Stage 생성/수집/삭제
└── docs/
    └── architecture/               # 운영 그림·편집 원본
```

| 저장소 | 관리 범위 |
| --- | --- |
| 인프라 | Terraform·IAM·서버 초기 설치·모니터링 설정 |
| 백엔드 | Java·Docker/Compose·앱 설정 조회·배포 스크립트·앱 CD |

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
uv pip install --python /tmp/getddo-infra-venv/bin/python PyYAML==6.0.3 boto3==1.40.59 Werkzeug==3.1.3

# Terraform·스크립트 검사
/tmp/getddo-infra-venv/bin/python scripts/validate.py

# 개별 검사
/tmp/getddo-infra-venv/bin/python scripts/validate.py terraform
/tmp/getddo-infra-venv/bin/python scripts/validate.py scripts
/tmp/getddo-infra-venv/bin/python scripts/validate.py documents
```

Terraform 검사는 backend를 제거한 임시 복사본과 mock AWS Provider를 사용하며 AWS·원격 State에 접근하지 않습니다. CI는 이 검사 외에도 Prometheus/Compose/대시보드 설정, 실제 Locust master/worker·Chromium UI 통합 시험과 Docker 이미지 빌드를 수행합니다. 전체 CI 절차와 추가 도구는 [검증 workflow](.github/workflows/validate.yml)를 따릅니다.

## 운영 시 참고

- [입력 예시](terraform/prod/prod.tfvars.example)를 참고하고 실제 tfvars·State·plan·env·자격 증명·개인 키는 Git에서 제외합니다.
- DB·Redis 비밀값은 ephemeral/write-only 입력으로 전달합니다.
- AWS 프로필 이름은 `getddo-dev`입니다. 작업 전 실제 계정과 역할을 확인합니다.
- 변경 전 State 잠금을 유지한 전체 plan에서 생성·교체·삭제 범위를 검토합니다.
- API의 시연용 사용자 선택 헤더는 사용자 인증 수단이 아닙니다.

## 관련 코드

- [운영 이미지 ECR·권한·배포 절차](docs/ecr-deployment.md)
- [운영자 IAM](terraform/bootstrap/iam.tf)
- [서버 접속](terraform/prod/access.tf)
- [이미지 저장소](terraform/prod/storage.tf) · [CloudFront](terraform/prod/cloudfront.tf)
- [Redis](terraform/prod/redis.tf)
- CloudFront API 요청은 IP별 최근 60초에 600회 기준으로 차단하고, 차단 로그는 7일 보관합니다. [구성·운영 절차](docs/waf-rate-limit.md) · [실제 차단·자동 해제·로그 검증 결과](docs/waf-rate-limit-test-results.md)

## 운영 Grafana

기존 HTTPS 주소의 `/grafana/`에서 기존 admin 계정으로 로그인한다. 비밀번호는 전용 SecureString에서 관리하고 Git에 넣지 않는다. [접속·적용·복구 절차](docs/grafana-https-access.md)를 참고한다.

## 임시 스테이징 부하 테스트

API a/c `t3.small`, 전용 RDS/Redis·내부 ALB와 Locust `t3.medium` 한 대(master+worker2개)를 시험할 때만 생성합니다. 기존 Prod·상시 Grafana는 유지합니다. 환경 생성·수집·삭제는 AWS 작업으로 실행하고, 부하는 Locust UI에서 제어합니다. 실행 중 서버에 SSH로 접속해 개인 Python 시나리오를 적용하고 시험 DB 데이터를 준비할 수 있습니다.

- **이미 서버를 사용하는 팀원:** [SSH 접속·개인 시나리오·더미 SQL·결과 수집](docs/staging-loadtest.md#이미-열린-환경을-사용하는-팀원)
- **환경 생성 담당자:** [AWS 인증·소스/이미지 준비·bundle/config·생성과 만료](docs/staging-loadtest.md#팀원이-처음-실행할-때)
- [Locust 입력값·반복 시험](docs/staging-loadtest.md#부하-테스트-사용법과-시나리오-관리) · [Grafana 지표](docs/staging-loadtest.md#grafana-지표) · [수집·삭제·복구](docs/staging-loadtest.md#수집과-삭제)
- [State·설정 계약](terraform/staging/README.md) · [Locust 런타임 계약](loadtest/CONTRACT.md)

### 생성·접속·종료의 구분

최대 수명은 요청 시점부터 **15~5760분(96시간)**이며 `--max-duration` 또는 시간대가 포함된 `--expires-at` 중 하나를 지정합니다. 특정 주말의 종료 날짜는 공통 기본값이 아닙니다. 현재 run ID·SSH 대상·만료 시각은 실행마다 조회해 공유합니다.

| 위치 | 사용하는 기능 |
| --- | --- |
| 생성 담당자 PC | `scripts/staging.py up/status/collect/destroy` — 고정 bundle/config 준비 후 AWS에 비동기 작업 요청 |
| SSH 서버 | `loadtest apply FILE`, `loadtest db`, `loadtest collect` — 개인 시나리오·데이터·시험 결과 관리 |
| [Locust 웹](https://dzc8oneey9mmx.cloudfront.net/locust/) | `locust-admin` 로그인 후 사용자·증가 속도·실행 시간 입력. 비밀번호는 서울 Parameter Store `/getddo/staging/locust/admin-password` |
| [Grafana 웹](https://dzc8oneey9mmx.cloudfront.net/grafana/) | 기존 계정으로 Staging 폴더의 발생기·업무 결과·API/JVM·호스트·AWS 지표 관찰 |

개인 시험은 Stop/시간 종료 후 SSH 서버의 `loadtest collect`로 보관합니다. 공유 환경을 종료하는 `destroy`는 모든 팀원 사용이 끝난 뒤 담당자가 요청합니다. 터미널 종료와 독립된 Scheduler가 만료 정리를 시작하며 실제 자원 삭제 완료에는 추가 시간이 걸립니다.

### 결과 보관과 검증 범위

- Locust CSV/HTML·개인 Python/SQL은 S3 저장 후 readback·해시 검증합니다. SQL 파일 보관은 DB 전체 백업이 아닙니다.
- Grafana S3 내보내기는 유효한 24시간 이내 등록을 지원합니다. 수명24시간 초과·등록 만료 후 수집은 모니터링 export를 `partial`로 표시하고 필수 Locust/소스 보관은 검증합니다. [제한과 사유](docs/staging-loadtest.md#grafana-이력과-수집-제한)를 확인합니다.
- 공용 Prometheus 이력은 14일 또는20GB 한도이며 먼저 도달하는 제한이 적용됩니다. S3 결과의 장기 보관 기간은 미정입니다.
- SSH·개인 시나리오·DB TLS/CRUD·소규모 부하·Grafana 대조·S3 재조회와 별도 환경의 TTL 자동 삭제·독립 잔여0을 실환경에서 확인했습니다. 이는 한계 성능·SLO 합격 검증이 아닙니다.
