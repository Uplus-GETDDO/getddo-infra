#!/usr/bin/env bash
# Local/CI tools only. No daemon, service installation, State or AWS calls.
set -euo pipefail
work=${1:?Usage: install-monitoring-check-tools.sh output-directory}
mkdir -p "$work"
curl -fsSL --proto '=https' --tlsv1.2 https://github.com/prometheus/prometheus/releases/download/v3.15.1/prometheus-3.15.1.linux-amd64.tar.gz -o "$work/prometheus.tar.gz"
printf '%s  %s\n' 6a726885328ad9efebbf36e876ce2fb6225468a71fd1e7ce3ba666d5f4d97a84 "$work/prometheus.tar.gz" | sha256sum -c -
tar -xzf "$work/prometheus.tar.gz" -C "$work" --strip-components=1 prometheus-3.15.1.linux-amd64/promtool
curl -fsSL --proto '=https' --tlsv1.2 https://github.com/docker/compose/releases/download/v5.6.1/docker-compose-linux-x86_64 -o "$work/docker-compose"
printf '%s  %s\n' 47737f4236e6d3ccea7291995203f8f018058783b4c3a9469119811a0b5db8c0 "$work/docker-compose" | sha256sum -c -
chmod +x "$work/promtool" "$work/docker-compose"
