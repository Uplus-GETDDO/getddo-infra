#!/usr/bin/env python3
"""Validate rendered Compose, YAML, dashboards and PromQL without a Docker daemon."""
from pathlib import Path
import json
import os
import subprocess
import tempfile
import yaml

ROOT = Path(__file__).resolve().parents[1]
OBS = ROOT / 'observability'


def main():
    for path in OBS.rglob('*'):
        if path.suffix in ['.yaml', '.yml']:
            yaml.safe_load(path.read_text())
        elif path.suffix == '.json':
            json.loads(path.read_text())
    # YAML flow-list commas can accidentally turn tmpfs options into extra mount paths.
    services = yaml.safe_load((OBS / 'compose.yaml').read_text())['services']
    for service in services.values():
        tmpfs = service.get('tmpfs', [])
        if isinstance(tmpfs, str):
            mounts = [tmpfs]
        elif isinstance(tmpfs, list):
            mounts = tmpfs
        else:
            raise ValueError('tmpfs must be a string or a list of strings')
        for mount in mounts:
            if not isinstance(mount, str) or not mount.split(':', 1)[0].startswith('/'):
                raise ValueError('Every tmpfs mount must have an absolute container path; quote comma-separated options')
    compose = os.environ.get('COMPOSE_BIN', 'docker')
    command = [compose] if 'COMPOSE_BIN' in os.environ else [compose, 'compose']
    subprocess.run(command + ['-f', str(OBS / 'compose.yaml'), 'config', '--quiet'], check=True,
                   env=dict(os.environ, GRAFANA_BIND_IP='10.0.0.10', GRAFANA_ROOT_URL='https://example.cloudfront.net/grafana/'))
    promtool = os.environ.get('PROMTOOL_BIN', 'promtool')
    subprocess.run([promtool, 'check', 'config', '--syntax-only', str(OBS / 'prometheus/prometheus.yml')], check=True)
    # Rule checking parses every provisioned PromQL expression, including template filters.
    with tempfile.TemporaryDirectory(prefix='getddo-promql-') as name:
        rules = []
        for dashboard in (OBS / 'grafana/dashboards').glob('*.json'):
            for panel in json.loads(dashboard.read_text())['panels']:
                for target in panel.get('targets', []):
                    if 'expr' in target:
                        rules.append({'record': f'validation:panel_{len(rules)}', 'expr': target['expr'].replace('$server', '.*')})
        path = Path(name) / 'rules.yml'
        path.write_text(yaml.safe_dump({'groups': [{'name': 'validation', 'rules': rules}]}))
        subprocess.run([promtool, 'check', 'rules', str(path)], check=True)
    print('PASS: Compose, monitoring YAML/JSON, Prometheus configuration and dashboard PromQL')


if __name__ == '__main__':
    main()
