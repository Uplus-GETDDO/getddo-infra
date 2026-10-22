# GD-299 Prod 모니터링 실제 검증 — 2026-10-22

사용자 승인으로 적용·설치·정상 병합·운영 배포를 진행했다. 현재 로그인은 기존 `getddo-dev` 프로필의 `gyeongju`이며 새 사용자/로그인 프로필을 만들지 않았다. root 관리 프로필은 IAM bootstrap과 보호된 루트 태그 소유권 정리에만 사용했다. 원본 dirty 작업본은 보존했다.

## 실제 적용

- 모니터링 독립 State: `monitoring/terraform.tfstate`. EC2·암호화 root/data EBS·SG·IAM 등 신규 15개 자원 생성. 기존 A/C·DB·ALB 인프라를 교체하지 않음.
- 모니터: `i-0b6ff5465f490af62`, t3.medium, 독립 30 GiB gp3 data EBS. 기존 Prod의 legacy 이름과 `dev/terraform.tfstate`는 유지.
- Prometheus/Grafana 고정 이미지, A/C/모니터 node_exporter 설치. 기존 CloudWatch 로그·지표 유지.
- Grafana 초기 관리자 비밀번호는 전용 `/getddo/monitoring/grafana/admin-password` SecureString에 등록. 값은 출력·PR·Git·Terraform State에 포함하지 않음. CLI 전달 임시 파일은 0600으로 만들고 즉시 삭제.
- `gyeongju`의 사전 권한 적용 결과와 Staging 권한 범위는 [별도 기록](staging-operator-permissions.md). 실제 Staging 서버·DB·부하는 생성/실행하지 않음.

## 배포와 DB 사전 조건

- [BE 승격 PR #162](https://github.com/Uplus-GETDDO/getddo-be/pull/162), `skip-review` 적용. 정상 승인·필수 CI를 통과하고 병합. main SHA `df0b1c8bf73b95caf02e7ef8e442fa0757129669`.
- [main Build](https://github.com/Uplus-GETDDO/getddo-be/actions/runs/39775170130) 전체 빌드·테스트·이미지 성공. 로컬 Docker 통합 테스트는 요청에 따라 실행하지 않음.
- [Container CD](https://github.com/Uplus-GETDDO/getddo-be/actions/runs/39776787035) rolling A→C 배포·버전 발행 모두 성공. 실제 A/C 이미지 revision이 위 SHA와 일치하며 ALB 두 대상 모두 healthy. 발행 버전 v0.1.6.
- V030은 기존 abuse_cases가 비어 있어야 적용되므로 병합 전 실제 조회로 10건을 발견해 중단했다. 사용자에게 설명하고 **삭제 후 10건 재생성 지시를 받은 뒤에만** 기존 10개 ID를 백업하고 트랜잭션으로 정확히 삭제, 0행 재확인 후 병합했다. 다른 테이블은 삭제하지 않음.
- 기존 10건의 V029 테이블 덤프와 참조 정보는 A 서버의 root 전용 `/var/lib/getddo/maintenance/gd299-v030/`에 보관. 이 덤프를 V030 DB에 그대로 실행하는 것은 별도 스키마 복구 검토 없이 허용하지 않음.
- 실제 Flyway V030 성공 후 신규 합성 ENTRY 10건 생성. 이전 사용자/이벤트 참조를 재사용하고 모두 만료된 이력으로 만들어 활성 차단을 만들지 않음. 합성 표시를 evidence에 기록. 실제 관리자 목록 totalElements=10·상세 HTTP 200 확인. 원본 덤프·참조 UUID·응답 개인정보는 Git/PR에 남기지 않음.

## 실제 검증 결과

| 항목 | 결과 |
| --- | --- |
| API target A/C | 둘 다 up |
| node A/C/monitoring + Prometheus self | 네 대상 up, 총 여섯 target 정상 |
| 지표 | A/C HTTP histogram·p95·Hikari·Tomcat·JVM 실제 유한 값 조회 |
| Grafana | Prometheus·CloudWatch metrics/logs health OK, 대시보드 2개. 실제 Prometheus A/C frame과 CloudWatch EC2 CPU 숫자 frame 반환 |
| 관리 포트 | 앱 8080에서 prometheus/env/heapdump/configprops 모두 404. 외부에서 A/C 9091/9100 및 모니터 3000/9090/9100 접근 차단 |
| 접속 | Grafana는 host 127.0.0.1:3000만 게시. gyeongju 실제 SSM 포트 전달 후 로컬 health에서 database=ok 확인 |
| 저장/재부팅 | 정확한 독립 EBS UUID/volume marker 검사 통과. 실제 모니터링 서버 재부팅 후 Docker/stack/exporter 자동 시작, 비밀번호 0400 UID/GID 472로 재생성, Grafana database=ok |
| 백업·복원 | 새 모니터의 작은 초기 데이터와 디스크 여유를 확인 후 offline TSDB/Grafana 백업·checksum·빈 디렉터리 복원·기존 소유권 복구 성공. 원본 복사본 보존. 복원/재부팅 이전 실제 과거 node 샘플 조회 성공 |
| 기존 서비스 | A/C를 의도적으로 장애 상태로 만들지 않았으며 데이터 무단 삭제·DB 강제 롤백·EBS 강제 분리 없음 |

대시보드는 API/데이터소스의 실제 query frame으로 검증했다. 브라우저 스크린샷/모든 패널의 육안 검수 또는 부하 시험 완료로 표현하지 않는다. 의도적인 디스크 분리·비밀번호 누락 장애는 대역 검사만 있고 이번 실제 시험에서 수행하지 않았다. 자동 외부 백업·알림·모니터 HA는 이 작업에 포함하지 않았다.

## 실행 중 수정한 문제

- Grafana tmpfs flow-list의 옵션 쉼표가 여러 mount로 파싱되어 최초 실행 실패. 전체 문자열을 인용하고 CI가 비절대 mount를 거절하게 했다. 유효 단일 문자열도 허용하며 daemon 없는 회귀 통과.
- 설치 스크립트의 IMDS 토큰은 curl argv 대신 stdin 헤더로 전달.
- 기존 EC2 태그 변경과 새 Staging 종료 권한 조합을 보호하기 위해 비Staging의 분류 태그 변경/삭제·키 없는 전체 태그 삭제를 거부. 실제 Prod create-tags/delete-tags dry-run 모두 UnauthorizedOperation. 아무 태그도 변경/삭제하지 않음.
- EC2 volume_tags는 독립 볼륨 연결 후 전체 볼륨의 태그를 합쳐 읽으므로 재적용하면 data EBS Name과 충돌. 생성 시 root 태그는 유지하고 `ignore_changes=[volume_tags]`로 기존 볼륨 일괄 덮어쓰기를 중지. 데이터 디스크 태그는 aws_ebs_volume.data가 계속 관리. 실제 monitoring full plan No changes로 검증.
- 배포 중 Prod plan의 A target 재등록 차이는 일시적이었다. 배포 후 관리 자원 변경 없음. gyeongju 조회의 최종 prod plan exit 2는 caller ARN 진단 output의 root→gyeongju 차이만이며 실제 자원 변경이 아님. 이 output 때문에 업무 자원을 재적용하지 않음.

원본 SSM 배포 로그 조회는 민감 설정 포함 가능성으로 자동 승인 검토에서 거부됐으므로 사용하지 않았다. 상태 메타데이터·종료 코드와 별도 비밀값 비출력 점검으로 확인했다. 원본 plan·State·비밀번호·DB 덤프는 첨부하지 않는다.

## 접속

AWS CLI와 공식 Session Manager plugin을 설치한 환경에서 gyeongju로 로그인한 기존 프로필을 사용한다.

```sh
aws ssm start-session --profile getddo-dev --region ap-northeast-2 \
  --target i-0b6ff5465f490af62 \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["3000"],"localPortNumber":["3000"]}'
```

터널을 유지하고 http://localhost:3000 접속. Grafana 사용자 admin. 초기 비밀번호는 AWS 콘솔 Parameter Store의 전용 SecureString에서 본인이 확인한다. 값은 채팅·이슈·화면 공유에 붙이지 않는다. 이번 검증용 임시 plugin/13000 터널은 영구 설치나 상시 프록시가 아니다.
