# 임시 스테이징 부하 테스트

관련 Jira: GD-306~GD-311. 환경 생성은 AWS의 CodeBuild에서, 최대 수명 정리는 독립 Scheduler에서 실행한다. 로컬 터미널을 닫아도 실행과 정리가 계속된다. 생성 완료만으로 부하를 시작하지 않는다. 환경과 역할의 상세 계약은 [Stage lifecycle](../terraform/staging/README.md)을 따른다.

현재 이 문서는 구현·검증 절차이며 실제 AWS 인수 결과가 아니다. 최종 리뷰 SHA의 생성 → 작은 부하 → 결과 재조회 → 자원 삭제를 확인한 결과는 PR에 별도로 기록한다.

## 준비와 실행

Python 3.12, boto3 1.40.59, AWS CLI, Git과 Terraform을 사용한다. 기존 `getddo-dev` 프로필로 실행한다. 런타임에 관리자/root 자격 증명을 전달하지 않는다.

AWS CLI `aws login`을 쓰는 로컬 환경에서 고정 SDK가 로그인 캐시를 직접 읽지 못하면, CLI 로그인용 프로필과 SDK의 `credential_process`를 연결한다. 같은 IAM 사용자 세션을 사용하며 액세스 키를 파일이나 명령 인자에 복사하지 않는다. 다음 예시는 기존 `gyeongju` 사용자와 `getddo-dev` 실행 프로필을 연결한다. root 세션을 연결하지 않는다.

```sh
aws configure set login_session arn:aws:iam::942466928325:user/gyeongju --profile getddo-cli-login
aws configure set region ap-northeast-2 --profile getddo-cli-login
aws configure set credential_process 'aws configure export-credentials --profile getddo-cli-login --format process' --profile getddo-dev
aws sts get-caller-identity --profile getddo-dev
```

기존 로그인 세션이 유효하면 재로그인 없이 공유한다. 세션이 만료되면 `aws login --profile getddo-cli-login --region ap-northeast-2 --remote`로 같은 IAM 사용자 인증을 갱신한다. `credential_process`의 출력은 SDK가 파이프로만 읽으므로 직접 출력·저장하지 않는다. CodeBuild와 EC2는 자체 역할의 자격 증명을 사용한다.

1. Infra·백엔드 리뷰 지적을 반영하고 CI를 확인한다. 백엔드는 PR head SHA로 생성한 `staging-image-SHA-ATTEMPT` 산출물을 사용한다. `main` 운영 CD로 배포하지 않는다.
2. 깨끗한 두 checkout과 CI 이미지로 bundle을 만든다. commit SHA, OCI image ID, 이미지 archive SHA256과 전체 bundle SHA256을 각각 검증한다. 아래 대문자 항목은 실제 값으로 대체한다.

```sh
python3 scripts/build-staging-bundle.py \
  --infra-sha INFRA_FULL_SHA \
  --backend /path/to/clean/getddo-be --backend-sha BACKEND_FULL_SHA \
  --artifact-dir /private/staging-image --output /private/staging-source.tar.gz
```

3. [설정 예시](../terraform/staging/README.md#submission-and-recovery)의 비밀 아닌 config에 같은 Infra SHA, 검증한 AMI와 기존 CloudFront 주소를 넣는다.
4. 최대 수명을 반드시 지정한다. 아래 120분은 환경 준비·검증·삭제를 위한 실행 예시다.

```sh
AWS_PROFILE=getddo-dev python3 scripts/staging.py up \
  --max-duration 120 --bundle /private/staging-source.tar.gz --config /private/staging.json
AWS_PROFILE=getddo-dev python3 scripts/staging.py status --run-id RUN_ID
```

`ready`에는 앱 두 대 배포, 전용 DB·Redis, 테스트용 USER 준비, Locust master·worker 2대의 대기 상태, HTTPS health와 모니터 등록 확인이 포함된다. 실제 업무 부하는 별도로 시작한다.

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
