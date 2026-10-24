# 운영 RDS Multi-AZ 적용 절차

상태: **적용 준비 코드**. 실제 운영 DB는 2026-10-24 조회 당시 Single-AZ다.
[운영 아키텍처 그림](architecture/production.html)은 현재 관측 구성을 표시한다.
이번 변경은 기존 `getddo-dev-mysql`의 Multi-AZ DB 인스턴스 활성화이며,
클러스터나 읽기 복제본을 만들지 않는다. 다른 AZ의 대기 DB는 읽기 요청을 받지 않는다.
기존 DNS·비밀번호 버전·20 GiB gp3·암호화·비공개·삭제 보호·백업 1일을 유지한다.

## 비용과 지원 사양

2026-10-24 서울 RDS API에서 MySQL 8.4.11 / db.t4g.micro / gp3의 MultiAZCapable=true,
최소 20 GiB를 확인했다. 같은 날 공개 요금 파일(발행 2026-10-20)을 Decimal로 계산했다.

| 항목 | Single-AZ | Multi-AZ |
| --- | --- | --- |
| db.t4g.micro 시간당 USD | 0.025 | 0.051 |
| gp3 GB-month당 USD | 0.131 | 0.262 |

730시간·20 GiB 기준 추가 비용은 `(0.051-0.025)*730 + (0.262-0.131)*20 = US$21.60/월`이다.
세금·환율·할인·추가 CPU 크레딧·추가 백업 비용은 제외한다. 적용 시 최신 요금을 재확인한다.

## 적용 전 조건

1. 백엔드의 `RDS_HA_BACKEND_HANDOFF_V1` 전체 블록을 받고
   `READY_FOR_INFRA_APPLY`, 계정, 서울 리전, 이미지 digest·소스 SHA와 A/C 실행 상태를
   실제 배포 및 ALB healthy 결과와 대조한다. 미수신·한쪽 미배포이면
   `WAITING_FOR_BACKEND`로 반환하고 DB/IAM을 적용하지 않는다.
2. PR 필수 리뷰·CI 및 기존 정상 반영 절차를 충족한다. 임의의 추가 release PR이나
   보호 브랜치 직접 push로 우회하지 않는다. State 주소·기존 리소스 import를 변경하지 않는다.
3. `getddo-gyeongju`의 STS 계정 `942466928325`와 ARN을 확인한다.
   프로필명만 신뢰하지 않는다. `getddo-root`는 필요한 IAM 보완에만 사용한다.
   이 런북은 실제 운영자 프로필을 사용한다. README의 예시 `getddo-dev`와 별개이며,
   해당 프로필의 기존 credential_process 충돌을 피한다. 다른 운영자는 자신의
   승인된 프로필로 바꾸되 위 계정·실행 주체를 먼저 검증한다.
4. 아래 조회로 pending 변경·유지보수·서브넷 상태·여유 IP·현재 사양의 Multi-AZ 지원을
   다시 확인한다. 진행 중인 변경·배포가 있으면 중복 실행하지 않는다.
5. 저트래픽 작업 시작/종료 시간, 관찰 담당자·연락 수단을 정하고 최근 정상 구간의
   API p95·오류율·RDS WriteLatency·DatabaseConnections를 기록한다.
   허용 수치와 초과 지속 시간을 담당자와 확정하여 기록한다. 미확정이면 적용하지 않는다.
6. LatestRestorableTime과 현재 UTC의 차이를 기록하고, 아래 전용 이름으로 작업 직전
   수동 snapshot을 생성해 `available`을 확인한다. IAM 보완은 별도 bootstrap 전체 plan을
   먼저 검토한다. 이번에는 `RebootDBInstance` 권한을 추가하거나 실행하지 않는다.

```bash
aws sts get-caller-identity --profile getddo-gyeongju --region ap-northeast-2 --no-cli-pager
aws rds describe-db-instances --profile getddo-gyeongju --region ap-northeast-2 \
  --db-instance-identifier getddo-dev-mysql --no-cli-pager
aws rds describe-pending-maintenance-actions --profile getddo-gyeongju \
  --region ap-northeast-2 \
  --resource-identifier arn:aws:rds:ap-northeast-2:942466928325:db:getddo-dev-mysql --no-cli-pager
aws rds describe-orderable-db-instance-options --profile getddo-gyeongju \
  --region ap-northeast-2 --engine mysql --engine-version 8.4.11 \
  --db-instance-class db.t4g.micro --no-cli-pager
aws ec2 describe-subnets --profile getddo-gyeongju --region ap-northeast-2 \
  --subnet-ids subnet-091ff1d3c2f446741 subnet-0ebda11b6bbe5f275 \
  --query 'Subnets[].{Id:SubnetId,AZ:AvailabilityZone,FreeIPs:AvailableIpAddressCount}' --no-cli-pager
```

snapshot 이름은 기존 IAM 접두사에 UUID를 붙여 생성 시도마다 새로 만든다.
응답 유실이나 waiter 실패 시에는 기존 ID의 상태를 먼저 조회한다. 생성이 성공했다면
해당 ID의 완료 대기만 재시도하고, 새 snapshot 생성이 필요할 때 새 UUID를 사용한다.
생성과 `available` 완료 대기도 관찰자가
있는 확정된 저트래픽 작업 시간 안에서 수행한다. Single-AZ snapshot 생성은 수초에서
수분간 I/O를 일시 중단할 수 있으므로, 생성 요청 전부터 API 오류율·p95와 DB 지연을
관찰하고 대기·전환·안정 확인까지 이어간다. 확정 임계치를 초과하면 DB 전환으로
진행하지 않고 아래 중단 절차를 따른다.

```bash
snapshot_id="getddo-dev-mysql-pre-multiaz-$(python3 -c 'import uuid; print(uuid.uuid4())')"
aws rds create-db-snapshot --profile getddo-gyeongju --region ap-northeast-2 \
  --db-instance-identifier getddo-dev-mysql --db-snapshot-identifier "$snapshot_id" --no-cli-pager
aws rds wait db-snapshot-available --profile getddo-gyeongju --region ap-northeast-2 \
  --db-snapshot-identifier "$snapshot_id"
aws rds describe-db-snapshots --profile getddo-gyeongju --region ap-northeast-2 \
  --db-snapshot-identifier "$snapshot_id" --query 'DBSnapshots[0].{Id:DBSnapshotIdentifier,Status:Status}' --no-cli-pager
```

## 전체 저장 plan과 적용

로컬 검사는 `python3 scripts/validate.py terraform`, `scripts`, `documents`를 실행한다.
mock apply는 외부 AWS API·원격 State를 사용하지 않으며 실제 HA 검증이 아니다.

| root | 기존 S3 State 키 | 허용하는 변경 |
| --- | --- | --- |
| `terraform/bootstrap` | `bootstrap/terraform.tfstate` | 필요할 때 기존 운영자 DB 정책의 snapshot 생성 권한만 수정 |
| `terraform/prod` | `dev/terraform.tfstate` | 기존 `aws_db_instance.main` in-place Multi-AZ 활성화·이번 전환만 즉시 적용 |

실제 입력은 기존 운영 경로로 전달한다. 테스트 비밀값·추측한 tfvars를 사용하지 않는다.
비밀번호와 write-only 버전은 기존 값을 보존한다. `db_apply_immediately`는 bool이며
기본값은 false다. 관찰자가 있는 확정된 작업 시간의 전환 plan에만 명시적으로 true를
전달한다. tfvars에 true를 영구 저장하지 않는다. 다른 pending 변경도 함께 적용될 수 있다.

```bash
# 실행 전에 AWS_PROFILE, 실제 변수 및 ephemeral 비밀값을 기존의 안전한 경로로 준비한다.
# plan 파일은 비밀값을 포함할 수 있다. chmod 700 작업 경로와 umask 077을 사용한다.
umask 077
terraform -chdir=terraform/prod init -input=false
terraform -chdir=terraform/prod plan -input=false -lock-timeout=60s -var=db_apply_immediately=true -out=/tmp/rds-ha-prod.tfplan
terraform -chdir=terraform/prod show -no-color /tmp/rds-ha-prod.tfplan
# 위 모든 게이트와 전체 plan 검토를 통과한 뒤에만 같은 plan을 적용한다.
terraform -chdir=terraform/prod apply /tmp/rds-ha-prod.tfplan
```

plan 원문·State·비밀값은 티켓/PR/결과 파일에 복사하지 않는다. DB 삭제·교체,
endpoint 변경·비밀번호 회전·무관한 리소스 변경이면 중단하고 원인을 확인한다.
`-target`으로 drift를 숨기지 않는다. IAM 보완이나 백엔드 배포·코드·입력·원격 State가
달라지면 다시 계획하고 검토한다. bootstrap plan을 prod에 적용하지 않는다.

## 대기 DB 준비와 서비스 확인

전환 중 RDS 이벤트, API 오류율·p95, DB 쓰기 지연·연결 수를 관찰한다.
`available`만으로 완료를 선언하지 않는다. 사용 CLI 모델이 아래 storage 필드를 지원하는지
확인하고, 미지원이면 최신 CLI/API로 조회한다. 미지원 필드의 null을 완료로 해석하지 않는다.

```bash
aws rds describe-db-instances --profile getddo-gyeongju --region ap-northeast-2 \
  --db-instance-identifier getddo-dev-mysql \
  --query 'DBInstances[0].{Status:DBInstanceStatus,MultiAZ:MultiAZ,PrimaryAZ:AvailabilityZone,StandbyAZ:SecondaryAvailabilityZone,Pending:PendingModifiedValues,StorageStatus:StorageOperationStatus,StorageProgress:StorageOperationPercentProgress}' --no-cli-pager
aws rds describe-events --profile getddo-gyeongju --region ap-northeast-2 \
  --source-type db-instance --source-identifier getddo-dev-mysql --duration 180 \
  --query 'Events[].{Time:Date,Message:Message}' --no-cli-pager
```

완료 근거는 MultiAZ=true, 서로 다른 주/대기 AZ, 전환 완료 이벤트(RDS-EVENT-0025),
Initializing 종료와 storage 필드 소멸, 무관한 pending 변경 없음, 기존 endpoint·보호 설정
유지다. 정상 읽기 API부터 A/C를 확인하고 안전한 기존 검증 대상이 있을 때만 쓰기를 확인한다.
안전한 대상이 없으면 쓰기는 미실시로 기록한다. 안정 관찰 뒤 전체 prod plan을 다시 실행해
잔여 변경을 확인하고 그림/README의 실제 상태를 갱신한다.

## 전환 직후 즉시 적용 설정 복구

`apply_immediately=true`는 Terraform의 후속 DB 변경에도 사용되는 값이다.
위 전환·대기 초기화·서비스 안정 확인을 모두 마친 직후 pending 변경이 없음을 확인하고,
`db_apply_immediately=true` override를 제거해 기본값 false로 복구한다. Multi-AZ와
나머지 보호 설정은 유지한다. 소스 수정이나 추가 PR은 필요하지 않으며, 이 복구 확인까지
작업 완료에 포함한다. 기존 tfvars·환경 변수에도 true가 남아 있지 않은지 확인한다.

기존 실제 입력·State·잠금으로 override 없는 새로운 전체 저장
plan을 생성한다. 기대 변경은 `apply_immediately: true → false`뿐이며, Multi-AZ 해제·
삭제·교체·비밀번호/endpoint 변경·무관한 변경이면 중단한다. 검토한 동일 plan으로 반영하고
후속 전체 plan에서 잔여 변경이 없는지 확인한다. 이전 전환 plan을 재사용하지 않는다.
추가 PR이나 보호 브랜치 직접 push로 우회하지 않는다.

`false`는 이후 변경 중 유지보수 창을 따르는 항목을 다음 창으로 미루는 설정이며,
모든 DB 변경을 예약하거나 이미 진행 중인 작업을 취소하는 값은 아니다.
실제 DB 변경을 포함하는 후속 plan에서는 각 항목의 AWS 적용 시점과 pending 변경을
다시 확인한다. 이 설정 복구를 위해 재부팅하거나 강제 전환하지 않는다.

## 중단과 복구

확정 임계치를 초과하면 추가 배포·시험을 중단하고 시각·이벤트·지표를 보존한다.
진행 중 변경을 취소하려고 연속 수정·재부팅하지 않는다. API 복구 문제는 백엔드 담당자에게
사실과 시각을 전달하며 무단 앱 재배포·양쪽 재시작을 하지 않는다.

Multi-AZ는 잘못된 삭제도 복제하므로 백업을 유지한다. snapshot/PITR은 새 DB로 복원하고
접속을 별도로 전환해야 하며 즉시 원복 수단이 아니다. 원본 DB를 덮어쓰지 않는다.
Single-AZ 복귀는 별도 변경 검토 대상이다. 강제 전환·재부팅·연결 차단·부하 시험은
이번 작업에서 `NOT_RUN_USER_DEFERRED`로 남긴다.

## 공식 근거

- [Multi-AZ DB 인스턴스](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/Concepts.MultiAZSingleStandby.html)
- [전환·대기 볼륨 초기화](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/Concepts.MultiAZ.Migrating.html)
- [DB 설정 적용 시점](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_ModifyInstance.Settings.html)
- [Single-AZ 수동 snapshot의 I/O 영향](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_CreateSnapshot.html)
- [AWS 공개 서울 RDS 요금](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonRDS/current/ap-northeast-2/index.json)
