# 운영 컨테이너 이미지와 ECR 배포

## 구성과 보관

운영 이미지는 서울 계정 `942466928325`의 비공개 저장소 `getddo-be`에서 관리한다.
저장소 URI는 `942466928325.dkr.ecr.ap-northeast-2.amazonaws.com/getddo-be`다.
S3에는 Compose·스크립트·메타데이터 배포 묶음을 보관한다. 사용자 업로드 이미지 S3와는 별개다.
PR·스테이징의 이미지 tar 전달 계약은 유지한다.

저장소는 `IMMUTABLE` 태그와 `AES256` 저장 암호화를 사용한다.
`force_delete=false`와 Terraform `prevent_destroy=true`로 Terraform의 저장소 제거를 방지한다.
이는 콘솔·API의 모든 삭제를 막는 계정 정책이 아니다.
태그 없는 이미지만 push 후 7일 뒤 만료하며 태그 있는 운영·복구 이미지는 자동 만료·아카이브하지 않는다.
태그 제거 작업과 계정 전체 스캔 설정 변경은 포함하지 않는다.
정책 적용 전에 lifecycle preview를 확인하고 운영·복구 digest가 만료 대상이 아닌지 대조한다.

## 권한과 연결값

| 주체 | 범위 |
| --- | --- |
| `getddo-dev-github-image-publish` | main ref에서 지정 저장소 push·manifest 및 이미지 metadata 확인 |
| 기존 API EC2 역할 | 지정 저장소의 이미지 manifest·레이어 pull |
| 기존 GitHub CD 역할 | 지정 저장소 manifest·태그·digest 읽기 |
| 운영자 | 지정 저장소 생성·속성·태그·lifecycle preview/관리와 baseline push/pull |

모든 ECR 작업에는 서울 `aws:RequestedRegion` 조건을 둔다.
`GetAuthorizationToken`은 리소스 제한이 없어 별도 `Resource: *` statement를 사용한다.
운영자의 registry scanning 조회도 별도 statement이며 설정 변경은 허용하지 않는다.
나머지 작업은 저장소의 정확한 ARN으로 제한한다. EC2·CD에는 push·삭제를 허용하지 않는다.
운영자도 이미지 삭제·저장소 삭제·repository policy 변경 권한을 추가하지 않는다.
기존 S3·SSM·앱 설정·관측 권한은 보존한다.

publisher의 OIDC `aud`는 `sts.amazonaws.com`, `sub`는 다음 값의 정확 일치다.
업로드 job에는 GitHub Environment를 지정하지 않는다.

```text
repo:Uplus-GETDDO@341824447/getddo-be@1362269492:ref:refs/heads/main
```

기존 CD 신뢰는 `:environment:production`을 유지한다.
PR·dev·다른 저장소에서 publisher를 획득할 수 없어야 한다.

Terraform 출력 `github_repository_variables`의 다음 비밀이 아닌 값을 백엔드 repository Variables에 등록한다.

| 변수 | 출력 |
| --- | --- |
| `ECR_REPOSITORY_URI` | `ecr_repository_uri` |
| `ECR_PUBLISH_ROLE_ARN` | `ecr_publish_role_arn` |
| `ECR_AWS_REGION` | `ap-northeast-2` |

production 환경의 기존 CD 역할·버킷·A/C·대상 그룹·rolling 설정은 유지한다.
운영자는 기존 그룹의 관리형 정책 10개를 보존하고 ECR 권한을 별도 inline 정책으로 관리한다.
적용 전에 기존의 모든 group inline 정책과 새 정책의 공백 제외 합계가 5,120자 이하인지 확인한다.
인증 토큰·장기 키·앱 env는 출력·GitHub Variables·Artifact·SSM 명령 본문에 넣지 않는다.

## 이미지 metadata 계약

`image-metadata.json`은 다음 구조의 데이터이며 실행할 shell 코드로 해석하지 않는다.

```json
{
  "schema_version": 1,
  "source_sha": "<40자리 SHA>",
  "build_run_id": "<Build run ID>",
  "build_run_attempt": "<Build 실행 차수>",
  "repository_uri": "942466928325.dkr.ecr.ap-northeast-2.amazonaws.com/getddo-be",
  "image_tag": "<SHA>-<run ID>-<attempt>",
  "image_digest": "sha256:<manifest digest>",
  "image_id": "sha256:<Docker config ID>",
  "platform": "linux/amd64"
}
```

같은 SHA의 다른 run·차수는 별도 태그를 사용한다.
CD는 선행 Build의 정확한 run·차수·SHA, 허용 저장소, 태그와 ECR manifest digest를 확인한다.
실행 참조는 `repository_uri@image_digest`이며 로컬 `image_id`와 digest는 서로 다른 값이다.
A/C는 pull 후 RepoDigests·config ID·revision·플랫폼을 검사한다.

## 적용과 기존 이미지 이전

1. 일반 운영자 프로필로 실제 계정·역할과 A/C 컨테이너·current·환경 파일 해시·정상 상태를 읽기 전용 확인한다.
2. bootstrap 전체 plan에서 기존 IAM 정책·연결 보존과 최소 ECR 권한 추가를 확인한다. bootstrap IAM 적용에 필요한 작업만 별도 관리자 프로필을 사용한다.
3. 일반 운영자 프로필로 기존 ECR 저장소 충돌과 registry scanning 설정을 확인한다. 동일 이름 저장소가 있으면 소유권 확인 없이 가져오거나 덮어쓰지 않는다.
4. 기존 `bootstrap/terraform.tfstate`, `dev/terraform.tfstate`와 잠금을 유지해 전체 prod plan을 검토·적용한다. 무관한 변경·교체·삭제가 있으면 조사하고 `-target`이나 State 조작으로 숨기지 않는다.
5. Terraform 출력과 실계정·리전을 대조해 변수를 등록하고 실제 권한을 확인한다. 시뮬레이션 allowed는 실제 push/pull 성공 증거가 아니다.
6. 현재 운영 image ID·revision과 같은 기존 산출물을 checksum으로 검증한다. 없으면 해당 이미지를 `docker save`로 보존하며 재빌드하지 않는다.
7. 별도 운영자 인증으로 `baseline-<운영 SHA>-<고유 이전 ID>` 태그로 이전한다. Docker Engine이 없으면 검증한 docker-archive 전송 도구를 사용하고 실제 Docker 검증과 구분한다.
8. 양쪽 서버가 baseline digest를 pull한 뒤 기존 로컬 ID·revision과 일치하는지 확인한다. 서버에는 상시 push 권한을 추가하지 않는다.
9. 백엔드의 검증된 baseline 변환 도구로 기존 release를 보존하는 새 기준 release를 만들고 current를 원자적으로 전환한다. A/C를 한 대씩 처리하며 컨테이너 ID·재시작 횟수·env 해시·정상 응답이 유지돼야 한다.

이전 도중에는 앱 컨테이너·현재 환경을 교체하지 않는다. A/C의 기존 이미지가 다르면 각각의 baseline을 보존한다.
원본 S3 release는 남기되 운영 배포 엔진의 tar fallback은 만들지 않는다.

## 배포와 자동 복구 확인

A/C 모두 후보 digest와 이전 복구 이미지를 준비·검증한 뒤 ALB 제외를 시작한다.
준비 실패 시 current·env·컨테이너·ALB 상태를 유지한다.
기존 순차 배포·연결 정리·설정 원자 교체·HTTP/지표·ALB 확인을 유지한다.
새 앱 기동 실패 시 이전 ECR 참조·Compose·직전 환경으로 복구하고 정상 확인 후 재등록한다.
후순위 서버 실패가 앞 서버까지 자동으로 되돌린다는 보장은 없으므로 최종 혼합 버전을 기록한다.
SSM 결과가 불명확하면 재발행·다음 서버 진행을 중단하고 잠금을 보존한다.
인증은 서버 역할의 임시 자격 증명, root 전용 임시 `DOCKER_CONFIG`, `--password-stdin`을 사용하고 정리한다.

실제 시험은 A 한 대에서 존재하지 않는 digest 준비 실패와 일회성 Compose의 기동 실패를 검증한다.
C healthy 유지·A 자동 복구·C 추가 배포 중단을 확인한 뒤 정상 산출물로 최종 A/C 버전을 맞춘다.
DB·Redis·WAF·알림·프론트 변경이나 부하 시험은 포함하지 않는다.
전체 plan 변경 0, 임시 인증·fixture 정리, 현재/복구 이미지 다운로드 가능 상태를 최종 확인한다.

## 공식 근거

- [ECR 업로드 권한](https://docs.aws.amazon.com/AmazonECR/latest/userguide/image-push-iam.html)
- [ECR API별 리소스 범위](https://docs.aws.amazon.com/service-authorization/latest/reference/list_ecr.html)
- [lifecycle preview](https://docs.aws.amazon.com/AmazonECR/latest/userguide/LifecyclePolicies.html)
- [IAM 정책 개수·크기 제한](https://docs.aws.amazon.com/IAM/latest/UserGuide/reference_iam-quotas.html)
- [GitHub immutable OIDC subject](https://docs.github.com/en/actions/reference/security/oidc)
