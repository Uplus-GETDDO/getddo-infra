# GETDDO 인프라

GETDDO 운영 인프라를 관리하는 Terraform 저장소.
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
├── scripts/                        # 로컬·CI 검증
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
| `terraform/bootstrap` | `bootstrap/terraform.tfstate` | State S3·운영자 IAM |
| `terraform/prod` | `dev/terraform.tfstate` | 운영 네트워크·API·데이터 저장소·배포 IAM·CloudWatch |

기존 State 키와 AWS 자원 이름(`getddo-dev-*`), Parameter Store 경로, 로그 그룹 이름은 유지합니다. `dev` 표기는 기존 이름이며 별도 개발 환경은 없습니다.

## 로컬 검증

필수 도구: Terraform 1.16.x, Python 3, Bash. AWS Provider 버전은 `.terraform.lock.hcl`에 고정합니다.
저장소 루트에서 실행합니다.

```bash
# 전체 검사: CI와 동일
python3 scripts/validate.py

# 개별 검사
python3 scripts/validate.py terraform
python3 scripts/validate.py scripts

# 문서 링크·회귀 테스트 (Node.js 필요)
python3 scripts/validate.py documents
```

CI는 Terraform과 스크립트·설정 검사를 두 job으로 실행합니다. Terraform 검사는 backend를 제거한 임시 복사본과 mock AWS Provider를 사용하며, AWS·원격 State 접근 없이 실행됩니다.

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

[실행·로그인·Grafana 지표·결과 수집·삭제](docs/staging-loadtest.md)와 [AWS lifecycle 계약](terraform/staging/README.md)을 참고합니다. 운영과 분리한 임시 환경이며, 부하는 준비 완료 뒤 UI/스크립트에서 시작합니다. 실제 AWS 인수 결과는 해당 PR에 기록합니다.
