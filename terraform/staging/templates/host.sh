# Stage-only host preparation; no application or load is started here.
dnf install -y amazon-cloudwatch-agent python3.12 python3.12-pip mariadb105 nano
python3.12 -m venv /opt/getddo-staging-venv
/opt/getddo-staging-venv/bin/pip install --quiet Werkzeug==3.1.3 PyMySQL==1.1.2 requests==2.34.2
install -d -m 0700 /opt/getddo/staging
work=$(mktemp -d)
trap 'rm -rf -- "$temporary" "$work"' EXIT
# Compose 2.40.3 defaults to Bake; the AL2023 Docker package's Buildx 0.12.1
# is below Bake's 0.17 minimum. Match Compose's reviewed Buildx dependency.
# SHA256: https://github.com/docker/buildx/releases/download/v0.29.1/checksums.txt
curl -fsSL --proto '=https' --tlsv1.2 --retry 3 \
  https://github.com/docker/buildx/releases/download/v0.29.1/buildx-v0.29.1.linux-amd64 \
  -o "$work/docker-buildx"
printf '%s  %s\n' 7d2d7d6d4680aa349614965aaa33ccec43f1a9a21e908a5ce4cb6adfa5ad5141 "$work/docker-buildx" | sha256sum -c -
install -m 0755 "$work/docker-buildx" /usr/local/lib/docker/cli-plugins/docker-buildx
docker buildx version >/dev/null
version=1.12.1
archive=node_exporter-${version}.linux-amd64.tar.gz
curl -fsSL --proto '=https' --tlsv1.2 "https://github.com/prometheus/node_exporter/releases/download/v${version}/${archive}" -o "$work/$archive"
printf '%s  %s\n' b51d8a76aa2a9156a55d501aca6276fae09e262259a5e4e831d2c2222f084e63 "$work/$archive" | sha256sum -c -
tar -xzf "$work/$archive" -C "$work"
getent passwd node_exporter >/dev/null || useradd --system --no-create-home --shell /sbin/nologin node_exporter
install -m 0755 "$work/node_exporter-${version}.linux-amd64/node_exporter" /usr/local/bin/node_exporter
cat > /etc/systemd/system/node_exporter.service <<'UNIT'
[Unit]
Description=Stage node exporter
After=network-online.target
[Service]
User=node_exporter
ExecStart=/usr/local/bin/node_exporter --web.listen-address=:9100
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
[Install]
WantedBy=multi-user.target
UNIT
cat > /opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json <<'JSON'
{"logs":{"logs_collected":{"files":{"collect_list":[{"file_path":"/var/log/cloud-init-output.log","log_group_name":"/getddo/staging/api","log_stream_name":"{instance_id}"}]}}}}
JSON
/opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl -a fetch-config -m ec2 -c file:/opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json -s
systemctl daemon-reload
systemctl enable --now node_exporter
touch /opt/getddo/staging/host-ready
