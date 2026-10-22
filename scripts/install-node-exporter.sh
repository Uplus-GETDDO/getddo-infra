#!/usr/bin/env bash
# Installation artifact only. Run locally on an approved A/C/monitoring host.
set -euo pipefail
[[ $EUID == 0 && $(uname -m) == x86_64 ]]
source_dir=$(cd -- "$(dirname -- "$0")/.." && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
version=1.12.1
archive=node_exporter-${version}.linux-amd64.tar.gz
curl --fail --silent --show-error --location --proto '=https' --tlsv1.2   "https://github.com/prometheus/node_exporter/releases/download/v${version}/${archive}" -o "$work/$archive"
printf '%s  %s\n' b51d8a76aa2a9156a55d501aca6276fae09e262259a5e4e831d2c2222f084e63 "$work/$archive" | sha256sum -c -
tar -xzf "$work/$archive" -C "$work"
getent passwd node_exporter >/dev/null || useradd --system --no-create-home --shell /sbin/nologin node_exporter
install -m 0755 "$work/node_exporter-${version}.linux-amd64/node_exporter" /usr/local/bin/node_exporter
token=$(curl --disable -fsS --noproxy '*' --max-time 5 -X PUT -H 'X-aws-ec2-metadata-token-ttl-seconds: 60' http://169.254.169.254/latest/api/token)
private_ip=$(printf 'X-aws-ec2-metadata-token: %s\n' "$token" | curl --disable -fsS --noproxy '*' --max-time 5 --header @- http://169.254.169.254/latest/meta-data/local-ipv4)
[[ $private_ip =~ ^10\.20\.[0-9]+\.[0-9]+$ ]]
printf 'PRIVATE_IP=%s\n' "$private_ip" > /etc/node_exporter.env
chmod 0644 /etc/node_exporter.env
install -m 0644 "$source_dir/observability/node-exporter/node_exporter.service" /etc/systemd/system/node_exporter.service
systemctl daemon-reload
systemctl enable --now node_exporter
