# 운영 모니터링 구성과 복구

Prometheus·Grafana가 기존 API A/C의 앱·호스트 지표와 CloudWatch 지표·로그를 조회한다. 구성은 `terraform/monitoring`, State는 `monitoring/terraform.tfstate`에서 관리한다.

실제 시험은 [모니터링 검증 결과](../docs/monitoring-runtime-verification.md), HTTPS 접속은 [Grafana 운영 안내](../docs/grafana-https-access.md), 지표 해석은 [대시보드 안내](../docs/grafana-dashboard-guide.md)를 참고한다.

## 서버·접근·저장

- 서버는 AL2023 x86_64·t3.medium이며 루트 gp3 20 GiB와 별도 데이터 gp3 30 GiB를 사용한다. CPU 크레딧은 standard이므로 장시간 부하에서는 제한될 수 있다.
- 데이터 EBS는 EC2와 분리해 관리하고 `prevent_destroy`를 설정한다. 서버 교체 시 서비스를 멈추고 정상 마운트 해제·분리 후 같은 AZ에 연결한다. 다른 AZ 복구에는 스냅샷 복원이 필요하다. 볼륨 유지와 백업은 별개다.
- Prometheus 9090은 호스트에 공개하지 않는다. node_exporter 9100과 앱 관리 포트 9091은 모니터링 보안 그룹의 사설 수집 경로로 제한한다.
- Grafana는 사설 IP:3000에 바인딩하고 기존 CloudFront·ALB의 `/grafana/` HTTPS 경로와 로그인을 사용한다. SSM 포트 전달도 관리자 접속에 사용할 수 있다.
- Grafana의 AWS 조회는 EC2 역할과 IMDSv2 임시 자격 증명을 사용한다. Access Key 파일을 배포하지 않는다. 로그 조회와 전용 관리자 비밀번호 읽기 범위를 구분한다.

관리자 비밀번호는 `/getddo/monitoring/grafana/admin-password` SecureString에서 읽어 `/run`의 제한된 파일로 준비한다. Terraform은 비밀번호 값을 저장하거나 읽지 않는다. 이 초기 설정 값을 변경해도 이미 초기화된 Grafana DB의 비밀번호가 자동 회전하지는 않는다.

## 설치와 대상 갱신

설치 파일은 모니터링 서버의 `/opt/getddo-monitoring`에 배치한다. State·plan·비밀번호·개인 설정 파일은 전송 묶음에 넣지 않는다. 서버에는 AWS CLI·SSM agent·Python과 HTTPS 외부 통신이 필요하다.

Terraform 출력의 실제 데이터 볼륨 ID를 사용한다. `--format-empty`는 확인한 빈 볼륨을 처음 준비할 때만 사용한다. 기존 ext4는 재사용하며, 작업을 진행하려고 파일시스템을 삭제하거나 강제 분리하지 않는다.

```sh
sudo /opt/getddo-monitoring/scripts/monitoring-host.sh prepare "$DATA_VOLUME_ID" --format-empty
sudo /opt/getddo-monitoring/scripts/monitoring-host.sh install
```

A/C가 교체되면 실제 사설 IP와 CloudWatch 차원으로 대상 파일을 다시 만든다.

```sh
python3 scripts/monitoring-targets.py \
  --output "$PRIVATE_RENDER_DIR/prometheus" \
  --cloudwatch-dashboard "$PRIVATE_RENDER_DIR/cloudwatch-generated.json"
```

생성한 파일을 모니터링 서버에 배치하고 권한을 확인한다. 생성 디렉터리와 실제 IP가 담긴 출력은 Git에서 제외한다.

```sh
sudo install -m 0644 "$PRIVATE_RENDER_DIR/prometheus/api-targets.json" /opt/getddo-monitoring/observability/prometheus/api-targets.json
sudo install -m 0644 "$PRIVATE_RENDER_DIR/prometheus/node-targets.json" /opt/getddo-monitoring/observability/prometheus/node-targets.json
sudo install -m 0644 "$PRIVATE_RENDER_DIR/cloudwatch-generated.json" /opt/getddo-monitoring/observability/grafana/dashboards/cloudwatch-generated.json
```

A/C와 모니터링 서버의 node_exporter는 `scripts/install-node-exporter.sh`로 설치한다. Docker의 데이터 마운트·비밀번호 준비 검사는 모니터링 서버에만 적용한다. API 서버의 Docker 전역 설정을 바꾸지 않는다.

Docker와 모니터링 서비스는 예상한 EBS 마운트·UUID·볼륨 식별자를 확인한다. 디스크나 비밀번호가 없으면 시작을 중단한다. 의존성을 복구한 뒤 Docker와 `getddo-monitoring.service`를 다시 시작한다. 검사를 우회하거나 `compose down -v`로 데이터를 제거하지 않는다.

## 수집과 보관 한도

운영 지표는 15초 간격·10초 timeout으로 수집한다. HTTP·JVM·Hikari·Tomcat·호스트·Prometheus 상태를 함께 본다. CloudWatch 대시보드의 1분 갱신은 원본 지표 해상도를 높이지 않는다.

Prometheus는 14일 또는 20GB 중 먼저 도달하는 한도로 보관한다. 데이터 디스크에는 WAL·메모리 내 활성 데이터의 디스크 기록·압축 작업·Grafana DB와 파일시스템 여유도 필요하다. 20GB에 먼저 도달하면 14일 이전에 오래된 샘플부터 정리된다. 디스크 여유와 활성 시계열 수를 확인해 조정한다.

누락된 지표를 정상 0으로 바꾸지 않는다. 모니터링 서버는 한 대이며, 알림 전달과 자동 외부 백업은 구성하지 않았다.

## 백업과 복원

백업은 서비스를 멈추고 Prometheus TSDB의 WAL과 Grafana SQLite를 함께 보관한다. 백업 후 기존 서비스 상태를 복구하며, 이전에 멈춰 있던 서비스는 시작하지 않는다.

```sh
sudo /opt/getddo-monitoring/scripts/monitoring-backup.sh backup "$ARCHIVE"
```

`ARCHIVE`는 충분한 공간이 있는 절대 경로다. 20 GiB 루트 디스크를 전체 데이터 백업 장소로 사용하지 않는다. 백업에는 인증 정보가 포함될 수 있으므로 권한을 제한하고 암호화해 서버 밖에 보관한다. 해당 코드 버전·파일시스템 구성·외부 SecureString도 별도로 유지한다.

복원 전 모니터링과 Docker를 멈추고 현재 볼륨의 복구 사본을 남긴다. 검증한 마운트에 빈 `prometheus/`·`grafana/` 디렉터리를 준비하고 체크섬을 확인한 백업으로 복원한다.

```sh
sudo /opt/getddo-monitoring/scripts/monitoring-backup.sh restore "$ARCHIVE"
```

복원 도구는 경로·파일 유형을 검사하고 소유권을 복구한 뒤 서비스를 멈춘 상태로 둔다. Docker·모니터링을 시작하고 새 지표·과거 샘플·Grafana 계정과 데이터소스를 확인한다. 실행 중인 TSDB를 단순 복사한 파일을 일관된 백업으로 취급하지 않는다.

## 로컬 검사

Terraform·스크립트 검사는 [인프라 실행 안내](../docs/infrastructure-development.md)를 따른다. Compose·Prometheus·대시보드 구문은 `scripts/install-monitoring-check-tools.sh`로 도구를 준비한 뒤 `scripts/check-monitoring.py`로 확인한다. 당시 사전 검사 결과는 [설정 검증 기록](VALIDATION.md)에 정리했다.
