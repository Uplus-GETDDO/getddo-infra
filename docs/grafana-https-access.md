# GD-302: 운영 Grafana HTTPS 접속

이 문서의 접속 경로와 실시간 대시보드는 모니터링 서버·CloudFront·ALB가 실행 중일 때만 유효합니다. 서버 종료 후에도 [구축·지표 수집 검증 기록](monitoring-runtime-verification.md)과 [대시보드 구성](grafana-dashboard-guide.md)은 저장소에서 확인할 수 있습니다.

기존 CloudFront 주소의 `/grafana/`를 브라우저에서 열고 기존 `admin` 계정으로 로그인한다. 비밀번호는 서울 Parameter Store의 `/getddo/monitoring/grafana/admin-password` SecureString에서 본인이 확인한다. 값을 Git·PR·채팅에 붙이지 않는다. 이 설정은 기존 Grafana DB와 admin 비밀번호를 초기화하지 않는다.

## 구성과 소유권

- `terraform/prod/cloudfront.tf`: `/grafana`, `/grafana/*` 두 경로만 캐시 없이 기존 private ALB로 전달. Grafana는 Origin/Host 일치를 검사하므로 AllViewer로 원래 Host·쿠키·쿼리·로그인 요청을 전달한다. 기존 API의 AllViewerExceptHostHeader와 이미지 캐시는 유지한다.
- `terraform/monitoring/grafana-access.tf`: 기존 내부 ALB/리스너/SG를 조회하고 Grafana 전용 TG·대상·경로 규칙(priority 20)과 SG 규칙만 소유한다. ALB 기본 동작이나 API TG를 변경하지 않는다. 3000은 ALB SG에서만 허용한다. Prometheus 9090과 exporter 9100을 공개하지 않는다.
- `observability/compose.yaml`: Grafana는 모니터 사설 IP:3000에 바인딩한다. `/grafana/` 외부 HTTPS root URL·Secure/HttpOnly 세션 쿠키·익명/회원가입/Basic 인증 비활성화를 사용한다. 아이디/비밀번호 로그인 폼은 유지한다. Prometheus·EBS·기존 admin 계정은 보존한다.

HTTPS는 기존 CloudFront에서 종료하고 VPC 내부 ALB→Grafana 구간은 기존 구조와 같은 HTTP다. 모든 구간 TLS를 구현한 구성은 아니다. WAF는 추가하지 않는다. Grafana 로그인 보호가 공개 접근의 인증 경계다. 비밀번호 무차별 시도 방어는 유지한다.

## 적용 순서

1. 기존 로그인 주체·원격 main·실제 자원·리스너 priority 20 사용 여부를 확인한다. 개인 미커밋 파일은 보존한다.
2. monitoring 입력 `cloudfront_distribution_id`에 기존 Prod distribution ID를 전달한다. 이 값은 비밀번호가 아니지만 실제 tfvars는 Git 밖에서 관리한다.
3. mock/Compose/스크립트 검증, 두 root의 전체 plan을 확인한다. 생성 예정은 monitoring의 SG 규칙 2개·TG·attachment·listener rule 총 5개이며, prod는 기존 distribution의 경로 2개 추가뿐이다. 삭제/교체가 있으면 적용하지 않는다. 실제 값은 plan으로 다시 확인한다.
4. 모니터의 기존 `/opt/getddo-monitoring/observability/compose.yaml`을 권한 제한 복구 위치에 백업한다. 기존 대시보드·Prometheus target 파일·데이터 디렉터리는 덮어쓰거나 지우지 않는다.
5. `terraform output -json grafana_runtime_config`의 비밀 아닌 두 값을 모니터의 `/opt/getddo-monitoring/observability/.env`에 기록한다. `GRAFANA_BIND_IP`는 모니터의 사설 IPv4, `GRAFANA_ROOT_URL`은 기존 HTTPS 주소 + `/grafana/`다. `.env`는 Git 제외, root 소유 0600. 비밀번호는 이 파일에 쓰지 않는다.
6. 새 Compose를 복사하고 `docker compose config --quiet`, 기존 EBS guard 확인 후 `docker compose up -d --no-deps grafana`로 Grafana만 재생성한다. Prometheus는 재시작하지 않는다.
7. monitoring Terraform plan 적용 후 TG healthy 확인, prod의 검토한 plan 적용 후 CloudFront Deployed 확인.
8. 실제 공개 HTTPS 로그인·로그아웃·미인증 API 거부·잘못된 Origin 거부·쿠키·대시보드/쿼리와 기존 API 정상 여부를 검증한다. 비밀번호·세션 쿠키·토큰은 파일 권한을 제한하고 출력하지 않는다. 로컬 Docker 통합 테스트는 하지 않는다.

최초 설치/재구축에서도 5번의 `.env`를 준비한 뒤 monitoring systemd 서비스를 시작해야 한다. 재부팅 시 이 파일과 기존 데이터 EBS를 사용한다. admin을 새로 만들거나 초기 비밀번호를 재설정하지 않는다.

## 복구

문제가 있으면 우선 CloudFront의 Grafana 경로 변경과 ALB Grafana 경로를 Terraform으로 원복해 공개 진입을 차단한다. 기존 API 기본 동작은 유지한다. 모니터에 백업한 Compose를 복원하고 Grafana만 재생성하면 기존 로컬 접속 설정으로 돌아간다. 새 `.env`는 이전 Compose에서 사용하지 않으므로 비밀번호/DB 복원은 필요 없다. 재적용 전 전체 plan을 확인한다. 데이터 볼륨이나 Grafana DB를 삭제하지 않는다.

## 참고

- [Grafana 하위 경로·프록시 설정](https://grafana.com/tutorials/run-grafana-behind-a-proxy/)
- [CloudFront 관리형 Origin Request Policy](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/using-managed-origin-request-policies.html)
- [기존 구축/백업 검증 기록](monitoring-runtime-verification.md)

## 실제 적용·검증 — 2026-10-22

- `gyeongju`로 monitoring 5개 생성, prod CloudFront 1개 수정 적용. 삭제/교체 없음. 두 root 전체 재계획 모두 `No changes`.
- 기존 모니터에서 Grafana만 재생성했고 기존 admin 비밀번호·DB·Prometheus 데이터/프로세스는 보존했다. ALB Grafana target healthy, CloudFront Deployed.
- 외부 HTTPS 로그인 페이지/health·기존 API 문서 HTTP 200. 기존 admin으로 로그인 성공, 대시보드 2개 및 공개 경로를 통한 Prometheus 실제 쿼리에서 6개 target up 확인.
- 비로그인 user API 401, 세션 쿠키 Secure/HttpOnly 및 `/grafana` 경로 확인, 외부 Origin POST 403, 로그아웃 후 기존 쿠키 재사용 401.
- 모니터 SG 3000 인바운드는 ALB SG만 허용하고 IPv4/IPv6 CIDR 허용 없음. Prometheus 9090을 게시하지 않는다.
- Terraform mock: bootstrap 6개·prod 21개·monitoring 4개 통과. 스크립트 테스트 14개와 daemon 없는 Compose/Prometheus/14개 PromQL 검증 통과. 로컬 Docker 통합 테스트는 실행하지 않았다.
- 변경 파일을 실제 Grafana·DB·Redis 비밀번호와 메모리에서 대조하고 AWS access key/private key 패턴도 검사했다. 실제 비밀값, `.env`, 자격 증명, State/plan은 Git/PR에 포함하지 않았다. 비밀번호·세션 쿠키는 검증 출력에 남기지 않았다.
- 일반 접속은 HTTPS `/grafana/`를 사용한다. 전체 패널의 화면 검수와 부하 시험은 진행하지 않았다.
