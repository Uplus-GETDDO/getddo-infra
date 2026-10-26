# GETDDO 인프라

GETDDO의 AWS 인프라, 모니터링, 임시 부하 테스트 환경을 관리하는 Terraform 저장소입니다. 애플리케이션 코드와 컨테이너 배포 스크립트는 백엔드 저장소에서 관리합니다.

## 시스템 아키텍처

![GETDDO 시스템 아키텍처](docs/architecture/production.png)

[SVG](docs/architecture/production.svg) · [draw.io 편집 원본](docs/architecture/production.drawio)

| 영역 | 구성과 역할 |
| --- | --- |
| 프론트엔드 | Vercel · React · Vite |
| API 진입 | CloudFront · WAF → 내부 ALB, 공개 HTTPS와 요청 제한 |
| 애플리케이션 | 두 AZ의 EC2에서 Spring Boot·Docker 실행 |
| 데이터 | RDS MySQL Multi-AZ · ElastiCache Redis 복제본과 자동 장애 전환 |
| 저장소·설정 | S3 이미지·배포 파일, Parameter Store 앱 설정·비밀값 |
| 배포 | GitHub Actions OIDC · ECR 이미지 · SSM 순차 배포 |
| 모니터링 | Prometheus·Grafana 지표, CloudWatch 앱 로그·AWS 지표 |
| 부하 테스트 | 필요할 때 생성·삭제하는 스테이징과 Locust |

RDS 대기 DB와 Redis 복제본은 장애 대비용이며 두 API는 같은 주 DB·Redis에 연결합니다.

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

## 검증 기록

서버 접속 없이 읽을 수 있는 저장소 내 시험 기록입니다. 각 문서에 시험 조건·결과·한계를 정리했습니다.

| 항목 | 기록 |
| --- | --- |
| 이미지 배포·롤백 | [ECR 순차 배포와 실패 복구](docs/ecr-deployment-test-results.md) |
| 데이터베이스 | [RDS 장애 전환·재연결·데이터 보존](docs/rds-failover-test-results.md) |
| 공유 요청 제한 | [Redis 장애 전환과 A/C 공유 제한](docs/redis-failover-test-results.md) |
| 진입점 요청 제한 | [WAF 차단·자동 해제·로그 검증](docs/waf-rate-limit-test-results.md) |
| 모니터링 | [구축·지표 수집 검증](docs/monitoring-runtime-verification.md) · [대시보드 구성](docs/grafana-dashboard-guide.md) |

## 로컬 검증

Terraform 1.16.x, Python 3.12, Bash, Node.js를 사용합니다. [검증 환경 준비·Terraform 작업 안내](docs/infrastructure-development.md)에 따라 실행 환경을 준비한 뒤 저장소 루트에서 실행합니다.

```bash
python scripts/validate.py            # Terraform·스크립트 검사
python scripts/validate.py documents  # 문서 링크·회귀 검사
```

Terraform 검사는 mock AWS Provider를 사용하며 실제 AWS·원격 State에 접근하지 않습니다. 전체 CI 검사는 [검증 workflow](.github/workflows/validate.yml)에 정의되어 있습니다.

## 구성·운영 문서

| 작업 | 문서 |
| --- | --- |
| 앱 배포 | [ECR·SSM 배포](docs/ecr-deployment.md) |
| 장애 전환 | [RDS](docs/rds-multiaz.md) · [Redis](docs/redis-ha.md) |
| 요청 제한 | [WAF](docs/waf-rate-limit.md) |
| 모니터링 | [Grafana 구성·접속·복구](docs/grafana-https-access.md) |
| 부하 테스트 | [스테이징·Locust 사용과 결과 수집](docs/staging-loadtest.md) |
| 서버·DB 사양 | [사양 변경](docs/capacity-resize.md) · [DB 연결 수](docs/database-connections.md) |

접속 주소와 실시간 대시보드는 해당 서버·네트워크가 실행 중일 때만 유효합니다. 서버 종료 후에는 위 검증 기록과 저장소에 정의된 구성으로 구현 내용을 확인할 수 있습니다.
