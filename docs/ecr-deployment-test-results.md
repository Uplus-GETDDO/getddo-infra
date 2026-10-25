# 운영 이미지 ECR 배포·복구 검증 결과

운영 이미지를 재빌드 없이 ECR로 이전한 뒤, 실제 main Build의 이미지를 A/C에 순차 배포했다. 이미지 준비 실패 시 기존 앱을 유지했고, A의 실제 기동 실패에는 이전 ECR 이미지·Compose·환경으로 자동 복구했다. 최종 정상 배포는 [v0.1.12](https://github.com/Uplus-GETDDO/getddo-be/releases/tag/v0.1.12)다.

## 시험 구성과 방법

| 구성 | 설명 |
| --- | --- |
| 이미지 저장소 | 서울의 비공개 ECR `getddo-be`. 불변 태그·AES256 저장 암호화 |
| API 서버 | 기존 A/C EC2, Docker Compose, ALB 순차 교체 |
| 배포 식별 | manifest digest·Docker config ID·소스 revision·단일 `linux/amd64`를 별도 대조 |
| 파일·설정 전달 | S3의 작은 Compose·스크립트·metadata 묶음과 기존 Parameter Store |
| 권한·보관 | main 전용 게시 역할, production CD 읽기, EC2 pull 분리. untagged만 7일 만료 |

1. 원본 S3 release의 checksum·config ID·revision·플랫폼을 A/C 실행 이미지와 대조하고, 같은 이미지를 baseline으로 게시했다. 로컬 Docker Engine이 없어 검증한 `crane`으로 docker-archive를 전송했으며 실제 pull·Docker 검증은 A/C에서 수행했다.
2. 양쪽 baseline pull의 로컬 ID를 확인한 뒤, 원본 release를 보존하는 새 기준 기록으로 current를 전환했다. 실행 컨테이너·시작 시각·재시작 수·env·Compose·HTTP를 전후 대조했다.
3. 실제 main Build의 metadata와 ECR manifest/config를 대조했다. 최초 자동 CD는 사전 시험을 위해 보유한 전역 잠금에서 SSM 실행 전에 중단시켰다.
4. 잘못된 리전의 metadata를 로컬에서 거부하고, 존재하지 않는 digest는 A에서 실제 pull 실패를 확인했다. 이후 A의 일회성 시험 Compose로 후보 컨테이너 기동 실패를 만들고 자동 복구와 C 추가 배포 중단을 확인했다.
5. 복구 확인 후 같은 Build 산출물의 정상 CD를 재실행했다. 양쪽 사전 준비·A→C 순차 교체와 최종 이미지·current·HTTP·지표·ALB·잠금·임시 파일을 대조했다.

## 케이스별 결과

| 테스트 케이스 | 확인 방법 | 기대 결과 | 실제 결과 |
| --- | --- | --- | --- |
| 원본 이미지 이전 | S3 checksum·실행 image ID·revision·플랫폼과 ECR config 대조 | 재빌드 없이 같은 이미지 보존 | 원본 checksum 검증, revision·config ID·플랫폼 일치. 양쪽 baseline digest pull 성공 |
| 재시작 없는 기준 기록 전환 | current·컨테이너·시작 시각·재시작 수·env·Compose·HTTP 대조 | 원본 release와 실행 앱 유지 | A/C 원본 release 보존. 컨테이너·시작 시각·재시작 0·env·Compose·HTTP 200 유지 |
| main 게시·출처 검증 | 성공한 Build의 metadata와 실제 ECR manifest/config 대조 | 정확한 SHA·run·attempt의 이미지 사용 | [Build 40211252483](https://github.com/Uplus-GETDDO/getddo-be/actions/runs/40211252483), attempt 1의 실제 OIDC 게시 성공. metadata와 ECR manifest digest·config ID 일치 |
| 복구 이미지 보관 | baseline 태그와 실제 lifecycle preview 확인 | 태그 있는 복구 이미지 제외 | preview COMPLETE, 만료 대상 0개 |
| 전역 잠금 충돌 | 잠금을 보유한 상태의 최초 자동 CD 확인 | SSM·앱 교체 전에 중단 | CD attempt 1은 조건부 Put에서 중단, SSM 미발행. 앱 기동 실패와 구분 |
| 잘못된 metadata | 허용 리전과 다른 입력으로 로컬 검증 | 서버 작업 전에 거부 | ValueError로 거부, AWS·SSM 호출 0회. 서버 장애 주입과 구분 |
| 서버 준비 중 pull 실패 | A에서 존재하지 않는 digest로 실제 pull, A/C 전후 상태 대조 | ALB 제외·앱 교체·env/current 변경 없이 실패 | SSM Failed/1. A/C 컨테이너·시작 시각·재시작 수·current·이미지·env·Compose·HTTP·지표 불변, ALB 양쪽 healthy |
| 실제 기동 실패·자동 복구 | A 시험 Compose, Docker 이벤트·SSM 결과·복구 상태 대조 | 이전 ECR 이미지·Compose·직전 환경 자동 복구 | 후보 create/start/die(exit 1) 관측. SSM Failed/1을 유지하면서 정상 복구 마커 기록. 이전 revision·digest·config ID·current·Compose·env로 복구, HTTP 200·필수 지표·ALB healthy |
| 실패 후 C 미배포 | C의 시험 전후 실행 상태와 최종 A/C 대조 | C 기존 앱 유지, 수동 복구 없이 종료 | C 컨테이너·시작 시각·재시작 수·이미지·설정 불변. 수동 앱 복구 없이 A/C 정상, 잠금 해제·진행 마커·임시 인증·복구 context 없음 |
| 정상 최종 배포 | 실제 CD·A/C 런타임·읽기 API 대조 | 같은 digest로 순차 배포 후 양쪽 정상 | [CD 40212253872 attempt 2](https://github.com/Uplus-GETDDO/getddo-be/actions/runs/40212253872/attempts/2) 성공. 재빌드 없이 같은 Build 사용, 양쪽 revision·digest·config ID·current 일치, HTTP 200·필수 지표·읽기 API JSON 확인 |
| 정리·인프라 일치 | 시험 release 경로·인증·마커·잠금·전체 Terraform plan 확인 | 운영·복구 release 보존, 임시 참조와 적용 변경 없음 | 시험 release 디렉터리 제거, current·baseline·원본 보존. 임시 인증·진행 마커·복구 context 없음, 전역 잠금 없음·호스트 잠금 사용 가능. bootstrap/prod 전체 plan 변경 0 |

최초 A 기준 전환 명령은 전환 후 임시 Python 캐시 정리 오류로 실패했으며, 실패 상태와 잠금을 보존한 채 A 재전환 없이 독립 검증·정리 후 C를 순차 완료했다.

## 최종 상태

2026-10-26(KST) 확인한 이미지 식별값이다. manifest digest는 레지스트리 이미지 참조이며 Docker config ID와 다른 값이다.

| 항목 | 기존 운영·복구 이미지 | 정상 배포 이미지 |
| --- | --- | --- |
| 소스 revision | `96b24cd7d70522b3031a534317376ed155b5abe4` | `879859a1f5faee6bbaf6a3de8105c45b720d4c7a` |
| manifest digest | `sha256:a316d82d46b207732b25811cd2dcfba17e7900b8072ef323a89cdd463b9de443` | `sha256:ed63d71afda7a421f4a64f8f47edebdb3c0c32c0a65e7785fd21db8fea4b5d02` |
| Docker config ID | `sha256:e01952948de48a05b8b69ecfa3583744691390dc41d5c2922b8fe1a13ba7bf48` | `sha256:b4d430ee990dc26e35d32c567d40da7a8db2667e866b4703936774334aec7ba2` |

A/C는 정상 배포 이미지의 `linux/amd64`를 실행하며 current는 `/opt/getddo/releases/40212253872-2`다. 두 서버 모두 재시작 수 0, 기존 env 해시 유지, 같은 정상 Compose를 확인했다. 이전 ECR 이미지도 양쪽 로컬에 남아 있으며 baseline과 원본 release를 보존했다.

양쪽 ALB target은 healthy이고 실행 중 SSM 명령은 0개였다. 전역 잠금은 없고 호스트 잠금은 사용 가능하며, 진행 마커·임시 ECR 인증·복구 context는 남지 않았다. 비밀값 없는 시험 묶음은 S3 증거로 남겼으며 현재 release에서 참조하지 않는다. 최종 bootstrap·prod 전체 plan은 추가 적용 변경 0이었다.

## 시험 범위

실제 운영 시험은 위 표에 기록한 준비 실패·A 기동 실패/자동 복구·정상 순차 배포다. SSM 응답 유실·env 복원 실패·부분 배포 등의 안전장치는 로컬 회귀 범위이며, mock 통과를 실제 권한·배포·복구 성공으로 대신하지 않았다.

필요한 낮은 빈도 GET만 사용했으며 업무 쓰기·프론트 접근·부하 시험은 수행하지 않았다. DB·Redis·WAF·알림 설정도 변경하지 않았다. Terraform `prevent_destroy`는 콘솔·API의 모든 삭제를 막는 정책이 아니며, lifecycle 제외는 모든 수동 삭제에 대한 영구 보존을 뜻하지 않는다.

[운영 절차](ecr-deployment.md) · [원본 Build](https://github.com/Uplus-GETDDO/getddo-be/actions/runs/40144472847)
