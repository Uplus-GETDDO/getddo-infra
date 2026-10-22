"""Exercise target rendering and fail-closed host startup without touching a host."""
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('targets', ROOT / 'scripts/monitoring-targets.py')
targets = importlib.util.module_from_spec(spec)
spec.loader.exec_module(targets)


class MonitoringTests(unittest.TestCase):
    def test_target_contract(self):
        api, node = targets.targets({'A': '10.20.0.10', 'C': '10.20.1.10'}, '10.20.0.20')
        self.assertEqual([t['labels']['server'] for t in api], ['A', 'C'])
        self.assertEqual(api[0]['targets'], ['10.20.0.10:9091'])
        self.assertEqual(len(node), 3)
        self.assertTrue(all(t['labels']['environment'] == 'prod' for t in api + node))
        self.assertNotIn('instance', api[0]['labels'])
        with self.assertRaises(ValueError):
            targets.targets({'A': '203.0.113.1', 'C': '10.20.1.10'}, '10.20.0.20')

    def test_cloudwatch_actual_dimensions(self):
        # Render the existing source dashboard; use placeholder strings only as a unit fixture.
        source = (ROOT / 'terraform/prod/templates/cloudwatch-dashboard.json.tftpl').read_text()
        dashboard = targets.cloudwatch_dashboard(json.loads(source))
        self.assertGreater(len(dashboard['panels']), 10)
        serialized = json.dumps(dashboard)
        for namespace in ['AWS/ApplicationELB', 'AWS/RDS', 'AWS/ElastiCache', 'GETDDO/API', 'CWAgent']:
            self.assertIn(namespace, serialized)
        self.assertIn('environment=\\"dev\\"', serialized)

    def test_mount_guard_blocks_docker_recovery(self):
        script = (ROOT / 'scripts/monitoring-host.sh').read_text()
        guard = script[script.index('guard() {'):script.index('\ncase ${1:-}')]
        with tempfile.TemporaryDirectory() as name:
            marker = Path(name) / 'volume-id'
            marker.write_text('expected')
            guard = guard.replace('/srv/monitoring/.volume-id', str(marker))
            stubs = '''set -euo pipefail
load_volume() { DATA_VOLUME_ID=expected; device=mock; }
mountpoint() { [[ $MOUNTED == yes ]]; }
findmnt() { echo "$MOUNT_UUID"; }
blkid() { echo expected-uuid; }
'''
            for mounted, uuid, marker_value, success in [
                ('no', 'expected-uuid', 'expected', False),
                ('yes', 'wrong-uuid', 'expected', False),
                ('yes', 'expected-uuid', 'other-volume', False),
                ('yes', 'expected-uuid', 'expected', True),
            ]:
                marker.write_text(marker_value)
                env = dict(os.environ, MOUNTED=mounted, MOUNT_UUID=uuid)
                result = subprocess.run(['bash', '-c', stubs + guard + '\nguard\necho SECRET_THEN_DOCKER'], env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode == 0, success)
                self.assertEqual('SECRET_THEN_DOCKER' in result.stdout, success)

    def test_docker_startup_is_gated_before_restart_policy(self):
        unit = (ROOT / 'observability/systemd/docker-monitoring.conf').read_text()
        self.assertIn('RequiresMountsFor=/srv/monitoring', unit)
        self.assertLess(unit.index('monitoring-host.sh guard'), unit.index('monitoring-host.sh secret'))
        install = (ROOT / 'scripts/monitoring-host.sh').read_text()
        self.assertLess(install.index('/docker.service.d/monitoring.conf'), install.index('systemctl enable --now docker'))
        compose = (ROOT / 'observability/compose.yaml').read_text()
        self.assertEqual(compose.count('create_host_path: false'), 3)
        self.assertNotIn('9090:9090', compose)
        self.assertIn('127.0.0.1:3000:3000', compose)


    def test_backup_restores_prior_state_after_each_failure(self):
        source = (ROOT / 'scripts/monitoring-backup.sh').read_text()
        block = source.split('  backup)\n', 1)[1].split('    ;;', 1)[0]
        stubs = """set -euo pipefail
systemctl() {
  case $1 in
    is-active) [[ $UNIT_ACTIVE == yes ]];;
    stop) echo UNIT_STOP; [[ $FAIL_AT != unit-stop ]] || return 21;;
    start) echo UNIT_START; return "$RESTART_STATUS";;
  esac
}
docker() {
  case $2 in
    ps)
      if [[ $* == *--services* ]]; then
        [[ $CONTAINERS_ACTIVE != yes ]] || echo prometheus
      else
        [[ $FAIL_AT != running-check ]] || echo still-running
        [[ $FAIL_AT != ps ]] || return 25
      fi;;
    stop) echo CONTAINER_STOP; [[ $FAIL_AT != compose-stop ]] || return 22;;
    start) echo "CONTAINER_START $3"; return "$RESTART_STATUS";;
  esac
  return 0
}
tar() { echo OFFLINE_ARCHIVE; [[ $FAIL_AT != tar ]] || return 23; touch "$archive"; }
sha256sum() { [[ $FAIL_AT != checksum ]] || return 24; command sha256sum "$@"; }
"""
        with tempfile.TemporaryDirectory() as name:
            work = Path(name)
            (work / 'observability').mkdir()
            for unit, containers in [('yes', 'yes'), ('no', 'yes'), ('no', 'no')]:
                for failure, status in [('none', 0), ('unit-stop', 21), ('compose-stop', 22),
                                        ('running-check', 1), ('ps', 25), ('tar', 23), ('checksum', 24)]:
                    for restart_status in [0, 26]:
                        with self.subTest(unit=unit, containers=containers, failure=failure, restart=restart_status):
                            archive = work / 'backup'
                            archive.unlink(missing_ok=True)
                            Path(str(archive) + '.sha256').unlink(missing_ok=True)
                            env = dict(os.environ, source_dir=name, archive=str(archive),
                                       UNIT_ACTIVE=unit, CONTAINERS_ACTIVE=containers,
                                       FAIL_AT=failure, RESTART_STATUS=str(restart_status))
                            result = subprocess.run(['bash', '-c', stubs + block], env=env, text=True, capture_output=True)
                            recovery = unit == 'yes' or containers == 'yes'
                            self.assertEqual(result.returncode, status or (restart_status if recovery else 0), result.stderr)
                            self.assertEqual('UNIT_START' in result.stdout, unit == 'yes')
                            self.assertEqual('CONTAINER_START prometheus' in result.stdout, unit == 'no' and containers == 'yes')
                            self.assertEqual('OFFLINE_ARCHIVE' in result.stdout,
                                             failure in ['none', 'tar', 'checksum'])
                            if recovery and restart_status:
                                self.assertIn('recovery failed', result.stderr)

    def test_restore_guards_and_transferred_checksum(self):
        source = (ROOT / 'scripts/monitoring-backup.sh').read_text()
        prefix = source.split('  restore)\n', 1)[1].split('    [[ -z ', 1)[0]
        with tempfile.TemporaryDirectory() as name:
            archive = Path(name) / 'moved-backup.tar.gz'
            archive.write_bytes(b'private-backup-fixture')
            for active in ['docker', 'getddo-monitoring', 'none']:
                for valid in [True, False]:
                    with self.subTest(active=active, valid_checksum=valid):
                        checksum = hashlib.sha256(archive.read_bytes()).hexdigest() if valid else '0'*64
                        Path(str(archive) + '.sha256').write_text(checksum + '\n')
                        stubs = 'set -euo pipefail; systemctl() { [[ $3 == "$ACTIVE" ]]; };\n'
                        result = subprocess.run(['bash', '-c', stubs + prefix + '\necho VERIFIED_ARCHIVE'],
                                                env=dict(os.environ, archive=str(archive), ACTIVE=active),
                                                text=True, capture_output=True)
                        success = active == 'none' and valid
                        self.assertEqual(result.returncode == 0, success)
                        self.assertEqual('VERIFIED_ARCHIVE' in result.stdout, success)
                        if active != 'none':
                            self.assertNotIn(': OK', result.stdout)

    def test_prepare_refuses_mounted_device_and_existing_fstab(self):
        source = (ROOT / 'scripts/monitoring-host.sh').read_text()
        block = source.split('  prepare)\n', 1)[1].split('    ;;', 1)[0]
        # Replace only privileged paths/device check; all disk commands are shell mocks.
        block = block.replace('[[ -b $device ]]', ':')
        stubs = """set -euo pipefail
findmnt() { [[ $MOUNTED == yes ]]; }
blkid() { if [[ $* == *UUID* ]]; then echo fixture-uuid; else echo ext4; fi; }
mkfs.ext4() { echo UNEXPECTED_FORMAT >&2; return 99; }
wipefs() { return 99; }
mount() { echo MOUNT; }
install() { :; }
guard() { echo PREPARED; }
"""
        with tempfile.TemporaryDirectory() as name:
            work = Path(name)
            block = block.replace('/srv/monitoring', str(work / 'data')).replace('/etc/fstab', str(work / 'fstab'))
            for mounted, existing in [('yes', False), ('no', True), ('no', False)]:
                with self.subTest(mounted=mounted, existing=existing):
                    fstab = work / 'fstab'
                    original = f'UUID=old {work / "data"} ext4 defaults 0 2\n' if existing else ''
                    fstab.write_text(original)
                    config = work / 'config'
                    result = subprocess.run(['bash', '-c', stubs + block, 'prepare', 'prepare', 'vol-' + '1'*17],
                                            env=dict(os.environ, config=str(config), MOUNTED=mounted), text=True, capture_output=True)
                    success = mounted == 'no' and not existing
                    self.assertEqual(result.returncode == 0, success)
                    self.assertEqual('MOUNT' in result.stdout, success)
                    self.assertEqual('PREPARED' in result.stdout, success)
                    if success:
                        self.assertIn('defaults,nofail', fstab.read_text())
                    else:
                        self.assertEqual(fstab.read_text(), original)
                        self.assertFalse(config.exists())


if __name__ == '__main__':
    unittest.main()
