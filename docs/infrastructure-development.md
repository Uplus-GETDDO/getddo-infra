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
uv pip install --python /tmp/getddo-infra-venv/bin/python PyYAML==6.0.3 boto3==1.40.59 Werkzeug==3.1.3
source /tmp/getddo-infra-venv/bin/activate

# Terraform·스크립트 검사
/tmp/getddo-infra-venv/bin/python scripts/validate.py

# 개별 검사
/tmp/getddo-infra-venv/bin/python scripts/validate.py terraform
/tmp/getddo-infra-venv/bin/python scripts/validate.py scripts
/tmp/getddo-infra-venv/bin/python scripts/validate.py documents
```

Terraform 검사는 backend를 제거한 임시 복사본과 mock AWS Provider를 사용하며 AWS·원격 State에 접근하지 않습니다. CI는 이 검사 외에도 Prometheus/Compose/대시보드 설정, 실제 Locust master/worker·Chromium UI 통합 시험과 Docker 이미지 빌드를 수행합니다. 전체 CI 절차와 추가 도구는 [검증 workflow](../.github/workflows/validate.yml)를 따릅니다.

## 운영 변경 시 확인

- [입력 예시](../terraform/prod/prod.tfvars.example)를 참고하고 실제 tfvars·State·plan·env·자격 증명·개인 키는 Git에서 제외합니다.
- DB·Redis 비밀값은 ephemeral/write-only 입력으로 전달합니다.
- AWS 프로필 이름은 `getddo-dev`입니다. 작업 전 실제 계정과 역할을 확인합니다.
- 변경 전 State 잠금을 유지한 전체 plan에서 생성·교체·삭제 범위를 검토합니다.
- API의 시연용 사용자 선택 헤더는 사용자 인증 수단이 아닙니다.
