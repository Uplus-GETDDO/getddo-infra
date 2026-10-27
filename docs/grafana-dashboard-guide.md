# 운영 대시보드 읽는 순서

[Google SRE의 Four Golden Signals](https://sre.google/sre-book/monitoring-distributed-systems/)인 트래픽·지연·오류·포화도를 기존 `GETDDO Prod — API and hosts` 상단에 배치했다.

| 위에서 볼 순서 | 의미와 해석 |
| --- | --- |
| 트래픽: API 요청/초 | 선택한 A/C의 최근 5분 평균 요청률. ALB 상태 검사 `/v3/api-docs`, Swagger, Actuator는 제외한다. |
| 지연: 성공 요청 p95 | 성공(2xx/3xx) 요청의 95%가 이 시간 이내에 처리되었다는 뜻. 선택한 서버의 histogram을 합쳐 계산한다. 오류 요청 지연은 아래 별도 선으로 본다. |
| 오류: 5xx 비율 | 현재 수집 정상(up=1)인 API의 전체 요청 중 서버 오류 비율. 4xx와 HTTP 200으로 반환된 업무상 실패는 제외한다. |
| 포화도: 가장 바쁜 DB 풀 | A/C DB 연결 사용률의 최댓값. CPU·메모리·디스크·Tomcat·pending을 함께 확인한다. |
| API 수집 상태 | A/C 각각 1이면 Prometheus 수집 성공, 0이면 수집 실패. |

`server` 선택은 기존 A/C/monitoring 필터를 사용한다. API 지표는 A/C만 있고, 호스트 지표는 monitoring도 있다. monitoring만 선택했을 때 API 카드가 비는 것은 정상적인 범위 차이다.

## 포화도 그래프

- 메모리: `1 - MemAvailable / MemTotal`. 캐시 전체를 고갈로 오해하지 않도록 사용 가능한 메모리를 기준으로 한다.
- 디스크: 파일시스템별 `1 - avail / size`. 루트 및 모니터링 데이터 디스크를 보며 가상·Docker 파일시스템은 제외한다.
- DB 연결: `active / max`. pending이 계속 늘거나 최대 연결에 가까우면 대기·연결 획득 시간도 확인한다. raw 그래프에 active/pending/idle/max를 함께 표시한다.
- Tomcat: `busy / configured max`. 최대치는 설정된 스레드 수를 사용한다. raw 그래프에 busy/current/max를 함께 표시한다.
- 사용률 그래프의 색상은 80%/90%에서 바뀐다. 알림 조건은 별도 설정이 필요하다.

## 빈 값과 0 구분

- 현재 수집 정상(up=1)이고 요청도 발생했지만 5xx 시계열만 없으면 오류율 0%로 표시한다. 수집 실패/누락 서버는 최근 요청 샘플이 남아 있어도 분자와 분모에서 동일하게 제외한다. 일부 서버 수집이 끊기면 남은 정상 수집 서버의 오류율이므로 A/C 수집 상태를 반드시 함께 본다.
- 요청이 0이면 오류율의 분모가 없으므로 빈 값이다. 요청/수집 지표가 없을 때도 0%로 채우지 않는다.
- 성공 요청이 없으면 p95는 정의되지 않는다. `—` 또는 `No data`가 표시되면 API 수집 상태와 트래픽을 확인한다.
- DB·스레드 최대치가 없거나 0이면 비율을 표시하지 않는다. Infinity나 임의 0으로 채우지 않는다.
- 요약은 최근 5분, CPU는 최근 2분의 rate를 사용한다. 짧은 피크와 소량 트래픽은 원본 그래프·로그와 함께 확인한다.


이 대시보드는 앱/호스트 내부 지표를 보여준다. CloudFront/ALB에서 앱에 도달하기 전에 발생한 오류는 기존 CloudWatch 대시보드에서도 확인해야 한다.

## 실제 검증 — 2026-10-22

- 기존 dashboard UID를 유지하고 provisioning 파일만 원자적으로 교체했다. 기존 JSON은 모니터의 root 전용 유지보수 경로에 백업했다. 컨테이너 재시작이나 Terraform apply는 하지 않았다.
- 운영 Prometheus에서 전체 26개 표현식 실행 성공. 새 포화도 지표는 유한 값이며 DB/Tomcat은 A·C 양쪽을 포함한다.
- HTTPS 로그인 후 Grafana API에서 갱신된 26개 패널·네 핵심 지표 카드·초/퍼센트 단위와 실제 사용률 쿼리를 확인했다. A/C 수집 상태 둘 다 1. 기존 CloudWatch 대시보드도 유지했다.
- 현재 dashboard 표현식으로 Promtool 합성 입력 12개 시나리오를 실행했다. 요청 없음·미수집·오류 없음·5xx 비율·상태 검사 제외·0 분모·성공 지연 분리·DB/Tomcat 최대치 및 MemAvailable 계산·수집 실패/누락/부분 실패 처리이 기대 결과와 일치했다.
- 스크립트 테스트 15개, daemon 없는 Compose/Prometheus 설정·26개 PromQL 검사를 통과했다. 텍스트 패널은 허용하지만 targets가 없는 일반 그래프는 실패하며 정상 그래프의 쿼리 검증이 유지되는 회귀 검사도 포함한다.
- 변경 파일에 실제 Grafana·DB·Redis 비밀번호가 없는지 메모리에서 대조하고 access key/private key 패턴을 검사했다. 비밀값·쿠키·State/plan·실제 env를 Git·PR·검증 출력에 포함하지 않았다.
- 전체 패널의 화면 검수와 스크린샷 비교는 진행하지 않았다.

계산 시나리오는 `observability/grafana/query-tests.yml`에 남겼고 기존 CI의 Promtool 검사에서 실행한다. fixture 표현식이 실제 대시보드와 달라지면 실패시켜 오래된 쿼리만 통과하는 것을 방지한다. 수집 실패에 최근 샘플이 남는 사례는 기존 쿼리의 실패를 재현한 뒤 수정·검증했다.
