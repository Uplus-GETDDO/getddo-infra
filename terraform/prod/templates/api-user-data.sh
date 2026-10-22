#!/bin/bash
# 도구와 공개 인증서만 준비한다. DB 자격 증명과 앱 env는 별도로 설정한다.
set -euo pipefail
umask 077
# 기본 AMI의 curl-minimal도 다운로드에 충분하므로 기존 curl을 사용한다.
if ! command -v curl >/dev/null 2>&1; then
  dnf install -y curl-minimal
fi
dnf install -y docker tar gzip util-linux coreutils awscli-2 java-21-amazon-corretto-headless mariadb105

if ! rpm -q amazon-ssm-agent >/dev/null 2>&1; then
  dnf install -y https://s3.ap-northeast-2.amazonaws.com/amazon-ssm-ap-northeast-2/latest/linux_amd64/amazon-ssm-agent.rpm
fi
systemctl enable --now amazon-ssm-agent docker

# Compose는 검증한 버전·체크섬으로 모든 사용자에게 설치한다.
temporary=$(mktemp -d)
trap 'rm -rf -- "$temporary"' EXIT
curl --fail --show-error --silent --location --retry 3 \
  https://github.com/docker/compose/releases/download/v2.40.3/docker-compose-linux-x86_64 \
  --output "$temporary/docker-compose"
echo "dba9d98e1ba5bfe11d88c99b9bd32fc4a0624a30fafe68eea34d61a3e42fd372  $temporary/docker-compose" | sha256sum --check -
install -d -m 0755 /usr/local/lib/docker/cli-plugins
install -m 0755 "$temporary/docker-compose" /usr/local/lib/docker/cli-plugins/docker-compose

# SSM 배포가 사용하는 디렉터리는 root만 변경할 수 있게 한다.
install -d -o root -g root -m 0700 /opt/getddo /opt/getddo/releases /etc/getddo
curl --fail --show-error --silent --location --retry 3 \
  https://truststore.pki.rds.amazonaws.com/ap-northeast-2/ap-northeast-2-bundle.pem \
  --output "$temporary/rds-ca.pem"
awk '/-----BEGIN CERTIFICATE-----/ {n++} n {print > (directory "/ca-" n ".pem")}' \
  directory="$temporary" "$temporary/rds-ca.pem"
for certificate in "$temporary"/ca-*.pem; do
  # 공개 CA만 포함한다. changeit은 DB 비밀번호나 개인 키 암호가 아니다.
  keytool -importcert -noprompt -alias "$(basename "$certificate" .pem)" \
    -file "$certificate" -keystore "$temporary/rds-truststore.p12" \
    -storetype PKCS12 -storepass changeit
done
install -m 0644 "$temporary/rds-ca.pem" /etc/getddo/rds-ca.pem
install -m 0644 "$temporary/rds-truststore.p12" /etc/getddo/rds-truststore.p12

docker info >/dev/null
docker compose version
aws --version
systemctl is-active --quiet amazon-ssm-agent
