# GD-301: gyeongju 사전 운영 권한

같은 IAM 사용자 `gyeongju`와 기존 CLI 프로필 `getddo-dev`를 사용한다. 새 사용자·로그인 프로필을 만들지 않는다. 기존 운영 그룹의 관리형 정책 연결이 10개이므로 `getddo-staging-terraform-gyeongju` 그룹에 Staging 정책 5개를 연결하고 같은 사용자를 추가한다. 기존 그룹·정책은 보존한다.

## 미리 허용한 범위

| 대상 | 추가 권한 범위 |
| --- | --- |
| 모니터링 | 기존 bootstrap의 State/잠금·EC2/EBS·IAM·SSM·전용 비밀번호 등록 권한을 실제 적용. 사용자에게 Grafana 전용 SecureString 1개의 읽기도 허용 |
| API 2대 | 서울, `getddo-staging-api-a/c`, t3.small, IMDSv2 필수 |
| Locust | 서울, `getddo-staging-locust`, t3.medium/t3.large, IMDSv2 필수 |
| 디스크·정리 | 암호화 gp3 생성, `Environment=staging` 디스크/인스턴스/SG의 운영·정리. 기존 VPC·서브넷은 재사용하며 삭제 권한을 추가하지 않음 |
| DB | `getddo-staging-*` RDS·서브넷 그룹·테스트 스냅샷, MySQL 8.4 기본 파라미터/옵션, private DB 생성·수정·복원·삭제 |
| Redis | `getddo-staging-*` 복제 그룹·클러스터·서브넷 그룹 생성/수정/삭제, 기본 Redis/Valkey 파라미터. 실제 생성은 단일 노드 계획 |
| ALB | `getddo-staging-*` ALB·대상 그룹·리스너·규칙·인증서 연결 및 정리. 실제 ALB는 내부 HTTPS로 구현해야 함 |
| State·산출물 | 기존 State 버킷의 `staging/terraform.tfstate`와 잠금, 기존 배포 버킷의 `staging/*`. State 본문 삭제는 허용하지 않음 |
| 설정·접속 | `/getddo/staging/*` Parameter Store, Staging 태그 서버 SSM·기존 승인 문서·포트 전달, `/getddo/staging/*` 로그 |
| TLS | 서울 ACM의 `Environment=staging` 인증서, `staging.*` 또는 `*.staging.*` 이름과 DNS 검증만 요청 가능. 실제 도메인을 생성/선택한 것은 아님 |
| runtime IAM | `getddo-staging-api/locust` 역할·프로필만 관리/EC2 전달. 역할 생성 시 고정 `GetddoStagingRuntimeBoundary` 필수; 경계 편집/제거 권한 없음 |
| 자기 권한 확인 | 본인 사용자·운영 그룹·정책 메타데이터 조회 및 본인 정책 시뮬레이션 |
| 공용 모니터 | 향후 같은 모니터 역할이 Staging 로그도 읽도록 선언. 앱/DB 비밀값 읽기는 추가하지 않음 |

runtime 경계는 Staging 파라미터·로그·산출물, SSM agent 연결, 앱/호스트 CloudWatch 지표 전송만 상한으로 허용한다. 경계 자체가 실행 역할에 권한을 부여하지는 않는다. 런타임 역할 생성 시 필요한 실제 inline 정책도 별도 선언해야 한다. Staging Parameter Store는 기본 AWS 관리 SSM 키, EBS/RDS는 기존 AWS 관리 키 사용을 전제로 한다. 새로운 고객 관리 KMS 키·다른 인스턴스 종류·다른 리전은 이 권한 범위가 아니다.

기존 운영 정책 일부는 서울 네트워크 변경 등을 이미 넓게 허용한다. 이번 추가분이 기존 사용자의 전체 권한을 격리한다고 주장하지 않는다. 기존 권한을 축소하는 리팩터링은 하지 않는다.

## 아직 만들지 않은 것과 검증 한계

이 변경은 IAM 사전 준비다. Staging Terraform workload·서버·ALB·DB·Redis·Locust·합성 데이터·실제 부하를 만들거나 실행하지 않는다. `Environment=staging`과 위 이름을 이후 Terraform에 명시해야 한다. 모니터/Prod와 State를 공유하지 않는다.

현재 계정 조회에서 Route53 hosted zone과 서울 ACM 인증서가 없었다. Route53 hosted zone 생성이나 운영 DNS 변경 권한을 임의로 추가하지 않는다. 실제 도메인 소유권·외부 DNS의 인증 레코드 등록은 도메인 설정 시 확인한다. ACM 자체 권한을 부여해도 외부 DNS 접근 권한이 생기지는 않는다. 현재 계정의 CloudWatch 조회 권한은 이미 있으므로 중복 전권을 부여하지 않는다.

검증은 정책 문법/보안 검사, Terraform mock, 전체 bootstrap plan과 적용, gyeongju의 실제 모니터링 plan·자기 그룹 조회·대표 허용/거부 시뮬레이션으로 구분한다. 아직 없는 Staging workload의 전체 생성·삭제 성공을 검증했다고 표현하지 않는다. 데이터 쓰기를 일으키는 테스트 자원 생성으로 권한을 시험하지 않는다. 실제 구현이 확정되면 해당 Terraform plan으로 다시 검증한다.

ACM 조건은 [AWS IAM action reference](https://docs.aws.amazon.com/service-authorization/latest/reference/list_acm.html)를 기준으로 작성했다. 복수 도메인은 모든 값 제한과 Null 검사를 함께 적용한다.

## 실행 결과 — 2026-10-22

- 기존 모니터링 권한: root 관리 프로필의 Terraform bootstrap 적용으로 1개 생성·4개 수정·삭제 0. 이후 실제 `gyeongju`의 전체 monitoring plan 성공(15개 생성 예정).
- 이 변경의 사전 권한: bootstrap 전체 plan 검토 후 12개 IAM 자원 생성·2개 수정·삭제 0 적용. 별도 Staging 그룹/정책/고정 runtime 경계와 기존 사용자 멤버십·전용 Grafana 비밀번호 읽기만 변경. 서버·DB·DNS·부하는 생성/실행하지 않음.
- 적용 후 bootstrap 전체 plan: No changes. 기존 사용자와 실제 그룹 멤버십을 조회했고 기존 그룹을 보존함.
- Access Analyzer: 신규 6개 정책과 기존 수정 파라미터 정책 모두 findings 0. Terraform mock: bootstrap 4개·monitoring 3개 통과. 스크립트 검사 및 테스트 13개 통과. 로컬 Docker 통합 테스트는 실행하지 않음.
- `gyeongju` 본인 `SimulatePrincipalPolicy`: 대표 12개 허용/거부 결과 일치. Staging 잠금·설정·DB 정리·경계가 있는 역할·서버 정리·제한된 인증서 허용; State 본문 삭제·Prod DB 삭제·경계 없는 역할·경계 제거·운영 도메인/빈 도메인 인증서 요청 거부. 시뮬레이션에서 사용한 가상 리소스는 생성하지 않음. 실제 Staging 생성/삭제 권한의 전체 실측을 대신하지 않음.
- 원본 plan·로그는 Git 밖 권한 제한 경로에 보관하며 정책/증거에 비밀값을 포함하지 않음.
