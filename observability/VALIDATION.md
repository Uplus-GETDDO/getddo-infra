# 모니터링 구성 사전 검증 기록

2026-10-22 모니터링 자원을 적용하기 전에 수행한 계획·설정 검증 기록이다. 실제 설치·재부팅·백업 복원 결과는 [운영 모니터링 검증 결과](../docs/monitoring-runtime-verification.md), 이후 접속 구성은 [Grafana 운영 안내](../docs/grafana-https-access.md)를 따른다.

## 당시 Terraform 계획

| 실행 루트 | 생성 | 수정 | 삭제 | 교체 |
| --- | --- | --- | --- | --- |
| monitoring | 15 | 0 | 0 | 0 |
| prod | 0 | 0 | 0 | 0 |
| bootstrap | 1 | 4 | 0 | 0 |

모니터링 서버·별도 데이터 EBS·보안 그룹·IAM을 추가하는 계획이었다. 기존 API 서버·DB·앱 자원의 교체·삭제 계획은 없었다.

## 케이스별 결과

| 검증 항목 | 확인 방법 | 당시 결과 |
| --- | --- | --- |
| Terraform 구성 | Terraform 1.16.5·AWS Provider 6.68.0의 fmt·validate·mock 검사 | 세 루트의 mock 26회 통과 |
| 설치·백업·복구 스크립트 | Python·Bash·JSON 검사와 대역 시험 | 최초 단위 검사 12개, 후속 보완 13개 통과 |
| 데이터 볼륨 보호 | 잘못된 UUID·볼륨 식별자·기존 마운트·fstab 입력 대역 | 잘못된 볼륨과 중복 설정 거절 확인 |
| 백업·복구 실패 처리 | 서비스 시작 상태·명령 실패 조합 대역 | 기존 서비스 상태와 최초 오류를 보존하는 84개 조합 검증 |
| Compose·Prometheus | 고정 버전 Compose·promtool로 설정 해석 | Compose 설정, 수집 설정과 대시보드 PromQL 14개 검증 통과 |
| CloudWatch 대시보드 변환 | 당시 실제 차원으로 읽기 전용 변환 | Grafana 패널 23개 생성 확인 |
| IAM 정책 | Access Analyzer의 정책 검사 | 수정한 정책 네 개에서 검사 지적 없음 |

## 당시 모니터링 서버 비용 추정

서울 리전에서 모니터링 서버만 730시간 사용할 때의 2026-10-22 견적이다.

| 항목 | 당시 월 추정액 |
| --- | --- |
| t3.medium | US$37.96 |
| gp3 50 GiB: 루트 20 + 데이터 30 | US$4.56 |
| 사용 중인 공인 IPv4 | US$3.65 |
| 합계 | US$46.17 |

세금·전송·CloudWatch 조회·추가 백업 비용은 제외했다.

당시 참고: [EC2 요금](https://aws.amazon.com/ec2/pricing/on-demand/) · [EBS 요금](https://aws.amazon.com/ebs/pricing/) · [공인 IPv4 요금](https://aws.amazon.com/vpc/pricing/).
