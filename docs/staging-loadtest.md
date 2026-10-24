# 임시 스테이징 부하 테스트

이 문서는 반복해서 사용할 환경 생성·팀원 시험·결과 보관·삭제 절차입니다. 특정 실행의 run ID·인스턴스 ID·종료 날짜는 고정하지 않습니다. 실행 담당자가 생성 결과와 현재 상태에서 확인해 공유합니다. 개인 시나리오·DB 터미널 기능은 공통 기능이며, 며칠 동안 서버를 유지할지는 매 실행의 만료 설정으로 정합니다.

## 이미 열린 환경을 사용하는 팀원

팀 공용 환경은 한 번에 하나만 사용합니다. 시작 전에 현재 시험 담당자와 사용 시간을 맞춥니다. 이미 서버가 있으면 새 `up`을 요청하지 않습니다.

| 필요한 정보 | 확인 방법 |
| --- | --- |
| 현재 run ID·만료 시각 | 실행 담당자가 `up` 결과와 `status`의 `run_id`, `expires_at` 공유 |
| SSH 대상 | `status`의 `deployment.locust_instance` — Linux 사용자 `ec2-user` |
| AWS 계정 | 현재 팀 구성은 IAM `gyeongju`, 로컬 실행 프로필 `getddo-dev`. 다른 계정을 쓰려면 별도 권한 확인 |
| 개인 키 | 기존 `getddo-dev-api.pem`을 본인만 읽는 경로에 보관. Git·문서·채팅에 키 원문을 넣지 않음 |
| Locust | <https://dzc8oneey9mmx.cloudfront.net/locust/> — `locust-admin` |
| Grafana | <https://dzc8oneey9mmx.cloudfront.net/grafana/> — 기존 `admin` 계정 |
| 비밀번호 | 서울 Parameter Store의 `/getddo/staging/locust/admin-password`, `/getddo/monitoring/grafana/admin-password`를 본인 권한으로 조회 |

평소 팀원 사용에는 AWS CLI와 SSH 클라이언트만 필요합니다. 로컬 Python·Terraform은 필요하지 않습니다. Linux/WSL SSH 접속을 실환경에서 검증했습니다. Windows에서도 EICE 로컬 터널과 SSH 클라이언트를 사용할 수 있습니다. Linux가 필요한 환경 생성 스크립트와 구분합니다.

### AWS 인증과 SSH 접속

내 PC에서 인증 계정을 확인합니다. 기대 계정은 `942466928325`, 현재 팀 사용자 ARN은 `arn:aws:iam::942466928325:user/gyeongju`입니다. 프로필 최초 설정·만료 갱신은 [AWS 인증 설정](#aws-인증-설정)을 따릅니다.

```bash
aws sts get-caller-identity --profile getddo-dev --region ap-northeast-2
```

Linux/WSL에서는 PEM을 홈 디렉터리로 복사해 권한을 제한합니다. `KEY_SOURCE`는 자신의 실제 개인 키 경로로 바꿉니다.

```bash
KEY_SOURCE='/path/to/getddo-dev-api.pem'
mkdir -p ~/.ssh
chmod 700 ~/.ssh
install -m 600 "$KEY_SOURCE" ~/.ssh/getddo-dev-api.pem
```

다음 세 값은 현재 실행 정보로 설정합니다. `STAGE_IP`는 `deployment.locust_instance.private_ip`를 사용합니다. 현재 Terraform은 `10.20.0.250`을 지정하지만 재사용 전 상태를 대조합니다. `EICE_ID`는 실행 담당자가 확인한 기존 EC2 Instance Connect Endpoint ID입니다. 이 명령은 내 PC에서 실행합니다.

```bash
RUN_ID='현재_run_id'
STAGE_IP='현재_Locust_사설_IP'
EICE_ID='현재_Endpoint_ID'
ssh -i ~/.ssh/getddo-dev-api.pem \
  -o "HostKeyAlias=getddo-stage-$RUN_ID" \
  -o "ProxyCommand=aws ec2-instance-connect open-tunnel --profile getddo-dev --region ap-northeast-2 --instance-connect-endpoint-id $EICE_ID --private-ip-address %h --remote-port %p" \
  "ec2-user@$STAGE_IP"
```

새 run마다 호스트 키가 달라질 수 있으므로 run별 별칭을 사용합니다. 처음 접속할 때 실행 담당자가 확인한 호스트 지문과 대조합니다. 호스트 키 검증을 끄지 않습니다. AWS `gyeongju`와 SSH `ec2-user`는 서로 다른 계정입니다.

Windows PowerShell은 두 창을 사용합니다. 창 1에서 실제 주소·Endpoint ID를 넣어 터널을 열고 유지합니다.

```powershell
aws ec2-instance-connect open-tunnel --profile getddo-dev --region ap-northeast-2 --instance-connect-endpoint-id ENDPOINT_ID --private-ip-address STAGE_PRIVATE_IP --remote-port 22 --local-port 2222
```

창 2에서 실제 PEM 경로와 현재 run ID를 넣어 접속합니다. 개인 키 ACL은 본인만 읽도록 제한합니다. 이 Windows 절차는 제공용이며 실환경 접속 검증은 Linux/WSL에서 수행했습니다.

```powershell
ssh -i "C:\path\getddo-dev-api.pem" -p 2222 -o HostKeyAlias=getddo-stage-CURRENT_RUN_ID ec2-user@localhost
```

### 개인 시나리오 작성과 적용

아래 명령부터는 **SSH 서버 셸**에서 실행합니다. 쓰기 가능한 공용 `gyeongju` 디렉터리 안에 자신의 시험 폴더를 만듭니다. `my-test`는 다른 팀원과 겹치지 않는 이름으로 바꿉니다.

```bash
cd /opt/getddo-loadtest/scenarios/gyeongju
mkdir -p my-test
cp events.py my-test/events.py
cp seed.sql my-test/seed.sql
nano my-test/events.py
loadtest apply /opt/getddo-loadtest/scenarios/gyeongju/my-test/events.py
```

`nano` 저장은 Ctrl+O → Enter, 종료는 Ctrl+X입니다. 성공 출력은 `state: applied`, `workers: 2`, 시나리오 SHA256과 웹 주소를 포함합니다. 파일 저장만으로 Python은 재적재되지 않습니다. `apply`가 이전 결과를 S3에 보관·재조회 검증하고 읽기 전용 스냅샷을 선택한 다음 Locust master와 worker 2개만 재시작합니다. API·DB·Redis는 유지됩니다. 시나리오를 바꾸려고 환경을 다시 생성할 필요는 없습니다.

실행 중에는 apply를 거부합니다. 문법·import 검증 실패 시 이전 시나리오를 유지하고, 교체 후 준비 실패 시 이전 상태 복원을 시도합니다. 적용 중에는 다른 사람이 START하지 않도록 합니다. 한 번 적용한 시나리오는 환경 전체에 공유됩니다.

예제 [events.py](../loadtest/examples/events.py)는 `StageUser`를 상속해 `GET /api/v1/events?page=1&size=10`을 반복하고 1~2초 대기합니다. `catch_response=True` 안에서 `self.check(response, events_page)`를 호출해 응답 계약과 업무 결과를 기록합니다. 개인 파일은 일반 Python 파일 하나, 최대 1MiB이며 설치된 패키지를 사용합니다. 다른 개인 파일 import나 임의 패키지 설치를 전제로 작성하지 않습니다.

기본 `smoke_events`는 합성 USER로 `GET /api/v1/events?page=1&size=1`을 검사합니다. 개인 예제 역시 기본적으로 같은 준비된 합성 USER 헤더를 사용합니다. **가상 사용자 50명은 DB 회원 50명이 아닙니다.** 여러 회원이 필요한 시나리오는 회원·권한·멤버십 데이터를 직접 준비하고 요청별 헤더를 맞춥니다. 상대 API 경로, 고정 요청 이름, 명시적 timeout, 업무 계약에 맞는 검증 함수를 사용합니다. 상세 계약은 [런타임 안내](../loadtest/README.md)와 [CONTRACT](../loadtest/CONTRACT.md)를 따릅니다.

### 더미 데이터 입력과 정리

공통 기본 데이터는 담당자가 준비하고, 시나리오별 더미 데이터는 시험하는 팀원이 넣는 방식을 권장합니다. 제공된 `seed.sql`은 조회 예제이며 보유한 더미 SQL이 자동 적용된 것은 아닙니다. 자신의 시험 폴더에 `seed.sql`과 `cleanup.sql`을 함께 관리합니다.

SSH 서버 셸에서 파일을 편집하고 DB에 접속합니다.

```bash
nano /opt/getddo-loadtest/scenarios/gyeongju/my-test/seed.sql
nano /opt/getddo-loadtest/scenarios/gyeongju/my-test/cleanup.sql
loadtest db
```

`mysql>`에서 실행합니다. SOURCE 경로는 내 PC가 아니라 SSH 서버의 파일 경로입니다.

```sql
SELECT DATABASE(), CURRENT_USER();
SHOW SESSION STATUS LIKE 'Ssl_cipher';
SOURCE /opt/getddo-loadtest/scenarios/gyeongju/my-test/seed.sql;
```

`loadtest db`가 `stage_tester` 계정과 서버 인증서를 검증하는 TLS 접속을 처리합니다. 비밀번호를 직접 인자로 넣지 않습니다. 계정은 SELECT·INSERT·UPDATE·DELETE용입니다. 기존 SQL에 CREATE TABLE·ALTER·DROP·TRUNCATE·GRANT가 있다면 데이터 입력 부분과 구분하고 담당자에게 확인합니다.

입력 후 시험별 ID로 필요한 건수·상태·연결 관계를 조회합니다. 반복 입력 시 중복 키·중복 행 처리도 작성합니다. 다른 사람의 부하 중 공용 데이터를 변경하지 않습니다. 시험 종료 후 서버 셸에서 `loadtest collect`로 결과·소스를 보관한 다음 `loadtest db`로 재접속해 정리 파일을 실행합니다.

```sql
SOURCE /opt/getddo-loadtest/scenarios/gyeongju/my-test/cleanup.sql;
```

정리 SQL은 자신의 시험 ID·식별자에 해당하는 행만 DELETE하며 외래 키와 삭제 순서를 확인합니다. 공용 데이터·전체 테이블을 일괄 삭제하지 않습니다. 종료는 MySQL의 `\q`입니다. 수집은 Python·SQL 파일을 보관하지만 **DB 전체 덤프를 만들지는 않습니다.** 필요한 데이터는 환경 삭제 전에 별도로 보관합니다.

## 부하 테스트 사용법과 시나리오 관리

Locust에 로그인하고 worker 2개의 준비를 확인합니다. 세션은 30분이며 만료되면 재로그인합니다. 로그인 5회 실패 시 60초 잠금이 있습니다.

| Locust 입력 | 의미 | 첫 확인 예시 |
| --- | --- | --- |
| Number of users | 동시 가상 사용자 목표. RPS나 고유 회원 수가 아님 | `2` |
| Ramp up | 초당 추가할 사용자 수 | `1` |
| Host | 생성된 Stage 내부 ALB, 고정 대상 | 표시된 값 유지 |
| Run time | 부하 전체 실행 시간. 증가 구간 포함 | `20s` |
| Profile | 시나리오 선택 기능이 아님 | 빈 값 |

웹 START 후 [Grafana](#grafana-지표)를 관찰합니다. Run time에 도달하거나 Stop을 누르면 부하만 중지됩니다. 창을 닫아도 서버 작업은 계속될 수 있습니다. 요청 수·실패·업무 결과를 확인한 다음 SSH 서버에서 수집합니다.

```bash
loadtest collect
```

수집은 부하 중지·최종 worker 보고 대기·CSV/HTML·실제 적용 스냅샷·개인 Python/SQL 보관과 S3 readback/해시 검증을 수행합니다. 출력의 `bucket`, `prefix`, 파일별 `sha256`을 시험명·시간·users/ramp/duration과 함께 기록합니다. S3에서 해당 prefix를 자신의 PC로 다운로드할 수 있습니다.

```bash
aws s3 cp s3://BUCKET/RESULT_PREFIX/ ./stage-results/ --recursive \
  --profile getddo-dev --region ap-northeast-2
```

`BUCKET`과 `RESULT_PREFIX`는 이번 수집 출력으로 바꿉니다. 받은 `locust/run.html`을 열고, Linux/WSL에서는 해당 `locust` 폴더에서 `sha256sum -c SHA256SUMS`로 보고서 해시를 확인할 수 있습니다. 추가 소스·메타데이터 해시는 수집 JSON의 목록과 대조합니다.

같은 시나리오를 반복하면 수집 성공 후 웹에서 START합니다. 파일을 바꿨다면 `loadtest apply FILE` 성공 후 시작합니다. 수집 실패 시 새 시험을 열지 말고 원인을 해결한 뒤 같은 `loadtest collect`를 재시도합니다. 미완료 업로드를 먼저 복구합니다.

| 증상 | 확인·조치 |
| --- | --- |
| `An unknown error occurred` / 409 | 이전 시험 미수집, 실행/중지/apply 중, worker 누락·오래된 통계 확인. 종료·수집 성공과 worker2 준비 후 재시도 |
| Host/Profile 거부 | Host 유지·Profile 빈 값·Run time 양수 확인 |
| 변경한 Python이 실행되지 않음 | 저장 후 apply 성공 여부와 SHA 확인 |
| 요청은 있는데 업무 카운터 없음 | 개인 코드의 `catch_response=True`와 `self.check` 호출 확인 |
| SSH Permission denied | 기존 PEM, Linux 사용자 `ec2-user`, 키 파일 권한 확인 |
| `loadtest: command not found` | 내 PC 대신 SSH 서버에서 실행했는지 확인 |
| SQL 권한 거부 | DML용 계정인지와 SQL에 DDL이 섞였는지 확인 |

## Grafana 지표

기존 Prod 대시보드·데이터 소스·시계열은 유지합니다. [Staging load test](https://dzc8oneey9mmx.cloudfront.net/grafana/d/getddo-staging-overview)와 Staging 폴더의 AWS resources를 사용합니다. 최근 15분부터 확인하고 종료 후 시험 시작~종료 절대 시각으로 고정합니다.

| 순서·화면 | 확인 항목 |
| --- | --- |
| 발생기 건전성 | master scrape·expected/connected/fresh/healthy worker, 데이터 신선도. worker2와 발생기 CPU·메모리를 먼저 확인 |
| Locust 부하·지연 | 사용자·RPS·누적 요청·실패, rolling/cumulative p50/p95/p99. 지연 단위는 초 |
| 업무 결과 | success, expected_rejection, rate_limited, server_error, http_error, invalid_envelope, transport_error |
| API/JVM | a/c 요청·RPS·5xx·지연, heap·GC, Hikari active/pending/idle/max, Tomcat 사용량 |
| 호스트 | API a/c·Locust CPU·메모리·디스크 여유·exporter 상태 |
| AWS resources | RDS 연결·CPU·I/O 지연·여유 공간, Redis 메모리·eviction·network, ALB target·5xx·지연, CPU credits |

Prometheus Stage 수집은 5초, 짧은 rate는 1분 창입니다. percentile은 master가 전체 요청에서 계산한 값이며 worker percentile 평균이 아닙니다. AWS 지표는 CloudWatch 원래 해상도를 따르고 CPU credits는 5분입니다. No data·NaN·수집 공백·요청0을 성공0으로 해석하지 않습니다. 짧은 시험 후 지표 도착을 기다려 대조합니다.

본격 시험 전에 시나리오·회원/데이터·부하 단계·기대 업무 결과·지연/실패 합격 및 중단 기준을 정합니다. 작은 조회 시험 성공은 쓰기 정합성·동시성·최대 처리량 또는 미정 SLO의 합격을 뜻하지 않습니다.

## 팀원이 처음 실행할 때

이 절은 **환경을 생성·종료하는 담당자**용입니다. 서버를 사용하는 팀원은 앞 절을 따릅니다. 생성 스크립트는 Linux/WSL, Python 3.12, AWS CLI, Git·GitHub CLI를 사용하며 로컬 Terraform·Docker는 제출에 필요하지 않습니다. 실제 생성·수집·삭제는 AWS CodeBuild, 독립 만료 정리는 Scheduler가 실행합니다.

### AWS 인증 설정

`aws login`에는 **AWS CLI 2.32.0 이상**이 필요합니다. 내 PC에서 `aws --version`으로 확인하고, 구버전이면 [공식 설치·업데이트 안내](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html)를 따릅니다. 관리자는 Stage 실행 권한과 별도로 IAM 사용자/그룹의 `SignInLocalDevelopmentAccess` 또는 동등한 `signin` 권한을 확인합니다. 자세한 선행 조건은 [AWS 로그인 공식 문서](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-sign-in.html)를 참고합니다.

현재 팀 구성의 `gyeongju` 인증을 사용합니다. 유효한 `getddo-dev`가 있으면 STS 조회부터 하고 재로그인은 생략합니다. 새 PC에서는 다음을 설정합니다. root 인증을 실행 프로필에 연결하지 않습니다.

```bash
aws configure set login_session arn:aws:iam::942466928325:user/gyeongju --profile getddo-cli-login
aws configure set region ap-northeast-2 --profile getddo-cli-login
aws configure set credential_process 'aws configure export-credentials --profile getddo-cli-login --region ap-northeast-2 --format process' --profile getddo-dev
aws configure set region ap-northeast-2 --profile getddo-dev
aws login --profile getddo-cli-login --region ap-northeast-2 --remote
aws sts get-caller-identity --profile getddo-dev --region ap-northeast-2
```

만료 시 login만 다시 실행합니다. `credential_process` 출력은 SDK가 파이프로 읽으므로 직접 출력·저장하지 않습니다. 별도 IAM 사용자는 기존 사용자와 같은 권한을 자동으로 갖지 않습니다. 관리자가 Stage 제어·bundle/결과 S3·SSM 비밀번호 조회·Scheduler/PassRole·EICE 터널 권한을 확인해야 합니다. STS 성공만으로 모든 권한이 확인되는 것은 아닙니다.

### 소스·CI 이미지·bundle 준비

검토한 고정 Infra·BE 소스와 **같은 BE SHA의 성공한 CI 이미지 산출물**을 사용합니다. 준비 담당자가 bundle/config와 해시를 만들고 다른 실행자에게 전달할 수도 있습니다. main을 새로 병합해도 기존 환경 이미지는 자동 갱신되지 않습니다.

작업 중인 checkout을 바꾸지 않고 새 폴더에서 준비합니다. 로컬 Python 환경·산출물은 checkout 밖 `stage-work`에 둡니다.

```bash
gh auth login
gh auth setup-git
mkdir getddo-stage-run
cd getddo-stage-run
git clone https://github.com/Uplus-GETDDO/getddo-infra.git
git clone --no-checkout https://github.com/Uplus-GETDDO/getddo-be.git
mkdir -p stage-work/image
python3.12 -m venv stage-work/venv
stage-work/venv/bin/python -m pip install boto3==1.40.59
cd getddo-infra
export AWS_PROFILE=getddo-dev
```

GitHub Actions에서 필요한 백엔드 commit의 빌드·테스트·이미지가 성공한 실행을 선택합니다. `CI_RUN_ID`, `CI_ATTEMPT`를 실제 값으로 바꿉니다. commit SHA·산출물 revision·이미지 해시는 빌더가 대조합니다. 만료된 산출물은 해당 소스의 CI를 다시 실행해 준비합니다. 이 절차가 운영 API를 배포하는 명령은 아닙니다.

```bash
CI_RUN_ID='성공한_BE_Actions_실행_ID'
CI_ATTEMPT='1'
export INFRA_SHA="$(git rev-parse HEAD)"
BACKEND_SHA="$(gh run view "$CI_RUN_ID" --repo Uplus-GETDDO/getddo-be --json headSha --jq .headSha)"
git -C ../getddo-be checkout --detach "$BACKEND_SHA"
gh run download "$CI_RUN_ID" --repo Uplus-GETDDO/getddo-be \
  --name "staging-image-${BACKEND_SHA}-${CI_ATTEMPT}" --dir ../stage-work/image
../stage-work/venv/bin/python scripts/build-staging-bundle.py \
  --infra-sha "$INFRA_SHA" \
  --backend ../getddo-be --backend-sha "$BACKEND_SHA" \
  --artifact-dir ../stage-work/image --output ../stage-work/source.tar.gz
```

선택한 실행의 head SHA가 검토한 백엔드 commit인지 확인합니다. 빌더 출력의 source SHA·image ID·image archive SHA256·bundle SHA256을 기록합니다. 기존 출력 덮어쓰기는 거부하므로 다시 만들 때 새 경로를 사용합니다.

[설정 계약](../terraform/staging/README.md#submission-and-recovery)에 맞는 비밀 없는 config를 만듭니다. AMI는 available·x86_64 Amazon Linux 2023인지, SG·CloudFront는 현재 계정 구성인지 사전에 검토합니다. 아래는 기존 계정 구성 예시이며 새 계정에 그대로 적용하지 않습니다.

```bash
aws ec2 describe-images --region ap-northeast-2 --image-ids ami-0870825cefcaafcc8 \
  --query 'Images[].{Id:ImageId,State:State,Architecture:Architecture,Name:Name}'
../stage-work/venv/bin/python - <<'PY'
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
Path("../stage-work/config.json").write_text(json.dumps(config, indent=2) + "\n")
PY
```

<a id="준비와-실행"></a>

### 생성과 만료 설정

다음은 요청 시점부터 120분 예시입니다. 수명은 준비 시간을 포함한 **15~5760분(최대96시간)**입니다. 생성만으로 부하가 자동 시작되지는 않습니다.

```bash
export AWS_PROFILE=getddo-dev
../stage-work/venv/bin/python scripts/staging.py up --max-duration 120 \
  --bundle ../stage-work/source.tar.gz --config ../stage-work/config.json
RUN_ID='up이_반환한_run_id'
../stage-work/venv/bin/python scripts/staging.py status --run-id "$RUN_ID"
```

특정 시각에 정리를 시작하려면 위 `--max-duration` 대신 `--expires-at`을 사용합니다. 아래 날짜는 원하는 날짜와 시간대로 바꾸며, 요청 시점에서 15분 이상·96시간 이내여야 합니다. 두 만료 인자는 동시에 사용할 수 없습니다.

```bash
EXPIRES_AT='YYYY-MM-DDTHH:MM:SS+09:00'
../stage-work/venv/bin/python scripts/staging.py up --expires-at "$EXPIRES_AT" \
  --bundle ../stage-work/source.tar.gz --config ../stage-work/config.json
```

`up` 반환은 `{run_id, build_id, expires_at}`이며 생성 완료가 아닙니다. 같은 run으로 status를 반복 조회하고 생성 CodeBuild가 성공·완료되고 `phase: ready`가 된 뒤 접속합니다. `ready`는 API 두 대·DB/Redis·합성 USER·Locust master/worker2·HTTPS·모니터 등록을 확인한 상태입니다. 반환 후 터미널을 닫아도 AWS 작업과 예약 정리는 계속됩니다.

`status`의 `deployment.locust_instance`, 만료 시각, EICE 정보와 호스트 지문을 팀원에게 공유합니다. 다음 명령은 CodeBuild 진행 조회입니다. `BUILD_ID`는 해당 작업 응답값으로 바꿉니다.

```bash
aws codebuild batch-get-builds --ids BUILD_ID \
  --query 'builds[].{Status:buildStatus,Phase:currentPhase,Complete:buildComplete}' \
  --profile getddo-dev --region ap-northeast-2
```

## 수집과 삭제

개인 시험이 끝나면 SSH 서버의 `loadtest collect` 후 `exit`로 나갑니다. 공용 환경 전체를 삭제하지 않습니다. 모든 팀원의 사용이 끝났을 때만 담당자가 내 PC의 저장소 루트에서 아래 명령을 사용합니다.

```bash
../stage-work/venv/bin/python scripts/staging.py collect --run-id "$RUN_ID"
../stage-work/venv/bin/python scripts/staging.py status --run-id "$RUN_ID"
# collect CodeBuild 성공·완료와 갱신된 results를 확인한 뒤 아래 삭제 요청
../stage-work/venv/bin/python scripts/staging.py destroy --run-id "$RUN_ID"
../stage-work/venv/bin/python scripts/staging.py status --run-id "$RUN_ID"
```

각 명령은 비동기 요청입니다. 반환된 CodeBuild가 성공·완료되고 이번 수집 결과가 갱신될 때까지 같은 run의 `status`를 반복 조회합니다. 같은 수집을 중복 요청하거나 수집과 삭제를 겹쳐 실행하지 않습니다. `phase: collected`만으로 이번 요청 완료를 판단하지 말고 **반환된 CodeBuild 성공·완료와 results 갱신**을 함께 확인합니다. `collected`는 자원이 계속 실행 중일 수 있는 단계이며 부하 상태는 Locust에서 확인합니다. 정상 destroy 자체도 최종 부하 중지·수집을 수행하므로 사전 collect가 반드시 필요한 것은 아닙니다.

결과 색인은 manifest `results`의 key/SHA256을 통해 `staging/runs/RUN_ID/results/`에서 조회합니다. 개인 호스트 수집은 그 아래 `operator/` prefix를 반환합니다. CSV·이력·실패/예외 요약·HTML·메타데이터·실제 선택한 Python 및 개인 Python/SQL을 보관합니다. 결과에는 비밀번호·쿠키·세션 비밀·개인정보를 넣지 않습니다.

### Grafana 이력과 수집 제한

공용 Prometheus는 환경 삭제 후에도 유지되며 보존은 **14일 또는20GB 중 먼저 도달하는 한도**입니다. 14일 전부 보장을 뜻하지 않습니다. S3 결과 장기 보관 기간은 별도 미정이며 `staging/` 결과 자동 만료를 추가하지 않았습니다.

환경 전체 수집에서 유효한 24시간 이내 등록은 Grafana 쿼리·시계열 S3 내보내기를 수행합니다. **생성~만료 수명이 24시간을 초과하거나 등록 만료 후 수집하면** 현재 공유 exporter의 지원 범위를 벗어나 `collection_status: partial`, `monitoring_status: unavailable`과 다음 사유를 기록합니다.

- `lifetime_exceeds_monitoring_limit`: 수명이24시간 초과.
- `expired_monitoring_registration`: 수명이24시간 이내여도 등록 만료 후 수집.

이 경우에도 Locust·개인 Python/SQL 보관과 readback/해시 검증은 필수입니다. Grafana 실시간 조회·기존 Prometheus 이력은 별개로 유지합니다. 필요한 그래프·패널 데이터는 시험 직후 보관합니다. 지원 구간에서 발생한 인증·소유권·무결성 오류를 위 제한으로 무시하지 않습니다.

### 삭제 범위와 복구

| 삭제 | 유지 |
| --- | --- |
| Stage EC2/EBS·시험 RDS/Redis·전용 ALB/TG·임시 rule/SG/IAM·앱/시험 DB 접속 파라미터·임시 로그 | Prod·기존 공유 네트워크·monitor/Grafana·S3 결과·영구 Locust 로그인3개·지속 제어 기반·State/manifest 기록 |

만료 시각은 삭제 **시작**이며 실제 RDS 등 삭제에는 추가 시간이 걸립니다. 정상 destroy는 필수 보관·재조회 검증 또는 detach 실패 시 삭제를 중단합니다. TTL·생성 실패 rollback은 수집을 최대360초 시도한 뒤 임시 자원 삭제를 우선합니다. 장애로 삭제가 실패하면 소유 잠금을 유지할 수 있습니다.

생성·삭제 실패 시 해당 CodeBuild 종료와 manifest·오류를 먼저 확인합니다. 실패한 채 새 up을 반복하거나 살아 있는 실행의 잠금을 강제로 삭제하지 않습니다. 소유권·부분 자원·Terraform 잠금 복구는 [실행 계약](../terraform/staging/README.md#submission-and-recovery)을 따릅니다.

최종 `destroyed`는 실제 run 소유 잔여 자원0과 영구 로그인 설정 보존을 확인한 상태입니다. 담당자는 해당 CodeBuild 성공·manifest·자원 재고를 확인합니다. `/locust/` 접속 불가와 기존 `/grafana/` 계속 접근은 삭제 후 정상이며, Grafana가 열린다는 사실로 Stage가 남았다고 판단하지 않습니다.

## 검증 범위

개인 Python 적용·오류 파일 거부·실행 중 교체 거부, 기존 PEM SSH, DB TLS와 데이터 CRUD, 웹·개인 시나리오의 작은 부하와 Grafana 대조, 결과·소스 S3 readback을 실환경에서 확인했습니다. 별도 검증 환경에서 5분 뒤 TTL 정리 작업을 시작하고 전체 삭제·독립 잔여0·Prod/monitor/영구 로그인 보존도 확인했습니다. 5분은 검증용 시작 예약 간격이며 일반 수명 기본값이나 전체 AWS 삭제 완료 시간은 아닙니다.

이는 매 실행의 정상 동작이나 최대 성능을 보장하는 판정이 아닙니다. 새 소스·AMI·권한·환경 변경은 다시 검증하며, 업무별 쓰기/동시성 정합성·부하 한계·SLO 기준은 별도로 정합니다.
