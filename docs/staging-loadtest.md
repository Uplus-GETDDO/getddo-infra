# 임시 스테이징 부하 테스트

환경 생성은 AWS의 CodeBuild에서, 최대 수명 정리는 독립 Scheduler에서 실행한다. 로컬 터미널을 닫아도 실행과 정리가 계속된다. 생성 완료만으로 부하를 시작하지 않는다. 환경과 역할의 상세 계약은 [Stage lifecycle](../terraform/staging/README.md)을 따른다.

이 문서는 팀원 실행·복구 절차입니다. 2026-10-23 고정 소스·이미지의 UI/CLI 소규모 부하·결과 재조회·삭제 검증은 완료했으며, 새 코드의 성능이나 전체 TTL 삭제 E2E 검증을 뜻하지 않습니다.

## 팀원이 처음 실행할 때

환경 생성·삭제는 본인 PC에서 AWS에 요청하고, 실제 서버 준비는 AWS CodeBuild에서 실행합니다. 부하 실행과 관측은 브라우저의 Locust·Grafana를 사용합니다. 현재 환경 생성 전용 웹 화면은 제공하지 않습니다.

### 1. 최초 준비와 권한

- 실행 터미널: Linux 또는 Windows의 WSL2/Bash. 현재 실행기는 `pwd` 등 POSIX 모듈을 사용하므로 Windows PowerShell에서 Python을 직접 실행하는 방식은 지원하지 않습니다. 다른 운영체제에서의 실행은 별도 검증하지 않았습니다. WSL은 요청을 보내는 도구이며 테스트 서버는 AWS에 생성됩니다.
- 설치: Git, GitHub CLI(`gh`), Python 3.12, AWS CLI(`aws login` 지원 버전). 아래 명령에는 `python3.12`를 사용합니다. 로컬 Terraform·Docker는 아래 bundle 생성·제출에는 필요하지 않으며 AWS 실행기가 사용합니다.
- GitHub: 본인 계정으로 `gh auth login`을 실행하고 비공개 Infra와 BE 저장소의 읽기·Actions 산출물 다운로드 권한을 확인합니다.
- AWS: 계정 `942466928325`의 본인 IAM 사용자로 로그인하고 Stage 실행 권한을 받아야 합니다. **팀원에게 이미 권한이 부여됐다고 가정하지 않습니다.** 저장소의 자동 그룹 연결은 현재 `gyeongju`에만 정의돼 있습니다. 관리자가 본인에게 실행 제어·S3 bundle/manifest·자격 설정 메타데이터·Scheduler/PassRole 권한과 Locust 비밀번호 조회 권한을 확인해야 합니다. 기존 정책은 `terraform/bootstrap/staging-control.tf`, `terraform/bootstrap/templates/iam/staging/`에서 확인합니다. 관리자/root 로그인을 매 실행에 사용하거나 다른 사람의 로그인 캐시를 공유하지 않습니다.

다음 예시의 `본인_IAM_사용자명`만 본인 계정명으로 바꿉니다. 프로필은 자신의 PC에서 한 번 설정합니다. 고정 boto3 버전은 AWS CLI의 로그인 세션을 `credential_process`로 사용합니다.

```bash
aws configure set login_session arn:aws:iam::942466928325:user/본인_IAM_사용자명 --profile getddo-cli-login
aws configure set region ap-northeast-2 --profile getddo-cli-login
aws configure set credential_process 'aws configure export-credentials --profile getddo-cli-login --format process' --profile getddo-dev
aws configure set region ap-northeast-2 --profile getddo-dev
aws login --profile getddo-cli-login --region ap-northeast-2 --remote
aws sts get-caller-identity --profile getddo-dev
```

마지막 결과의 계정·사용자를 확인합니다. 세션 만료 시 login만 다시 실행합니다. STS 성공은 모든 Stage 권한이 있다는 뜻은 아닙니다.

### 2. 실행할 코드와 CI 이미지 준비

작업 중인 checkout을 바꾸지 않도록 새 작업 디렉터리에서 실행합니다. `gh` 인증을 Git에서도 사용하도록 설정하고 두 저장소를 받습니다.

```bash
gh auth setup-git
mkdir getddo-stage-run
cd getddo-stage-run
git clone https://github.com/Uplus-GETDDO/getddo-infra.git
git clone --no-checkout https://github.com/Uplus-GETDDO/getddo-be.git
cd getddo-infra
python3.12 -m venv .venv
.venv/bin/python -m pip install boto3==1.40.59
export AWS_PROFILE=getddo-dev
mkdir -p .stage/image
```

BE의 같은 저장소 PR에서 **빌드·테스트와 Docker 이미지 작업이 성공한 실행**을 선택합니다. GitHub Actions 실행 화면에서 실행 ID와 재시도 번호를 확인해 다음 세 값을 입력합니다. 첫 실행의 재시도 번호는 `1`입니다. `dev` push는 Stage 이미지 산출물을 게시하지 않으며, 산출물은 7일 보관되므로 만료됐다면 해당 PR의 CI를 다시 실행해야 합니다. 이미 병합된 PR의 남아 있는 산출물도 동일한 고정 SHA로 검증합니다.

```bash
PR_NUMBER='테스트할_BE_PR_번호'
CI_RUN_ID='성공한_Actions_실행_ID'
CI_ATTEMPT='1'
export INFRA_SHA="$(git rev-parse HEAD)"
BACKEND_SHA="$(gh pr view "$PR_NUMBER" --repo Uplus-GETDDO/getddo-be --json headRefOid --jq .headRefOid)"
git -C ../getddo-be checkout --detach "$BACKEND_SHA"
gh run download "$CI_RUN_ID" --repo Uplus-GETDDO/getddo-be \
  --name "staging-image-${BACKEND_SHA}-${CI_ATTEMPT}" --dir .stage/image
.venv/bin/python scripts/build-staging-bundle.py \
  --infra-sha "$INFRA_SHA" \
  --backend ../getddo-be --backend-sha "$BACKEND_SHA" \
  --artifact-dir .stage/image --output .stage/source.tar.gz
```

bundle에는 Infra·BE 소스와 CI 이미지가 들어갑니다. 빌더가 checkout SHA·이미지 revision·해시를 검증하며, 기존 출력 파일 덮어쓰기는 거부합니다. 재시험용 bundle을 만들 때는 새 작업 디렉터리 또는 다른 출력 파일명을 사용합니다. 변경된 PR head와 이전 CI 이미지를 섞지 않습니다.

설정 파일을 생성합니다. 아래 AMI·보안 그룹·주소는 이 계정의 기존 구성 기준입니다. AMI가 더 이상 available 상태가 아니거나 x86_64 Amazon Linux 2023 이미지가 아니면 검토한 이미지로 바꾼 뒤 진행합니다. 비밀번호는 넣지 않습니다.

```bash
aws ec2 describe-images --region ap-northeast-2 --image-ids ami-0870825cefcaafcc8 \
  --query 'Images[].{Id:ImageId,State:State,Architecture:Architecture,Name:Name}'
.venv/bin/python - <<'PY'
import json
import os
from pathlib import Path
config = {
    "source_sha": os.environ["INFRA_SHA"],
    "public_origin": "https://dzc8oneey9mmx.cloudfront.net",
    "terraform": {
        "ami_id": "ami-0870825cefcaafcc8",
        "monitoring_security_group_id": "sg-04954d4932de5fa69",
        "shared_alb_security_group_id": "sg-09e12310ecd082ffb"
    },
    "hooks": {name: f"scripts/staging_hooks/{name}.py"
              for name in ("prepare", "collect", "detach")}
}
Path(".stage/config.json").write_text(json.dumps(config, indent=2) + "\n")
PY
```

`.venv/`·`.stage/`는 로컬 작업용이며 Git에 추가하지 않습니다. 준비 담당자가 한 번 만들고 다른 실행자에게 접근이 제한된 경로로 bundle·대응 config·빌더 출력의 SHA256을 전달해도 됩니다. 받는 사람도 본인 AWS 인증을 사용하고 SHA256과 검증 대상 버전을 확인합니다. 개인 `/tmp`나 로그인 캐시는 공유하지 않습니다.

### 3. 매번 실행: 생성 → 브라우저 시험 → 삭제

위 작업 디렉터리에서 실행합니다. `stage up`이라는 설치된 명령은 없습니다. 아래는 저장소의 Python 스크립트를 직접 실행하는 명령입니다.

```bash
export AWS_PROFILE=getddo-dev
.venv/bin/python scripts/staging.py up --max-duration 120 \
  --bundle .stage/source.tar.gz --config .stage/config.json
RUN_ID='up이 반환한 run_id'
.venv/bin/python scripts/staging.py status --run-id "$RUN_ID"
```

- `up`은 환경 생성 요청입니다. 120분은 요청 시점부터의 최대 수명이며 준비 완료 후 시험 시간이 아닙니다. `run_id`는 이번 환경의 식별자입니다. 다른 터미널에서도 같은 값을 사용합니다.
- `up` 반환은 생성 완료가 아닙니다. status를 반복 조회해 `phase`가 `ready`가 된 뒤 시험합니다. `build_id`는 AWS CodeBuild 진행 확인에 사용합니다. 반환 후 터미널을 닫아도 생성·만료 정리는 계속됩니다.
- Locust: `https://dzc8oneey9mmx.cloudfront.net/locust/`, 아이디 `locust-admin`. 비밀번호는 AWS 서울 리전의 Parameter Store `/getddo/staging/locust/admin-password`에서 본인 권한으로 복호화해 확인합니다.
- 처음에는 Locust UI에서 사용자 수 `2`, 초당 생성 `1`, 실행 시간 `30s`로 시작합니다. 초기 시나리오는 이벤트 목록 조회 `smoke_events`입니다. 업무 시나리오는 시험 전에 별도로 준비합니다.
- Grafana: `https://dzc8oneey9mmx.cloudfront.net/grafana/` → 기존 계정 로그인 → `Staging` 폴더 → 최근 15분. 요청 수·RPS·실패 수·p95 지연·worker 2개·API a/c 처리량부터 확인합니다.

종료 후 결과 수집을 요청하고, 해당 CodeBuild 성공과 status의 `phase: collected`·결과 갱신을 확인한 뒤 삭제합니다. 각 명령은 비동기 요청이므로 바로 이어서 실행하지 않고 해당 작업의 완료를 기다립니다.

```bash
.venv/bin/python scripts/staging.py collect --run-id "$RUN_ID"
.venv/bin/python scripts/staging.py status --run-id "$RUN_ID"
# collect의 CodeBuild 성공과 결과 보관을 확인한 뒤 실행
.venv/bin/python scripts/staging.py destroy --run-id "$RUN_ID"
.venv/bin/python scripts/staging.py status --run-id "$RUN_ID"
```

마지막에 `phase: destroyed`까지 확인합니다. Locust Stop은 부하만 멈추고 AWS 자원은 삭제하지 않습니다. `destroy`는 결과 보관을 검증한 뒤 Stage 서버·DB 등을 삭제하며 운영 환경·Grafana·S3 결과는 보존합니다. 결과는 `staging/runs/RUN_ID/results/`에 저장되며 색인은 status의 `results`로 확인합니다.

환경은 팀 공용으로 동시에 1개만 사용합니다. 다른 팀원이 사용 중이면 기다리며 기존 잠금이나 다른 사람의 run을 임의로 삭제하지 않습니다. `AccessDenied`는 본인 권한 확인, 생성·삭제 실패는 status와 CodeBuild 로그 확인부터 시작합니다. 실패한 채 새 up을 반복하지 말고 아래 복구 절차를 따릅니다.

<a id="준비와-실행"></a>

## 실행 계약과 복구 참고

| 명령·파일 | 의미 |
| --- | --- |
| `up` | 고정 bundle·config를 전달하고 AWS 환경 생성 요청 |
| `status` | 지정한 run의 manifest·단계·결과 색인 조회 |
| `collect` | 부하 중지·결과 보관·재조회 검증 요청. 완료 시 `collected` |
| `destroy` | 결과 보관 검증 후 임시 자원 삭제 요청. 실제 잔여 0 확인 후 `destroyed` |
| bundle | Infra·BE 소스와 고정 CI 이미지를 묶은 파일. 비밀번호를 넣지 않음 |
| config | 같은 Infra SHA·AMI·공유 보안 그룹·CloudFront·hook 경로를 지정하는 비밀 아닌 설정 |

최대 수명은 15~1440분 중 필수 지정합니다. 상세 소유권·설정 계약은 [State·설정 예시](../terraform/staging/README.md#submission-and-recovery)를 따릅니다. `ready`에는 앱 두 대 배포, 전용 DB·Redis, 테스트용 USER 준비, Locust master·worker 2개의 대기 상태, HTTPS health와 모니터 등록 확인이 포함됩니다. 실제 업무 부하는 별도로 시작합니다.

CLI 로그인 세션이 유효하면 기존 본인 세션을 재사용합니다. `credential_process` 출력은 SDK가 파이프로만 읽으므로 직접 출력·저장하지 않습니다. CodeBuild와 EC2는 자체 역할을 사용합니다. 생성·삭제 실패 복구는 아래 [수집과 삭제](#수집과-삭제)를 따릅니다.

## 로그인과 같은 master 제어

- 주소: `https://dzc8oneey9mmx.cloudfront.net/locust/`
- 아이디: `locust-admin`
- 비밀번호: Parameter Store `/getddo/staging/locust/admin-password`의 SecureString. 서울 리전 콘솔에서 본인 권한으로 확인한다. 문서·PR·터미널 인자에 값을 복사하지 않는다.
- 계정명·세션 비밀 경로: `/getddo/staging/locust/admin-user`, `/getddo/staging/locust/session-secret`.

위 세 파라미터는 최초 생성한 영구 설정이다. up·destroy·rollback·TTL에서 회전하거나 삭제하지 않는다. EC2가 역할로 값을 읽고 비밀번호는 해시, 세션 비밀은 root 전용 `/run` 파일로만 전달한다. worker에는 로그인 비밀을 마운트하지 않는다.

웹 UI와 `loadtest/control.py`는 같은 master의 인증·CSRF 보호 HTTP API를 사용한다. 컨트롤러 인자의 사용자/비밀번호는 값이 아닌 실행 사용자 소유 0600 파일 경로다. 외부 CLI는 본인만 읽는 임시 경로를 사용하고, EC2 내부 수집은 비밀번호를 stdin으로만 전달한다. 사용 후 인증 정보는 보관하지 않는다.

```sh
python3 loadtest/control.py --origin https://dzc8oneey9mmx.cloudfront.net \
  --username-file /run/private/username --password-file /run/private/password \
  run --users 2 --spawn-rate 1 --duration 30
```

`stop`, `collect --output DIR`도 같은 전역 인증 인자를 쓴다. 부하 대상은 생성된 전용 internal Stage ALB로 고정하며 UI의 임의 host·리다이렉트로 변경할 수 없다. 전체 성능 시나리오는 시험 전에 작성한다. 기본 `smoke_events`는 합성 USER로 `GET /api/v1/events`를 조회하고 HTTP 상태뿐 아니라 ResponseEnvelope와 페이지 형태를 검사하는 플랫폼 확인용이다.

## Grafana 지표

기존 Prod 대시보드·UID·데이터 소스·시계열을 유지하고 별도 `Staging` 폴더를 추가한다. 같은 Prometheus에 Stage API·node·Locust job을 별도로 등록하며 임시 대상만 삭제한다.

| 화면 | 지표와 해석 |
| --- | --- |
| Staging load test | 사용자 수, master rolling RPS, 전체 요청·실패 수, 1분 실패율, rolling/cumulative p50·p95·p99(초) |
| Locust 결과·건전성 | 업무 결과별 수·초당 횟수, 429/서버 오류/응답 계약 오류/전송 오류, master scrape, expected·connected·fresh·healthy worker, 최근 요청·worker 데이터 신선도 |
| API/JVM | a/c 요청 수·RPS, 성공/오류 지연 p95·p99, 5xx, heap·GC, DB pool active/pending/idle/max, Tomcat busy/current/max |
| 서버 | API a/c·Locust CPU, 사용·가용 메모리, 파일시스템 여유, exporter 수집 상태 |
| Staging AWS resources | 실제 Stage RDS·Redis·ALB·TG·EC2 차원의 CPU·메모리·연결·디스크·IOPS·I/O 지연·큐, Redis eviction·network, ALB 5xx·연결 오류·지연·healthy targets, CPU credits |

Prometheus는 Stage를 5초마다 수집한다. 짧은 부하의 rate는 1분 창을 사용한다. Locust percentile은 master가 전체 요청에서 계산한 값이며 worker percentile 평균을 사용하지 않는다. 수집 누락·오래된 데이터·요청 없음은 정상 0으로 치환하지 않는다. AWS 지표는 원래 CloudWatch 해상도를 따른다. CPU credits는 5분이며 새 자원의 지표 도착 지연은 Prometheus 수집 주기와 별개다.

Monitor는 고정 S3 데이터 요청만 읽고 실제 EC2의 계정·VPC·이름·Environment·RunId·주소를 검증한다. Stage runner에 기존 monitor의 임의 SSM 명령 실행 권한을 주지 않는다. Stage 생성 시 monitor→Stage 목적지 포트만 임시 허용한다.

## 수집과 삭제

```sh
AWS_PROFILE=getddo-dev python3 scripts/staging.py collect --run-id RUN_ID
AWS_PROFILE=getddo-dev python3 scripts/staging.py destroy --run-id RUN_ID
AWS_PROFILE=getddo-dev python3 scripts/staging.py status --run-id RUN_ID
```

수집은 부하를 멈춘 후 Locust CSV·전체 이력·실패/예외 수·HTML·메타데이터와 Grafana Stage 쿼리·시계열을 S3에 보관한다. 결과 색인은 Infra SHA·백엔드 SHA·이미지 ID·archive digest와 두 보고서의 객체 키·SHA256을 포함한다. 모든 결과를 다시 읽어 해시가 일치해야 성공이다. 로그인 비밀번호·쿠키·세션 비밀·앱 비밀은 결과에 넣지 않는다. 보관 기간은 아직 정하지 않았으며 자동 만료를 설정하지 않는다.

정상 destroy에서 결과 보관이 실패하면 삭제를 멈추고 실패를 보고한다. 최대 수명·생성 실패 정리에서는 수집을 최대 6분 시도한 뒤 임시 자원 삭제를 우선한다. monitor 대상 해제 후 Terraform destroy를 실행하고 AWS 서비스별 잔여 자원을 확인한다. 자원이 남거나 소유권이 맞지 않으면 성공으로 표시하거나 singleton 잠금을 해제하지 않는다.

중단된 생성은 CodeBuild의 `buildComplete`까지 확인한 뒤 최신 실행 기록을 다시 읽는다. Terraform 실행 전에 기록한 build ID·사용자/호스트·State 경로·작업 종류·시간이 잠금과 일치하고 해당 실행이 종료된 경우에만 기존 잠금 객체를 ETag 조건으로 제거한다. 살아 있거나 소유권을 입증하지 못한 잠금은 제거하지 않는다.

Terraform에 기록되지 않은 부분 생성 자원은 실제 `Environment=staging`과 해당 `RunId` 태그를 모두 확인한 후 제한 시간 내 서비스별로 삭제한다. Terraform 실패 후 이 경로를 사용한 경우 State를 다시 정상 destroy로 정리해야 하며, 다른 실행·공유 자원·지속 설정의 소유권이 불명확하면 중단한다.

| 삭제 | 유지 |
| --- | --- |
| Stage EC2·EBS·전용 ALB/TG·listener rule·RDS/Redis·전용 subnet group/SG·추가 egress rule·workload 역할·앱 파라미터·임시 API log group | Prod 자원·기존 monitor/Grafana와 이력·검증한 S3 결과/산출물·영구 Locust 로그인 3개·persistent CodeBuild/Scheduler 제어 설정 |

`destroyed`는 Terraform 종료만을 뜻하지 않는다. 실제 잔여 자원 0과 영구 로그인 설정 보존 확인이 필요하다. `/locust/`는 임시 listener rule이 없어지면 사용 불가하며 기존 `/grafana/`는 계속 유지한다.

## 최종 인수 기준

리뷰 반영한 최종 두 PR SHA로 환경을 만들고, UI와 CLI에서 작은 부하(각 사용자 2명·초당 생성 1명·30초)를 명시적으로 실행한다. 두 worker, API a/c 처리, 업무 응답과 Grafana 수신을 대조하고 결과 재조회·삭제·잔여 0·Prod/monitor/로그인 보존까지 확인한다. 이는 한계 성능이나 아직 정하지 않은 SLO 합격 기준을 확정하는 시험이 아니다. 코드 수정이 필요하면 수정 SHA에서 다시 검증한다. 팀원 승인 1명이 필요한 PR은 열린 상태로 남기며 자동 병합하지 않는다.
