# 운영 Redis 이중화 절차

기존 `getddo-dev-redis`에 복제본 한 대를 추가하고 Multi-AZ와 자동 장애 전환을 활성화한다.
목표는 서로 다른 AZ의 primary 1대와 replica 1대다. 실제 구성과 시험 결과는
[장애 전환 시험 결과](redis-failover-test-results.md)에서 확인한다.

## 연결과 데이터 계약

Redis OSS 7.1, `cache.t4g.micro`, cluster mode disabled를 유지한다.
앱은 기존 메인 DNS와 6379, TLS required, AUTH를 사용하며 읽기도 메인으로 보낸다.
사설 data-a/data-c 서브넷과 API 보안 그룹만 허용하는 규칙, 저장 암호화,
자동 스냅샷 0일과 `prevent_destroy`를 유지한다. 다른 저장소나 알림은 변경하지 않는다.

복제는 비동기이며 WAIT를 추가하지 않는다. 최근 카운터뿐 아니라 차단 상태와
RDS 반영 전 Redis 저널도 장애 시 유실될 수 있다. 이미 RDS에 저장된 이력은 유지되지만,
사라진 저널을 기존 워커가 복원한다고 보장하지 않는다. 이 구성은 무유실 보장이 아니다.

일반 공유 연결·응모 평가 전용 풀·이력 워커 전용 풀의 세 경로를 각각 확인한다. Redis 판정 오류 시
기존 fail-open을 유지하며 사용자·업무 검증은 계속한다. 이미 초과 판정을 얻은 요청은
기존 429와 Retry-After를 유지한다. 업무 거절 응답도 정상적인 제한기 통과 결과일 수 있다.

## 적용

1. 최신 main과 A/C 실행 revision·이미지·시작 시각·재시작 횟수, ALB 정상 상태를 기록한다.
   STS로 계정 `942466928325`와 서울 리전을 확인하고 동시 배포·Terraform 작업을 피한다.
2. 기존 입력으로 prod/bootstrap 전체 plan을 확인한다. 운영 State `dev/terraform.tfstate`와
   권한 State `bootstrap/terraform.tfstate`를 유지한다. 비밀번호는 기존 실행 입력으로만
   전달하고 원문 env·State·plan을 Git이나 공개 기록에 넣지 않는다.
3. 전체 로컬 검사와 PR의 필수 CI·리뷰를 통과한 소스를 정상 병합한다.
4. `getddo-root`는 bootstrap IAM 적용에만 사용한다. 전체 bootstrap plan에서 정확한
   운영 그룹 ARN·서울에 대한 `IncreaseReplicaCount` 보완만 확인하고 적용한다.
   시험 만료 입력은 null로 둔다. 실제 Redis 작업은 `getddo-gyeongju`로 실행한다.
5. prod 전체 plan에 이번 실행만 `redis_apply_immediately=true`를 전달한다.
   예상 변경은 기존 Redis 그룹과 대시보드의 in-place 수정이다. 무관한 변경이나
   삭제·교체가 있으면 멈춘다. 검토한 저장 plan을 적용한다.
6. provider 6.68.0은 노드를 먼저 증가시키고 HA 설정을 변경한다. 노드 증가는 즉시 적용되므로
   `redis_apply_immediately=false`를 전체 작업의 유지보수 보류 기능으로 해석하지 않는다.
   기존 그룹 증가 API에 preferred AZ 선언이 전달되지 않으므로 실제 배치를 반드시 조회한다.
7. available·노드 두 대·primary/replica 각 한 대·서로 다른 AZ·MultiAZ/AutomaticFailover enabled·
   pending 없음·기존 메인 endpoint 유지와 복제 동기화 상태를 확인한다. 같은 AZ면
   완료 처리와 시험을 중단하고 명시적 AZ 변경 계획을 별도로 검토한다.
8. 즉시 적용 입력을 제거하고 기본 false로 복구한다. 필요한 설정 변경만 마친 뒤
   전체 plan의 실행 변경 0과 A/C 제한 기능을 확인한다.

대시보드는 멤버 ID를 정렬해 각 노드의 CPU·메모리·eviction·연결·복제 지연을 표시한다.
ID 순서가 역할을 뜻하지 않는다. `ReplicationLag`는 replica 지표이며 primary의 미발행을
0으로 해석하지 않는다. 노드가 교체되면 콘솔용 JSON의 실제 ID도 대조한다.

## 단일 장애 전환 시험

- 안전한 합성 사용자와 존재하지 않는 UUID의 업무 거절 경로를 소스로 먼저 확인한다.
  실제 보상·응모·차감·알림을 만들지 않는다. A/C localhost에서 실제 컨트롤러와 제한기를
  통과시키고 초당 1회 이하·동시 1·제한 POST 총 60회 상한으로 기록한다.
- 같은 사용자·미션으로 A/C 교대 요청을 보내 합산 한도 5회 뒤 429/COMMON-006와
  Retry-After, 실제 1분 TTL 뒤 업무 거절로 돌아오는지 확인한다.
- 일반 연결과 응모 전용 연결·차단 저널 워커는 각각 확인한다. 안전한 응모 조건이 없으면
  그 항목을 미완료로 기록하고 일반 제한기 결과로 대체하지 않는다.
- 시험 직전 bootstrap의 `redis_failover_test_expires_at`에 현재부터 최대 60분 이내의
  미래 UTC RFC3339 Z 시각을 전달한다. 전체 plan이 전용 inline policy 한 개만 생성하는지
  확인하고 적용한다. 대상 그룹 한 개·서울·TestFailover 한 동작만 허용한다.
  IAM은 노드 그룹이나 호출 횟수를 제한하지 않으므로 실행 기록으로 한 번을 통제한다.
- 그룹·노드 그룹 `0001`·역할을 재조회하고 실행 의도와 UTC 시각을 private 지속 파일에
  기록한 뒤 아래 호출을 정확히 한 번 실행한다. timeout이나 응답 유실도 재호출하지 않는다.

```bash
AWS_MAX_ATTEMPTS=1 aws elasticache test-failover \
  --profile getddo-gyeongju --region ap-northeast-2 \
  --replication-group-id getddo-dev-redis --node-group-id 0001 \
  --cli-connect-timeout 5 --cli-read-timeout 30 --no-cli-pager
```

AWS 상태는 약 15초마다, 앱은 A/C 교대로 5초당 한 요청 이하로 관찰한다.
요청 시작·수락·승격 이벤트·첫 앱 오류·양쪽 첫 정상 제한 처리 시각을 각각 기록한다.
전환 중 Redis 오류가 관측되면 기존 fail-open과 업무 검증을 확인한다.
오류를 못 봤다는 사실을 항상 무중단으로 표현하지 않는다.

3분 내 기능 복구가 없으면 추가 시험 호출을 중지하고 이벤트·DNS·TLS·AUTH·Lettuce와
응모 전용 연결·실효 timeout·로그를 조사한다. 5분이 지나도 한쪽 제한 기능이 돌아오지
않으면 자동 복구 시험 실패로 기록하고 운영 복구를 우선한다. 앱 재시작이 필요하면
재시작 없는 시험은 실패다. 반복 failover·노드 삭제·SG 차단으로 복구하지 않는다.
새 5xx가 지속되거나 ALB unhealthy면 시험 요청을 중지한다.

기능 복구 후 A/C 양쪽의 공유 제한·429·TTL 해제를 다시 확인한다. 같은 컨테이너와 재시작 0,
응모 Lua 평가 성공과 시험 키, 합성 저널 ID의 DB 저장 및 ack를 대조한다.
DB 저장 ID가 한 건인지 확인하며 backlog 0이나 경고 없음만으로 전용 풀 회복을 판정하지 않는다. replica 준비까지 확인한 뒤 1분 지표 세 구간과 소량 기능
확인으로 3분 관찰한다. 서비스 복구와 HA 복구는 별도로 기록한다.

종료 즉시 만료 입력을 제거하고 전체 bootstrap plan/apply로 시험 권한을 삭제한다.
60분은 최대 권한 수명이며 기다리는 시간이 아니다. 실효 권한 철회와 prod/bootstrap
전체 plan 0을 확인한다. 카운터는 TTL로 만료시키고 합성 ID만 정리한다.
FLUSHALL·FLUSHDB·전역 KEYS·글로벌 저널 인덱스 삭제는 사용하지 않는다.

## 검증 명령

프로젝트 CI와 같은 Python 3.12 및 Terraform 1.16 환경에서 실행한다.

```bash
python scripts/validate.py terraform
python scripts/validate.py scripts
python scripts/validate.py documents
```

## 근거

- [AWS Multi-AZ와 자동 장애 전환](https://docs.aws.amazon.com/AmazonElastiCache/latest/dg/AutoFailover.html)
- [TestFailover API와 완료 이벤트](https://docs.aws.amazon.com/cli/latest/reference/elasticache/test-failover.html)
- [provider 6.68.0 복제 그룹 갱신 구현](https://github.com/hashicorp/terraform-provider-aws/blob/v6.68.0/internal/service/elasticache/replication_group.go)
- [Redis 비동기 복제의 유실 한계](https://redis.io/docs/latest/operate/oss_and_stack/management/replication/)
