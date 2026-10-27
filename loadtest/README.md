# 스테이징 부하 시험 도구

Locust master 한 개와 worker 두 개로 API 부하 시험을 실행하고 결과를 수집한다. 처음 시작할 때는 요청을 보내지 않으며, 로그인한 사용자가 웹이나 CLI로 실행해야 요청이 시작된다.

실제 환경 생성·접속·삭제는 [스테이징 운영 안내](../docs/staging-loadtest.md), 마운트·권한·지표·환경 정리의 연결 규칙은 [실행 계약](CONTRACT.md)을 따른다.

## 실행과 관찰

1. 환경 설치 시 보호된 설정 파일과 `/var/lib/getddo-loadtest/reports`를 준비한다. 저장소 루트에서 다음과 같이 실행한다.

   ```sh
   LOCUST_BIND_IP=<private-IPv4> docker compose -f loadtest/compose.yaml up -d --build --scale worker=2
   ```

2. 기존 CloudFront HTTPS 주소의 `/locust/`에 로그인한다. 대상 API 주소는 고정돼 있다. 사용자 수·증가 속도·실행 시간을 지정하고 Profile은 비워 둔다. worker 두 개가 준비돼야 실행할 수 있다.
3. 웹 대신 CLI를 사용할 수도 있다. `loadtest/requirements.txt`가 설치된 Python 환경에서 실행한다.

   ```sh
   python loadtest/control.py --origin https://EXISTING.cloudfront.net \
     --username-file /protected/admin-user --password-file /protected/admin-password \
     run --users 2 --spawn-rate 1 --duration 30
   ```

   같은 공통 옵션 뒤에 `stop` 또는 `collect --output <empty-private-directory>`를 지정한다. 로그인 정보는 보호된 파일과 메모리에서만 읽으며 응답 본문·인증 정보는 출력하지 않는다.
4. Grafana의 스테이징 대시보드에서 요청 결과와 앱·호스트·DB 지표를 함께 본다. worker 상태와 지표 갱신 여부를 먼저 확인한다. 지연 단위는 초이며, `NaN`이나 수집 없음은 정상 0을 뜻하지 않는다. exporter 9646은 사설 수집 경로만 사용한다.
5. 시험을 중단한 뒤 최종 worker 보고를 기다리고 결과를 수집한다. 통계·전체 이력·실패·가림 처리한 예외·HTML·메타데이터·체크섬을 보관한다. 이전 실행의 수집을 마쳐야 다시 실행할 수 있다.

로그인은 세션과 CSRF·Origin을 확인한다. 로그인 실패 다섯 번이면 계정을 60초 잠그며 세션 쿠키 수명은 30분이다. 임의 대상·사용자·프로필 변경과 통계 초기화는 허용하지 않는다. 준비 상태 경로는 `/locust/health`다.

## 기본 시나리오

기본 `smoke_events`는 합성 계정으로 `GET /api/v1/events?page=1&size=1`만 호출한다. 계정 ID·역할·회원 등급을 실제 시험 데이터와 맞추고 응답 구조를 검사한다. 응답 본문과 계정 식별자는 결과에 저장하지 않는다. 기존 요청 제한도 유지된다.

기본 시나리오 외의 업무를 시험하려면 아래 방법으로 개인 시나리오를 적용한다. 헬스 확인 요청은 업무 집계에서 제외한다.

시나리오 작성 시 `LockedSession`의 상대 경로, 고정 요청 이름, 명시적인 timeout과 응답 검증을 사용한다. 허용할 업무 거절은 `envelope_result`에 HTTP 상태와 오류 코드 쌍으로 지정한다.

## 개인 시나리오 적용

스테이징 Locust 서버에 접속해 `loadtest/examples/events.py`와 `seed.sql`을 `/opt/getddo-loadtest/scenarios/<name>/` 아래에 복사하고 수정한다.

Python 시나리오는 `loadtest.runtime`의 `StageUser`를 사용한다. 고정된 스테이징 주소와 준비된 계정 헤더가 제공된다. `catch_response=True` 안에서 `self.check(response, validator)`를 호출해야 업무 결과가 집계된다. 별도 시험 계정을 사용하면 준비한 ID·역할·회원 등급을 함께 맞춘다.

```sh
loadtest apply /opt/getddo-loadtest/scenarios/<name>/events.py
# 웹에서 users=2, ramp up=1, run time=30s로 시험한다. Profile은 비워 둔다.
loadtest collect
loadtest db
```

`apply`는 실행 중인 시험이 있으면 거절한다. 구문·import·task 검증 후 기존 결과를 S3에 저장하고 재조회·해시를 확인한다. 변경할 소스의 읽기 전용 사본을 선택해 master와 worker 두 개만 재생성한다. 준비 실패 시 이전 설정과 소스로 복구한다. 파일을 고치는 것만으로 실행 중인 Python이 다시 로드되지는 않는다.

`collect`는 사용자를 중단하고 worker 최종 보고를 기다린다. Locust 보고서와 적용한 Python·SQL 소스를 함께 보관하고 S3 재조회를 확인한다. 웹 시작 시 409가 나오면 이전 결과 미수집, 진행 중인 적용, worker 미준비 여부를 확인한다. 웹 Stop은 AWS 자원을 삭제하지 않는다.

## 시험 데이터와 파일 관리

- `loadtest db`는 스테이징 시험용 계정으로 TLS를 검증한 MySQL 세션을 연다. `SOURCE /opt/getddo-loadtest/scenarios/<name>/seed.sql;`로 준비한 SQL을 실행한다.
- DB 접속 정보는 제한된 임시 파일로 전달하며 명령 인자에 비밀번호를 넣지 않는다.
- 팀이 작성한 Python을 실행하므로 외부의 신뢰하지 않는 코드를 받는 격리 환경으로 사용하지 않는다.
- Python·SQL 소스는 시험 결과와 함께 보관된다. 비밀번호나 개인정보를 넣지 않는다.
- 팀원들이 같은 시험 호스트·DB를 공유한다. 다른 사람의 실행 중에 시험 데이터를 변경하지 않는다.
- 환경 만료 시 개인 폴더·시험 데이터도 삭제된다. 필요한 결과와 덤프는 정리 전에 별도로 보관한다.

## 로컬 도구 검증

```sh
python -m pip install -r loadtest/requirements-test.txt
python -m playwright install --with-deps chromium
python -m pytest -q loadtest/tests
```

합성 계정과 로컬 API로 로그인·웹 실행·master/worker 집계·중단·수집을 검사한다. 로컬 소켓·openssl·브라우저 실행 권한이 필요하다. 이미 설치한 브라우저는 `LOCUST_TEST_CHROMIUM`으로 지정할 수 있다.

[도구 검사 결과](VALIDATION.md) · [스테이징 시험 결과](../docs/staging-loadtest.md#검증-범위)

환경 정리 후에도 영구 Locust 로그인 설정 세 개는 유지한다.
