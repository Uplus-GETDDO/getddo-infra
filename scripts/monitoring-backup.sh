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
    systemctl stop getddo-monitoring
    # A failed/inactive unit can leave Docker-restarted containers alive. Stop and verify them directly.
    cd "$source_dir/observability"
    docker compose stop --timeout 60
    [[ -z $(docker compose ps --quiet --status running) ]]
    trap 'systemctl start getddo-monitoring' EXIT
    # compose stop waits for graceful database shutdown; archive WAL and SQLite together.
    tar -C /srv/monitoring -czf "$archive" prometheus grafana
    sha256sum "$archive" > "$archive.sha256"
    chmod 0600 "$archive" "$archive.sha256"
    ;;
  restore)
    ! systemctl is-active --quiet getddo-monitoring
    ! systemctl is-active --quiet docker
    sha256sum -c "$archive.sha256"
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
