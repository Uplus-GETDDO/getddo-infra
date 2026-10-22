#!/usr/bin/env bash
# Run on the new monitoring host only, after GD-299 approval. Never enable tracing.
set -euo pipefail
umask 077
[[ $EUID == 0 ]]
source_dir=$(cd -- "$(dirname -- "$0")/.." && pwd)
config=/etc/getddo-monitoring.conf

load_volume() {
  source "$config"
  [[ $DATA_VOLUME_ID =~ ^vol-[0-9a-f]{17}$ ]]
  device=/dev/disk/by-id/nvme-Amazon_Elastic_Block_Store_${DATA_VOLUME_ID//-/}
  [[ -b $device ]]
}
guard() {
  load_volume
  mountpoint -q /srv/monitoring
  [[ $(findmnt -n -o UUID --target /srv/monitoring) == "$(blkid -s UUID -o value "$device")" ]]
  [[ $(cat /srv/monitoring/.volume-id) == "$DATA_VOLUME_ID" ]]
}
case ${1:-} in
  prepare)
    # Explicit EBS ID avoids formatting a different NVMe disk. Never formats existing FS.
    DATA_VOLUME_ID=${2:?Usage: monitoring-host.sh prepare volume-id}
    [[ $DATA_VOLUME_ID =~ ^vol-[0-9a-f]{17}$ ]]
    [[ ! -e $config ]] || { echo 'Configuration exists; use guard and the recovery runbook.' >&2; exit 1; }
    device=/dev/disk/by-id/nvme-Amazon_Elastic_Block_Store_${DATA_VOLUME_ID//-/}
    [[ -b $device ]]
    if findmnt -rn -S "$device" >/dev/null; then
      echo 'Device is already mounted.' >&2; exit 1
    fi
    if ! blkid "$device" >/dev/null; then
      [[ ${3:-} == --format-empty ]] || { echo 'Fresh disk requires explicit --format-empty' >&2; exit 1; }
      [[ -z $(wipefs --noheadings --output TYPE "$device") ]]
      mkfs.ext4 "$device"
    fi
    [[ $(blkid -s TYPE -o value "$device") == ext4 ]]
    mkdir -p /srv/monitoring
    [[ -z $(ls -A /srv/monitoring) ]]
    uuid=$(blkid -s UUID -o value "$device")
    if grep -qE '[[:space:]]/srv/monitoring[[:space:]]' /etc/fstab; then
      echo '/srv/monitoring already has an fstab entry.' >&2; exit 1
    fi
    printf 'UUID=%s /srv/monitoring ext4 defaults,nofail 0 2\n' "$uuid" >> /etc/fstab
    mount /srv/monitoring
    if [[ -e /srv/monitoring/.volume-id ]]; then
      [[ $(cat /srv/monitoring/.volume-id) == "$DATA_VOLUME_ID" ]]
    else
      printf '%s\n' "$DATA_VOLUME_ID" > /srv/monitoring/.volume-id
    fi
    install -d -m 0755 -o 65534 -g 65534 /srv/monitoring/prometheus
    install -d -m 0750 -o 472 -g 472 /srv/monitoring/grafana
    printf 'DATA_VOLUME_ID=%s\n' "$DATA_VOLUME_ID" > "$config"
    guard
    ;;
  guard) guard ;;
  secret)
    guard
    install -d -m 0750 -o root -g 472 /run/getddo-monitoring
    temp=$(mktemp /run/getddo-monitoring/password.XXXXXX)
    trap 'rm -f "$temp"' EXIT
    aws ssm get-parameter --region ap-northeast-2 \
      --name /getddo/monitoring/grafana/admin-password --with-decryption \
      --query Parameter.Value --output text > "$temp"
    [[ -s $temp ]]
    chown 472:472 "$temp"
    chmod 0400 "$temp"
    mv "$temp" /run/getddo-monitoring/grafana-admin-password
    ;;
  install)
    guard
    # AL2023 supplies Docker/AWS CLI; Compose is a separately checksum-pinned plugin.
    dnf install -y docker
    install -d -m 0755 /usr/local/lib/docker/cli-plugins
    temp=$(mktemp)
    trap 'rm -f "$temp"' EXIT
    curl -fsSL --proto '=https' --tlsv1.2 \
      https://github.com/docker/compose/releases/download/v5.6.1/docker-compose-linux-x86_64 -o "$temp"
    printf '%s  %s\n' 47737f4236e6d3ccea7291995203f8f018058783b4c3a9469119811a0b5db8c0 "$temp" | sha256sum -c -
    install -m 0755 "$temp" /usr/local/lib/docker/cli-plugins/docker-compose
    install -d -m 0755 /etc/systemd/system/docker.service.d
    install -m 0644 "$source_dir/observability/systemd/docker-monitoring.conf" /etc/systemd/system/docker.service.d/monitoring.conf
    systemctl daemon-reload
    systemctl enable --now docker
    install -m 0644 "$source_dir/observability/systemd/getddo-monitoring.service" /etc/systemd/system/getddo-monitoring.service
    install -m 0644 "$source_dir/observability/systemd/getddo-staging-monitoring.service" /etc/systemd/system/getddo-staging-monitoring.service
    install -m 0644 "$source_dir/observability/systemd/getddo-staging-monitoring.timer" /etc/systemd/system/getddo-staging-monitoring.timer
    systemctl daemon-reload
    systemctl enable --now getddo-staging-monitoring.timer
    echo 'Render targets with the operator role, copy them to observability/prometheus, then enable getddo-monitoring.'
    ;;
  *) echo 'Usage: monitoring-host.sh prepare|guard|secret|install' >&2; exit 2 ;;
esac
