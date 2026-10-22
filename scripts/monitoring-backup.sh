#!/usr/bin/env bash
# Offline TSDB + Grafana SQLite backup. Archives are private, encrypted off-host by operator.
set -euo pipefail
umask 077
[[ $EUID == 0 ]]
source_dir=$(cd -- "$(dirname -- "$0")/.." && pwd)
"$source_dir/scripts/monitoring-host.sh" guard
archive=$(realpath -m "${2:?Usage: monitoring-backup.sh backup|restore absolute-archive-path}")
[[ $archive != /srv/monitoring/* && $archive == /* ]]
case ${1:-} in
  backup)
    [[ ! -e $archive && ! -e $archive.sha256 ]]
    unit_active=false
    if systemctl is-active --quiet getddo-monitoring; then unit_active=true; fi
    cd "$source_dir/observability"
    running_services=$(docker compose ps --services --status running)
    recover() {
      status=$?
      trap - EXIT
      if (( status != 0 )); then
        rm -f -- "$archive" "$archive.sha256" || echo "Failed to remove incomplete backup files." >&2
      fi
      restart_status=0
      if "$unit_active"; then
        systemctl start getddo-monitoring || restart_status=$?
      elif [[ -n $running_services ]]; then
        mapfile -t services <<< "$running_services"
        docker compose start "${services[@]}" || restart_status=$?
      fi
      if (( restart_status != 0 )); then
        echo "Monitoring recovery failed (exit $restart_status); original backup exit $status." >&2
        if (( status == 0 )); then status=$restart_status; fi
      fi
      exit "$status"
    }
    trap recover EXIT
    systemctl stop getddo-monitoring
    # A failed/inactive unit can leave Docker-restarted containers alive. Stop and verify them directly.
    docker compose stop --timeout 60
    running=$(docker compose ps --quiet --status running)
    [[ -z $running ]]
    # compose stop waits for graceful database shutdown; archive WAL and SQLite together.
    tar -C /srv/monitoring -czf "$archive" prometheus grafana
    sha256sum "$archive" | cut -d ' ' -f 1 > "$archive.sha256"
    chmod 0600 "$archive" "$archive.sha256"
    ;;
  restore)
    if systemctl is-active --quiet getddo-monitoring; then
      echo 'Stop getddo-monitoring before restore.' >&2; exit 1
    fi
    if systemctl is-active --quiet docker; then
      echo 'Stop Docker before restore.' >&2; exit 1
    fi
    checksum=$(cat "$archive.sha256")
    [[ $checksum =~ ^[0-9a-f]{64}$ ]]
    printf '%s  %s\n' "$checksum" "$archive" | sha256sum -c -
    [[ -z $(ls -A /srv/monitoring/prometheus) && -z $(ls -A /srv/monitoring/grafana) ]]
    python3 - "$archive" <<'PY'
import sys, tarfile
from pathlib import PurePosixPath
with tarfile.open(sys.argv[1]) as archive:
    for member in archive.getmembers():
        path = PurePosixPath(member.name)
        if path.is_absolute() or '..' in path.parts or path.parts[0] not in ['prometheus', 'grafana'] or not (member.isfile() or member.isdir()):
            raise ValueError('Unsafe backup member')
    archive.extractall('/srv/monitoring')
PY
    chown -R 65534:65534 /srv/monitoring/prometheus
    chown -R 472:472 /srv/monitoring/grafana
    echo 'Restore complete; inspect data and start getddo-monitoring explicitly.'
    ;;
  *) exit 2 ;;
esac
