# 인프라 문서

운영 구조와 주요 설계는 [저장소 README](../README.md)를 참고한다.

## 구성·운영 절차

| 작업 | 문서 |
| --- | --- |
| 실행 환경과 Terraform 적용 | [인프라 실행 안내](infrastructure-development.md) |
| 앱 이미지 배포·복구 | [ECR·SSM 배포](ecr-deployment.md) |
| DB·Redis 장애 대응 | [RDS 이중화](rds-multiaz.md) · [Redis 이중화](redis-ha.md) |
| IP별 요청 제한 | [WAF 구성](waf-rate-limit.md) |
| 모니터링 접속·복원 | [Grafana 운영 안내](grafana-https-access.md) · [대시보드 해석](grafana-dashboard-guide.md) |
| 부하 시험 환경 사용·정리 | [스테이징·Locust](staging-loadtest.md) |
| 현재 사양과 DB 연결 설정 | [서버·DB 사양](capacity-resize.md) · [DB 연결 수](database-connections.md) |

## 실제 시험 결과

- [RDS 장애 전환](rds-failover-test-results.md)
- [Redis 장애 전환](redis-failover-test-results.md)
- [ECR 배포 실패와 복구](ecr-deployment-test-results.md)
- [WAF 차단과 해제](waf-rate-limit-test-results.md)
- [모니터링 수집·재부팅·백업 복원](monitoring-runtime-verification.md)

## 도구·설정 검증 기록

- [모니터링 구성 사전 검사](../observability/VALIDATION.md)
- [Locust 도구 검사](../loadtest/VALIDATION.md)
