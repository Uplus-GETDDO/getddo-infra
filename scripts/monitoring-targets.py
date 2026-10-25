#!/usr/bin/env python3
"""Resolve live private targets by Terraform's existing host-name contract; no secrets."""
import argparse
import ipaddress
import json
from pathlib import Path
import subprocess


def aws(*args):
    return json.loads(subprocess.check_output(['aws', '--region', 'ap-northeast-2', *args, '--output', 'json']))


def private_ip(value):
    if ipaddress.ip_address(value) not in ipaddress.ip_network('10.20.0.0/16'):
        raise ValueError('Target must be in the existing production VPC CIDR')
    return value


def targets(hosts, monitor_ip):
    api, node = [], []
    for server in ['A', 'C']:
        ip = private_ip(hosts[server])
        labels = {'environment': 'prod', 'server': server}
        api.append({'targets': [f'{ip}:9091'], 'labels': labels})
        node.append({'targets': [f'{ip}:9100'], 'labels': labels})
    node.append({'targets': [f'{private_ip(monitor_ip)}:9100'],
                 'labels': {'environment': 'prod', 'server': 'monitoring'}})
    return api, node


def cloudwatch_dashboard(body):
    """Reuse exact current resource dimensions and legacy app searches, without State."""
    panels = []
    for widget in body['widgets']:
        if widget['type'] != 'metric':
            continue
        props = widget['properties']
        targets = []
        previous = []
        for row in props['metrics']:
            ref = chr(65 + len(targets))
            target = {'refId': ref, 'region': props.get('region', 'ap-northeast-2'), 'queryMode': 'Metrics',
                      'period': str(props.get('period', 60)), 'statistic': props.get('stat', 'Average'),
                      'matchExact': True}
            if isinstance(row[0], dict):
                target.update(metricQueryType=1, metricEditorMode=1, expression=row[0]['expression'])
            else:
                values = [previous[i] if value == '.' else value for i, value in enumerate(row) if not isinstance(value, dict)]
                previous = values
                target.update(metricQueryType=0, metricEditorMode=0, namespace=values[0], metricName=values[1],
                              dimensions={values[i]: [values[i+1]] for i in range(2, len(values), 2)})
            targets.append(target)
        i = len(panels)
        panels.append({'id': i+1, 'title': props['title'], 'type': 'timeseries',
                       'datasource': {'type': 'cloudwatch', 'uid': 'prod-cloudwatch'},
                       'gridPos': {'x': (i % 2)*12, 'y': (i // 2)*8, 'w': 12, 'h': 8}, 'targets': targets})
    return {'uid': 'getddo-prod-cloudwatch', 'title': 'GETDDO Prod — CloudWatch (legacy dimensions)',
            'schemaVersion': 39, 'version': 1, 'refresh': '1m', 'tags': ['prod'],
            'time': {'from': 'now-3h', 'to': 'now'}, 'panels': panels}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cloudwatch-dashboard', type=Path, required=True)
    args = parser.parse_args()
    dashboard = aws('cloudwatch', 'get-dashboard', '--dashboard-name', 'getddo-dev-overview')
    args.cloudwatch_dashboard.parent.mkdir(parents=True, exist_ok=True)
    args.cloudwatch_dashboard.write_text(json.dumps(cloudwatch_dashboard(json.loads(dashboard['DashboardBody'])), indent=2) + '\n')
    args.cloudwatch_dashboard.chmod(0o644)
    hosts = {}
    vpcs, groups = set(), []
    for server, name in [('A', 'getddo-dev-api'), ('C', 'getddo-dev-api-c'), ('monitoring', 'getddo-prod-monitoring')]:
        result = aws('ec2', 'describe-instances', '--filters', f'Name=tag:Name,Values={name}', 'Name=instance-state-name,Values=running')
        instances = [i for r in result['Reservations'] for i in r['Instances']]
        if len(instances) != 1:
            raise ValueError(f'Expected exactly one running {server} host')
        host = instances[0]
        hosts[server] = host['PrivateIpAddress']
        vpcs.add(host['VpcId'])
        groups.append({g['GroupId'] for g in host['SecurityGroups']})
    if len(vpcs) != 1 or not groups[0].intersection(groups[1]):
        raise ValueError('Targets must share the expected VPC and API security group')
    api, node = targets(hosts, hosts['monitoring'])
    args.output.mkdir(parents=True, exist_ok=True)
    for name, data in [('api-targets.json', api), ('node-targets.json', node)]:
        temp = args.output / (name + '.tmp')
        temp.write_text(json.dumps(data, indent=2) + '\n')
        temp.chmod(0o644)
        temp.replace(args.output / name)


if __name__ == '__main__':
    main()
