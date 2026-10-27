# CloudFront API 요청 제한

CloudFront 진입점에서 `/api/`로 시작하는 요청을 접속 IP별로 합산한다. OPTIONS는 제외하며,
기준은 최근 60초에 600회다. IPv4와 IPv6를 지원하고 사용자 제공 X-Forwarded-For로 집계하지
않는다. URI는 URL_DECODE 다음 NORMALIZE_PATH로 검사하고, 변환한 경로가 `/api/`로 시작하는 요청을 집계한다.
전달 URL은 재작성하지 않는다. 공유 네트워크의 사용자는 같은 IP로 합산될 수 있다. AWS는 요청량을 추정하므로
요청량 추정과 규칙 반영 지연 때문에 차단 순번과 해제 시점은 달라질 수 있다.

2026-10-25 기존 CloudFront에 적용하고 실제 차단·자동 해제·로그 저장과 최종 설정을 확인했다.
[실측 결과](waf-rate-limit-test-results.md)에서 케이스별 방법과 관측값을 확인할 수 있다.
기존 백엔드 제한도 유지한다. EC2의 직접 관리 경로는 CloudFront WAF 보호 범위 밖이다.

## 응답과 로그

최종 구성은 `waf_mode=block`, `waf_log_mode=blocked`, `waf_probe=null`이다.
요청 제한에 해당하면 WAF가 앱에 전달하기 전에 HTTP 429를 반환한다.

```json
{"success":false,"code":"COMMON-006","message":"요청이 너무 많습니다. 잠시 후 다시 시도해 주세요.","data":null}
```

고정 공개 오류에만 `Access-Control-Allow-Origin: *`를 붙인다. `Retry-After`와
credentials 허용 헤더는 보내지 않으며 정상 API의 CORS 정책은 유지한다.

WAF와 로그는 `us-east-1`에서 관리하고 기존 서버·ALB·DB는 `ap-northeast-2`에 둔다.
로그 그룹 `aws-waf-logs-getddo-dev-api`는 7일 보관한다. 최종 로그 필터는 기본 DROP,
`action=BLOCK`만 KEEP이다. WAF·로그 그룹·연결은 계속 유지하고 오래된 기록만 만료한다.
ALLOW는 WAF 통과 여부다. 앱의 처리 결과는 API 응답과 앱 로그에서 확인한다.

쿼리 문자열과 authorization, proxy-authorization, cookie, x-api-key, x-user-id,
x-user-role, x-user-membership 헤더는 마스킹한다. 요청 표본 보기는 ACL과 규칙 모두 끈다.
본문 전체는 별도로 수집하지 않는다. 실제 사용자 토큰을 검증 요청이나 문서에 넣지 않는다.

## 적용 순서

1. 실제 AWS 계정·주체, 기존 CloudFront 배포와 ACL 연결 상태, 기존 입력·원격 State 잠금을 확인한다.
   전체 plan에서 무관한 실행 변경이 있으면 중단한다. 기존 ACL을 임의로 덮어쓰지 않는다.
2. 사용하지 않는 ALB 리스너 우선순위 10~19를 확인하고 UUID를 만든다. `waf_probe`에
   `run_id`와 `priority`를 입력하면 GET 전용 `/api/__waf_probe/<UUID>` 경로가 만들어진다.
   ALB가 `waf-probe:<UUID>`를 고정 응답하므로 반복 요청은 앱과 DB에 도달하지 않는다.
3. `count/all`은 초기 연결과 ALLOW 로그·마스킹 확인에만 짧게 사용한다. 이때도 600/60초는 유지한다.
   CountedRequests는 모든 요청 수가 아니며 한도에 해당해 COUNT 처리된 요청 수다.
4. 전체 저장 plan으로 `block/blocked`를 적용하고 실제 설정을 재조회한 뒤 아래 도구를 실행한다.
5. 다른 IP, 정상 업무 읽기, 제외 메서드·이미지, 실제 로그·지표를 따로 검증한다.
6. `waf_probe=null`로 임시 리스너 규칙만 삭제한다. prod와 bootstrap 전체 plan의 실행 변경 0,
   실제 Block·600/60초·IP·blocked 필터·보관 7일, 요청 발생 프로세스 종료를 확인한다.

운영 권한은 기존 CloudFront·observability 정책을 확장한다. WAF는 전용 이름의
`arn:aws:wafv2:us-east-1:942466928325:global/webacl/getddo-dev-api-waf/*`로,
로그 접근은 전용 로그 그룹과 그 하위 스트림으로 제한한다. 리소스 단위 제한을 지원하지 않는
ListWebACLs, CreateLogDelivery, DeleteLogDelivery, PutResourcePolicy,
DescribeResourcePolicies, DescribeLogGroups 등은 `Resource: *`를 사용하되
`us-east-1`과 동일 계정 조건을 적용한다. PutLoggingConfiguration의 로그 목적지도
전용 로그 그룹으로 제한한다. 기존 다른 자원·리전의 권한과 로그 전달 정책은 보존한다.

## 제한된 probe 검증

Python 3.12와 기존 로그인에 연결된 AWS CLI를 사용한다. 도구는 AWS 설정을 수정하지 않는다.
`WAF_BASE_URL`, `WAF_PROBE_PATH`, ACL·배포 식별자는 실제 Terraform 출력과 AWS 조회를
대조하고, `WAF_FRONTEND_ORIGIN`에는 실제 프론트의 HTTPS Origin을 넣는다.
증거 디렉터리는 아직 존재하지 않는 경로를 지정한다.

```bash
umask 077
python3.12 scripts/verify-waf-rate-limit.py \
  --base-url "$WAF_BASE_URL" --probe-path "$WAF_PROBE_PATH" \
  --web-acl-id "$WAF_ACL_ID" --distribution-id "$WAF_CF_ID" \
  --profile getddo-gyeongju --region us-east-1 \
  --origin "$WAF_FRONTEND_ORIGIN" --evidence-dir "$WAF_PRIVATE_DIR/evidence"
```

HTTP 전에 고정 HTTPS 배포·정상 배포 상태·실제 ACL 연결, 600/60초/IP/Block,
경로·메서드 조건, 오류 응답, 표본 비활성화, 로그 목적지·필터·마스킹·7일 보관,
실제 ALB의 UUID 경로·GET·고정 본문·우선순위를 대조한다. 불일치는 요청 전에 중단한다.
AWS CLI의 바이너리 `SearchString` 출력은 base64를 해석해 대조한다.

| 동작 | 상한·통과 기준 |
| --- | --- |
| 기준 응답 | HTTP 200과 해당 UUID의 고유 본문 필수 |
| 높은 요청 | 전체 12회/초 이하, 동시 최대 2개, 최대 1,800회 또는 150초 |
| 개별 HTTP | timeout 3초, 자동 리다이렉트·재시도 없음 |
| probe 이탈 | timeout·401·404·5xx·고유 본문 불일치 등은 새 부하 중단 후 진행 요청 합류·미완료 기록 |
| 차단 관측 | 첫 유효한 429/COMMON-006 뒤 추가 높은 요청 중단, 이미 진행 중인 요청 합류 |
| 응답 계약 | 고정 JSON, CORS `*`, JSON Content-Type, Retry-After·credentials 헤더 없음 |
| 헤더 변경 | 같은 IP에서 문서용 X-Forwarded-For 한 번 변경 후 429 유지 확인 |
| 자동 해제 | 높은 요청 종료 후 10초 간격, 최대 5분, 200·고유 본문 3회 연속 |
| BLOCK 로그 | 같은 UUID 경로·GET·ApiIpRateLimit·BLOCK·429를 최대 5분 낮은 빈도로 조회 |
| 종료 | 진행 요청 합류 후 요약 저장, 설정 다시 조회 |

600회 한도를 낮추거나 상한에 도달한 부하를 자동 재실행하지 않는다. 기준 응답이 느리면 동시성을
늘리지 않는다. 헤더 변경 때 이미 해제됐다면 우회 성공이나 실패로 단정하지 않고 NOT_RUN을 남긴다.

종료 코드 0은 도구가 수행하는 필수 probe 검사 통과, 1은 응답 계약·자동 해제 실패,
2는 환경·전파·차단 미관측·로그 미확인 등 검증 미완료다.
`verification.json`의 browser, other_ip, normal_reads, options, image, initial_allow_redaction,
metrics, cleanup은 별도 증거가 없으므로 NOT_RUN이다. CORS와 429 JSON 응답 계약은
실제 Origin을 넣은 HTTP 요청으로 검사한다. 브라우저/UI 동작은 자동 검사 범위에 포함하지 않는다.

## 증거와 추가 검증

증거 디렉터리는 0700, 생성 파일은 0600이다. `run.json`, `requests.jsonl`,
`verification.json`, `waf-state.json`, `logging-state.json`, 실제 BLOCK 수신 시 `log-proof.json`을
저장한다. HTTP 본문·인증 헤더·Set-Cookie를 저장하지 않고 고정 본문과 계약의 일치 여부만 기록한다.
AWS 읽기 오류의 원문도 출력하지 않는다. 실패 시 생성되지 않은 파일을 증거로 인용하지 않는다.
원본 State·plan·실제 입력·인증정보는 공개하지 않는다.

초기 all 구간에는 가짜 민감 값으로 REDACTED를 확인한다. 주 시험 IP 차단 중에는 이미 존재하는
별도 서버에서 소수 GET을 보내고 WAF 로그의 clientIp가 다른지 확인한다. 헤더 변경과 다른 IP 요청은 각각 시험한다. 기존 정상 읽기 API는 합성 계정으로 각 3회 조회하며 개인정보 본문은
저장하지 않는다. 실제 BLOCK 로그와 BlockedRequests를 양성 증거로 확인하고, 누락된 지표를
실제 0으로 해석하지 않는다.

최종 상태는 임시 규칙 부재와 전체 plan 0을 별도 기록한다. 초기 ALLOW 로그는 필터 전환 후에도
보관 기간 동안 남을 수 있으므로 조회 시간을 구분한다. 로그 전송은 best effort여서 요청·로그·지표
건수가 정확히 같다고 요구하지 않는다. 실제 로그 수신이 없으면 성공으로 쓰지 않는다.

CloudWatch Logs Insights에서 `us-east-1`의 지정 로그 그룹을 조회한다.

```text
fields @timestamp, httpRequest.clientIp, httpRequest.httpMethod,
       httpRequest.uri, action, terminatingRuleId, responseCodeSent
| filter action = "BLOCK" and terminatingRuleId = "ApiIpRateLimit"
| sort @timestamp desc
| limit 50
```

WAF AllowedRequests에는 이미지·OPTIONS도 포함될 수 있다. 상세 로그 필터와 합계 지표 수집은
별개이며 외부 알림 수신처는 별도 운영 설정이다. 장애 시 Count로 전환해야 한다면 로그도 all로
함께 전환하고 조사 후 block/blocked로 복원한다. 제한 횟수·평가 시간은 바꾸지 않는다.

로컬 검사에는 가짜 AWS/HTTP 응답만 사용한다.

```bash
python3.12 -m unittest discover -s scripts -p test_verify_waf_rate_limit.py -v
```

- [AWS WAF 요청 제한](https://docs.aws.amazon.com/waf/latest/developerguide/waf-rule-statement-type-rate-based.html)
- [CloudWatch Logs 전달](https://docs.aws.amazon.com/waf/latest/developerguide/logging-cw-logs.html)
- [AWS CLI Web ACL 조회](https://docs.aws.amazon.com/cli/latest/reference/wafv2/get-web-acl.html)
