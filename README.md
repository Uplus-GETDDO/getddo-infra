# GETDDO 인프라

GETDDO 운영 인프라·공용 모니터링·임시 스테이징 부하 테스트 환경을 관리하는 Terraform 저장소.
서울 리전(`ap-northeast-2`)에서 CloudFront·ALB·EC2·RDS·Redis·S3를 사용합니다.

## 운영 아키텍처

![GETDDO 운영 아키텍처](docs/architecture/production.svg)

[크게 보기 · 이미지 내보내기](docs/architecture/production.html) · [편집 원본](docs/architecture/production.architecture.json)

| 구성 | 역할 |
| --- | --- |
| CloudFront | 공개 HTTPS 진입점. API는 캐시 없이 내부 ALB로 전달하고, `/images/*`는 OAC로 비공개 S3에서 조회 |
| 내부 ALB | 두 AZ의 사설 서브넷에 배치. HTTP 80 → API 서버 8080으로 요청 분산 |
| API A / C | `ap-northeast-2a` / `ap-northeast-2c`의 `t3.small` EC2. Docker 앱 실행, 공인 IPv4·IGW로 외부 통신, 보안 그룹으로 인바운드 제한 |
| RDS | 비공개 MySQL 8.4, `db.t4g.micro`, Single-AZ. API 보안 그룹에서만 3306 접속 허용 |
| Redis | 비공개 Redis OSS 단일 노드. TLS·AUTH 연결, 공유 요청 제한 저장소 |
| S3 | 이미지·배포 파일 저장소 분리. API 역할로 이미지 업로드, CloudFront OAC로 조회 |
| Parameter Store | 앱 설정·비밀값 저장. 서버 역할로 조회 |
| GitHub Actions · SSM | 백엔드 빌드·배포. OIDC 임시 권한, S3 파일 전달, SSM Run Command로 서버 배포 |
| CloudWatch | API·ALB·RDS·Redis 지표, 앱 로그, 대시보드 |

- 실선: 서비스 요청 / 점선: 설정·배포·모니터링
- 외부 HTTPS는 CloudFront에서 종료. ALB·API 구간은 사설망 HTTP
- DB 관리 접속은 EC2 Instance Connect Endpoint·SSH 터널로 API 서버 경유
- RDS는 Single-AZ, Redis는 복제본·자동 장애 전환 없는 단일 노드

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
│       ├── storage.tf              # 이미지·배포 파일 S3
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

- [운영자 IAM](terraform/bootstrap/iam.tf)
- [서버 접속](terraform/prod/access.tf)
- [이미지 저장소](terraform/prod/storage.tf) · [CloudFront](terraform/prod/cloudfront.tf)
- [Redis](terraform/prod/redis.tf)

## 운영 Grafana

기존 HTTPS 주소의 `/grafana/`에서 기존 admin 계정으로 로그인한다. 비밀번호는 전용 SecureString에서 관리하고 Git에 넣지 않는다. [접속·적용·복구 절차](docs/grafana-https-access.md)를 참고한다.

## 임시 스테이징 부하 테스트

API a/c `t3.small`, 전용 RDS/Redis·내부 ALB, Locust `t3.medium` 한 대(master+worker 2개)를 시험할 때만 생성합니다. 기존 Prod와 상시 Grafana는 유지합니다. 환경 생성·결과 보관·삭제는 스크립트, 실제 부하 시작·중지는 Locust UI 또는 CLI가 맡습니다.

- **처음 실행하는 팀원:** [최초 권한·도구 준비 → CI 이미지/bundle 생성 → 환경 생성·시험·삭제](docs/staging-loadtest.md#팀원이-처음-실행할-때)
- [State·실행 소유권·정리 계약과 config 예시](terraform/staging/README.md)
- [Locust UI/CLI·통계와 검증](loadtest/README.md)
- [백엔드 Stage 이미지·배포 경로](https://github.com/Uplus-GETDDO/getddo-be/blob/dev/docs/06-runbook/staging-deployment.md)

### 생성·조회·결과 수집·삭제

앞의 Python 환경, 유효한 `getddo-dev` 인증, 검증된 source bundle과 비밀값 없는 config를 먼저 준비합니다. `SOURCE.tar.gz`·`CONFIG.json`은 위 실행 절차에 따라 준비한 실제 경로로 바꾸고, `RUN_ID`는 up이 반환한 이번 실행 ID로 바꿉니다.

```bash
AWS_PROFILE=getddo-dev /tmp/getddo-infra-venv/bin/python scripts/staging.py up \
  --max-duration 120 --bundle SOURCE.tar.gz --config CONFIG.json
AWS_PROFILE=getddo-dev /tmp/getddo-infra-venv/bin/python scripts/staging.py status --run-id RUN_ID
# ready 확인 후 UI/CLI에서 부하 시작
AWS_PROFILE=getddo-dev /tmp/getddo-infra-venv/bin/python scripts/staging.py collect --run-id RUN_ID
AWS_PROFILE=getddo-dev /tmp/getddo-infra-venv/bin/python scripts/staging.py destroy --run-id RUN_ID
AWS_PROFILE=getddo-dev /tmp/getddo-infra-venv/bin/python scripts/staging.py status --run-id RUN_ID
```

최대 수명은 15~1440분에서 필수 입력하며, 120분은 예시입니다. 생성 요청부터 시간을 계산하고 만료 시 정리를 시작합니다. up의 `run_id/build_id/expires_at` 반환 후에는 터미널을 닫아도 AWS 실행이 계속됩니다. CodeBuild 성공과 manifest의 `ready`를 확인한 뒤 부하를 시작하며 up이 자동으로 부하를 주지는 않습니다. 고정 SDK와 AWS CLI 로그인 연결은 [본인 AWS 인증 설정](docs/staging-loadtest.md#1-최초-준비와-권한)를 따릅니다.

### 접속·관측

| 항목 | 사용 방법 |
| --- | --- |
| Locust | [HTTPS /locust/](https://dzc8oneey9mmx.cloudfront.net/locust/) — ready 동안 사용. 아이디 `locust-admin`, 비밀번호는 서울 Parameter Store `/getddo/staging/locust/admin-password` SecureString에서 본인 권한으로 조회 |
| 부하 제어 | UI 또는 `loadtest/control.py`의 run/stop/collect가 같은 master를 사용. CLI 인증 파일은 실행 사용자 소유 0600 파일이며 원문을 인자로 넣지 않음 |
| Grafana | [기존 HTTPS /grafana/](https://dzc8oneey9mmx.cloudfront.net/grafana/)의 Staging 폴더. 기존 Grafana 계정·비밀번호를 사용 |
| 관측 지표 | Locust 사용자·RPS·p50/p95/p99·실패·업무 결과·worker, API/JVM·호스트, RDS/Redis/ALB·CPU credits. [지표별 해석](docs/staging-loadtest.md#grafana-지표) 참고 |

UI Stop·CLI stop은 부하만 중지합니다. **서버·DB까지 삭제하려면 `scripts/staging.py destroy`를 실행해야 합니다.** 시험을 반복하기 전 collect로 이전 결과를 보관합니다. 삭제 후 `/locust/`를 사용할 수 없는 것은 정상이며 기존 `/grafana/`는 유지됩니다.

### 보관·정리와 확인한 범위

- 결과는 비공개 배포 버킷의 `staging/runs/RUN_ID/results/`에 보관합니다. manifest의 `results`가 색인 key/SHA256을 제공하고, Locust CSV/HTML과 Grafana 쿼리·시계열의 key/해시를 연결합니다. 정상 destroy는 보관·재조회 검증 실패 시 삭제를 중단합니다.
- 생성 실패/TTL은 수집을 최대 360초 시도한 뒤 임시 자원 삭제를 우선합니다. 실제 잔여 자원이 없고 영구 로그인 설정이 보존돼야 소유 잠금을 해제합니다.
- 삭제 대상은 Stage EC2/EBS·전용 DB/Redis·ALB/TG·임시 rule/SG/IAM·앱 설정입니다. Prod·공유 네트워크·monitor/대시보드·S3 결과·Locust 로그인 3개·지속 제어 기반·State/manifest는 보존합니다.
- Prometheus는 14일/20GB 한도이며 S3 결과 보관 기간은 별도 미정입니다. 임의 S3 자동 만료를 추가하지 않았습니다.
- 2026-10-23 기능 인수: UI 41건·CLI 39건 모두 실패 0, Grafana 대조·S3 재조회·정상 destroy·독립 잔여 0 확인. 생성 실패 rollback도 실제 검증했습니다. **만료 시각의 전체 workload 삭제 E2E는 미수행**이며 Scheduler 전달 계약·TTL 모의 검증과 구분합니다. 성능 한계/SLO 시험은 아닙니다.
