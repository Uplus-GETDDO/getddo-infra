"""Exercise target rendering and fail-closed host startup without touching a host."""
import importlib.util
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


    def test_backup_quiesces_containers_even_if_unit_is_inactive(self):
        source = (ROOT / 'scripts/monitoring-backup.sh').read_text()
        block = source.split('  backup)\n', 1)[1].split('    ;;', 1)[0]
        with tempfile.TemporaryDirectory() as name:
            work = Path(name)
            (work / 'observability').mkdir()
            stubs = """set -euo pipefail
systemctl() { :; }
docker() {
  if [[ $2 == stop ]]; then echo DIRECT_DOCKER_STOP; return 0; fi
  if [[ $2 == ps && $CONTAINER_RUNNING == yes ]]; then echo container; fi
  return 0
}
tar() { echo OFFLINE_ARCHIVE; touch "$archive"; }
"""
            for running in ['yes', 'no']:
                archive = work / ('backup-' + running)
                env = dict(os.environ, source_dir=name, archive=str(archive), CONTAINER_RUNNING=running)
                result = subprocess.run(['bash', '-c', stubs + block], env=env, text=True, capture_output=True)
                self.assertIn('DIRECT_DOCKER_STOP', result.stdout)
                self.assertEqual('OFFLINE_ARCHIVE' in result.stdout, running == 'no')
                self.assertEqual(result.returncode == 0, running == 'no')
        self.assertIn('! systemctl is-active --quiet docker', source)


if __name__ == '__main__':
    unittest.main()
