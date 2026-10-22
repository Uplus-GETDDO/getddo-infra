#!/usr/bin/env python3
"""검증된 Stage 대상만 공용 모니터에 등록한다. 운영 파일과 비밀값은 읽지 않는다."""
import argparse
import datetime as dt
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

ACCOUNT = '942466928325'
REGION = 'ap-northeast-2'
VPC = 'vpc-0fcf829db1692b713'
BUCKET = 'getddo-deploy-942466928325-ap-northeast-2'
REQUEST = 'staging/monitoring/request.json'
STATUS = 'staging/monitoring/status.json'
class MissingRequest(Exception):
    pass


FILES = ('staging-api-targets.json', 'staging-node-targets.json', 'staging-locust-targets.json')


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def aws(*args):
    result = subprocess.run(['aws', '--region', REGION, '--no-cli-pager', *args, '--output', 'json'],
                            capture_output=True, text=True, timeout=45)
    if result.returncode:
        if args[:2] == ('s3api', 'get-object') and '(NoSuchKey)' in result.stderr:
            raise MissingRequest()
        # AWS diagnostics and incoming request strings are never copied to the journal.
        raise RuntimeError('AWS request failed: ' + args[0] + '/' + args[1])
    return json.loads(result.stdout) if result.stdout.strip() else {}


def run_id(manifest):
    value = manifest.get('run_id', '')
    if not isinstance(value, str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{7,62}', value):
        raise ValueError('Invalid run identifier')
    return value


def render(manifest, client=aws, now=None):
    run_id(manifest)
    if (manifest.get('account_id'), manifest.get('region'), manifest.get('vpc_id')) != (ACCOUNT, REGION, VPC):
        raise ValueError('Stage account/region/VPC mismatch')
    expiry = dt.datetime.fromisoformat(manifest['expires_at'].replace('Z', '+00:00'))
    if expiry.tzinfo is None or expiry <= (now or dt.datetime.now(dt.timezone.utc)):
        raise ValueError('Stage targets have expired')
    hosts = dict(manifest['api_instances'])
    if set(hosts) != {'a', 'c'}:
        raise ValueError('Both exact Stage API servers required')
    hosts['locust'] = manifest['locust_instance']
    ids = [host['id'] for host in hosts.values()]
    if len(set(ids)) != 3 or any(not re.fullmatch(r'i-[a-f0-9]{17}', i) for i in ids):
        raise ValueError('Three distinct exact instance IDs required')
    addresses = [ipaddress.ip_address(host['private_ip']) for host in hosts.values()]
    if len(set(addresses)) != 3 or any(ip not in ipaddress.ip_network('10.20.0.0/16') for ip in addresses):
        raise ValueError('Distinct private VPC addresses required')
    if client('sts', 'get-caller-identity')['Account'] != ACCOUNT:
        raise ValueError('Monitor account mismatch')
    response = client('ec2', 'describe-instances', '--instance-ids', *ids)
    live = [i for r in response['Reservations'] for i in r['Instances']]
    if len(live) != 3 or {i['InstanceId'] for i in live} != set(ids):
        raise ValueError('Exact live instance set mismatch')
    for server, host in hosts.items():
        instance = next(i for i in live if i['InstanceId'] == host['id'])
        tags = {t['Key']: t['Value'] for t in instance.get('Tags', [])}
        expected = 'getddo-staging-locust' if server == 'locust' else 'getddo-staging-api-' + server
        if (instance['VpcId'] != VPC or instance['PrivateIpAddress'] != host['private_ip'] or
                instance['State']['Name'] != 'running' or tags.get('Environment') != 'staging' or
                tags.get('Name') != expected or tags.get('RunId') != manifest['run_id']):
            raise ValueError('Stage instance identity/tag/state mismatch')
    def target(server, port):
        return {'targets': [hosts[server]['private_ip'] + ':' + str(port)],
                'labels': {'environment': 'staging', 'server': server}}
    return {FILES[0]: [target(s, 9091) for s in ('a', 'c')],
            FILES[1]: [target(s, 9100) for s in ('a', 'c', 'locust')],
            FILES[2]: [target('locust', 9646)]}


def cloudwatch_dashboard(manifest):
    """Terraform이 출력한 Stage dimension만 사용하며 운영 자원은 선택하지 않는다."""
    db, cache = manifest['db_identifier'], manifest['redis_cluster_id']
    if not all(isinstance(x, str) and re.fullmatch(r'getddo-staging-[a-z0-9-]+', x) for x in (db, cache)):
        raise ValueError('Stage-only database/cache identifiers required')
    prefix = f'arn:aws:elasticloadbalancing:{REGION}:{ACCOUNT}:'
    alb, tg = manifest['load_balancer_arn'], manifest['target_group_arn']
    if not re.fullmatch(re.escape(prefix) + r'loadbalancer/app/getddo-staging-[a-z0-9-]+/[a-f0-9]+', alb):
        raise ValueError('Stage ALB ARN required')
    if not re.fullmatch(re.escape(prefix) + r'targetgroup/getddo-staging-[a-z0-9-]+/[a-f0-9]+', tg):
        raise ValueError('Stage TG ARN required')
    load_balancer = alb.split(':loadbalancer/', 1)[1]
    target_group = tg.split(':', 5)[5]
    panels = []
    def panel(title, namespace, metrics, dimensions, unit='short', statistic='Average'):
        index = len(panels)
        panels.append({'id': index+1, 'title': title, 'type': 'timeseries',
                       'datasource': {'type': 'cloudwatch', 'uid': 'prod-cloudwatch'},
                       'gridPos': {'x': (index % 2)*12, 'y': (index // 2)*8, 'w': 12, 'h': 7},
                       'fieldConfig': {'defaults': {'unit': unit}, 'overrides': []},
                       'targets': [{'refId': chr(65+i), 'region': REGION, 'queryMode': 'Metrics',
                                    'metricQueryType': 0, 'metricEditorMode': 0,
                                    'namespace': namespace, 'metricName': metric, 'statistic': statistic,
                                    'period': '60', 'matchExact': True,
                                    'dimensions': {k: [v] for k,v in dimensions.items()}} for i,metric in enumerate(metrics)]})
    rd = {'DBInstanceIdentifier': db}
    panel('RDS CPU (%)', 'AWS/RDS', ['CPUUtilization'], rd, 'percent')
    panel('RDS connections', 'AWS/RDS', ['DatabaseConnections'], rd)
    panel('RDS free memory / disk', 'AWS/RDS', ['FreeableMemory', 'FreeStorageSpace'], rd, 'bytes')
    panel('RDS read / write I/O latency', 'AWS/RDS', ['ReadLatency', 'WriteLatency'], rd, 's')
    panel('RDS read / write IOPS', 'AWS/RDS', ['ReadIOPS', 'WriteIOPS'], rd, 'iops')
    panel('RDS I/O queue', 'AWS/RDS', ['DiskQueueDepth'], rd)
    # CPU-credit metrics have a native 5-minute period.
    panel('RDS CPU credit balance (5m)', 'AWS/RDS', ['CPUCreditBalance'], rd)
    panels[-1]['targets'][0]['period'] = '300'
    redis = {'CacheClusterId': cache, 'CacheNodeId': '0001'}
    panel('Redis CPU / engine CPU (%)', 'AWS/ElastiCache', ['CPUUtilization', 'EngineCPUUtilization'], redis, 'percent')
    panel('Redis memory usage (%)', 'AWS/ElastiCache', ['DatabaseMemoryUsagePercentage'], redis, 'percent')
    panel('Redis connections', 'AWS/ElastiCache', ['CurrConnections'], redis)
    panel('Redis new connections / evictions per minute', 'AWS/ElastiCache', ['NewConnections', 'Evictions'], redis, statistic='Sum')
    panel('Redis network bytes per minute', 'AWS/ElastiCache', ['NetworkBytesIn', 'NetworkBytesOut'], redis, 'bytes', 'Sum')
    ad = {'LoadBalancer': load_balancer}
    panel('ALB requests per minute', 'AWS/ApplicationELB', ['RequestCount'], ad, statistic='Sum')
    panel('ALB-generated / target 5xx per minute', 'AWS/ApplicationELB', ['HTTPCode_ELB_5XX_Count', 'HTTPCode_Target_5XX_Count'], ad, statistic='Sum')
    panel('ALB target connection errors per minute', 'AWS/ApplicationELB', ['TargetConnectionErrorCount'], ad, statistic='Sum')
    panel('ALB target p95 latency', 'AWS/ApplicationELB', ['TargetResponseTime'], ad, 's', 'p95')
    panel('ALB target p99 latency', 'AWS/ApplicationELB', ['TargetResponseTime'], ad, 's', 'p99')
    dims = dict(ad, TargetGroup=target_group)
    panel('ALB minimum healthy targets', 'AWS/ApplicationELB', ['HealthyHostCount'], dims, statistic='Minimum')
    panel('ALB maximum unhealthy targets', 'AWS/ApplicationELB', ['UnHealthyHostCount'], dims, statistic='Maximum')
    for server, host in {**manifest['api_instances'], 'locust': manifest['locust_instance']}.items():
        panel(server + ' EC2 CPU credit balance (5m)', 'AWS/EC2', ['CPUCreditBalance'], {'InstanceId': host['id']})
        panels[-1]['targets'][0]['period'] = '300'
    return {'uid': 'getddo-staging-cloudwatch', 'title': 'GETDDO Staging — AWS resources',
            'schemaVersion': 39, 'version': 1, 'refresh': '1m', 'tags': ['staging'],
            'time': {'from': 'now-1h', 'to': 'now'},
            'description': 'Run ' + run_id(manifest) + '. Original CloudWatch resolution; not 5-second telemetry.', 'panels': panels}


def atomic(path, value):
    fd, name = tempfile.mkstemp(prefix='.staging-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(canonical(value)+b'\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, 0o644)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def publish(request, directory, dashboards, client=aws):
    if not isinstance(request, dict) or set(request) != ({'action','manifest','request_id'} if request.get('action') == 'collect' else {'action','manifest'}):
        raise ValueError('Unexpected monitoring request fields')
    action, manifest = request['action'], request['manifest']
    if action not in ('publish', 'remove', 'collect') or not isinstance(manifest, dict):
        raise ValueError('Unexpected monitoring action')
    if action == 'collect' and not re.fullmatch(r'[a-f0-9]{32}', request.get('request_id', '')):
        raise ValueError('Unique collection request identity required')
    run = run_id(manifest)
    directory, dashboards = Path(directory), Path(dashboards)
    if any(p.is_symlink() or not p.is_dir() for p in (directory, dashboards)):
        raise ValueError('Existing regular monitoring directories required')
    owner = directory / '.staging-owner.json'
    dashboard = dashboards / 'staging-cloudwatch.json'
    paths = [directory/name for name in FILES]+[owner,dashboard]
    if any(p.is_symlink() for p in paths):
        raise ValueError('Refuse symlink monitoring targets')
    lock = directory / '.staging-targets.lock'
    if lock.is_symlink():
        raise ValueError('Refuse symlink monitoring lock')
    with lock.open('a') as stream:
        os.chmod(lock, 0o600)
        fcntl.flock(stream, fcntl.LOCK_EX)
        current = json.loads(owner.read_bytes()) if owner.exists() else None
        if current and current.get('run_id') != run:
            raise ValueError('Monitoring targets belong to another run')
        if action == 'collect':
            if not current or current.get('manifest') != manifest:
                raise ValueError('Export requires the exact previously validated Stage manifest')
            receipt = export_metrics(manifest, dashboards, Path('/run/getddo-staging-monitoring'), client)
            return {'run_id':run, 'action':action, 'status':'applied', 'results':receipt}
        values = render(manifest, client) if action == 'publish' else {name: [] for name in FILES}
        rendered = cloudwatch_dashboard(manifest) if action == 'publish' else None
        backups = {p: p.read_bytes() if p.exists() else None for p in paths}
        try:
            for name,value in values.items():atomic(directory/name,value)
            if rendered is not None:atomic(dashboard,rendered)
            if action == 'publish':atomic(owner,{'run_id':run,'manifest':manifest})
            else:owner.unlink(missing_ok=True)
        except Exception:
            for path,content in backups.items():
                if content is None:path.unlink(missing_ok=True)
                else:
                    fd, name = tempfile.mkstemp(prefix='.restore-stage-',dir=path.parent)
                    with os.fdopen(fd,'wb') as restore:restore.write(content)
                    os.chmod(name,0o644);os.replace(name,path)
            raise
    return {'run_id':run,'action':action,'status':'applied'}


def export_metrics(manifest, dashboards, state, client=aws, opener=None):
    """Export provisioned Stage queries through Grafana; credentials never leave this process."""
    import gzip
    import http.cookiejar
    import urllib.request
    origin = 'https://dzc8oneey9mmx.cloudfront.net'
    created = dt.datetime.fromisoformat(manifest['created_at'].replace('Z', '+00:00'))
    expiry = dt.datetime.fromisoformat(manifest['expires_at'].replace('Z', '+00:00'))
    now = dt.datetime.now(dt.timezone.utc)
    if created.tzinfo is None or expiry.tzinfo is None or not dt.timedelta(0) < expiry-created <= dt.timedelta(days=1):
        raise ValueError('Bounded Stage export time range required')
    end = min(now, expiry)
    if created >= end:
        raise ValueError('Stage export time range is empty')
    start_ms, end_ms = str(int(created.timestamp()*1000)), str(int(end.timestamp()*1000))
    if opener is None:
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    def post(path, value):
        request = urllib.request.Request(origin+'/grafana/'+path, canonical(value),
            headers={'Content-Type':'application/json', 'Origin':origin, 'Referer':origin+'/grafana/'})
        with opener.open(request, timeout=20) as response:
            content = response.read(32*1024*1024+1)
        if len(content) > 32*1024*1024:
            raise ValueError('Stage export response exceeds size limit')
        return json.loads(content)
    password = client('ssm', 'get-parameter', '--name', '/getddo/monitoring/grafana/admin-password',
                      '--with-decryption')['Parameter']['Value']
    post('login', {'user':'admin', 'password':password})
    dashboards = Path(dashboards)
    sources = [json.loads((dashboards/'overview.json').read_bytes()), cloudwatch_dashboard(manifest)]
    results = []
    for dashboard in sources:
        queries = []
        for panel in dashboard['panels']:
            if panel.get('type') == 'text':
                continue
            for target in panel['targets']:
                query = dict(target, datasource=panel.get('datasource',target.get('datasource')),
                             refId=f'Q{len(queries)}', maxDataPoints=3600,
                             intervalMs=max(5000, int((end-created).total_seconds()*1000/3600)))
                if 'expr' in query:
                    query.update(expr=query['expr'].replace('$server','.*'), range=True, instant=False, format='time_series', interval='5s')
                queries.append(query)
        result = post('api/ds/query', {'from':start_ms, 'to':end_ms, 'queries':queries})
        entries = result.get('results',{})
        if set(entries) != {q['refId'] for q in queries} or any(value.get('error') for value in entries.values()):
            raise RuntimeError('Stage metrics export contains query failures')
        results.append({'dashboard':dashboard,'queries':queries,'response':result})
    state = Path(state)
    output = state/'monitoring.json.gz'
    with gzip.open(output,'wb') as stream:
        stream.write(canonical({'run_id':run_id(manifest),'from':start_ms,'to':end_ms,'results':results}))
    output.chmod(0o600)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    key = f'staging/runs/{run_id(manifest)}/results/monitoring-{digest}.json.gz'
    client('s3api','put-object','--bucket',BUCKET,'--key',key,'--body',str(output),
           '--expected-bucket-owner',ACCOUNT,'--server-side-encryption','AES256')
    return {'key':key,'sha256':digest}


def watch_once(directory, dashboards, state, client=aws):
    """제한된 S3 설정만 받아 적용하며 임의 명령·코드를 실행하지 않는다."""
    state = Path(state)
    if state.is_symlink():
        raise ValueError('Refuse symlink state directory')
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    state.chmod(0o700)
    request_path = state/'request.json'
    if any((state/name).is_symlink() for name in ('request.json', 'applied.json', 'status.json')):
        raise ValueError('Refuse symlink state files')
    existing = client('s3api', 'list-objects-v2', '--bucket', BUCKET, '--prefix', REQUEST,
                      '--max-keys', '1', '--expected-bucket-owner', ACCOUNT)
    if not any(item['Key'] == REQUEST for item in existing.get('Contents', [])):
        return
    try:
        client('s3api','get-object','--bucket',BUCKET,'--key',REQUEST,
               '--expected-bucket-owner',ACCOUNT,str(request_path))
    except MissingRequest:
        return
    request_path.chmod(0o600)
    if request_path.stat().st_size > 65536:
        raise ValueError('Monitoring request too large')
    request = json.loads(request_path.read_bytes())
    digest = hashlib.sha256(canonical(request)).hexdigest()
    receipt_path = state/'applied.json'
    if receipt_path.exists() and json.loads(receipt_path.read_bytes()).get('request_sha256') == digest:
        return
    try:result=publish(request,directory,dashboards,client)
    except Exception as error:
        result={'run_id':request.get('manifest',{}).get('run_id','invalid'),'action':request.get('action','invalid'),
                'status':'failed','error':type(error).__name__}
    result['request_sha256']=digest
    result_path=state/'status.json';atomic(result_path,result);result_path.chmod(0o600)
    client('s3api','put-object','--bucket',BUCKET,'--key',STATUS,'--body',str(result_path),
           '--expected-bucket-owner',ACCOUNT,'--server-side-encryption','AES256')
    if result['status']=='applied':atomic(receipt_path,result);receipt_path.chmod(0o600)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['watch'])
    parser.add_argument('--directory',type=Path,default=Path('/opt/getddo-monitoring/observability/prometheus'))
    parser.add_argument('--dashboards',type=Path,default=Path('/opt/getddo-monitoring/observability/grafana/staging-dashboards'))
    parser.add_argument('--state',type=Path,default=Path('/run/getddo-staging-monitoring'))
    args=parser.parse_args()
    try:watch_once(args.directory,args.dashboards,args.state)
    except Exception as error:
        print('Stage monitoring update failed: '+type(error).__name__)
        return 1
    return 0


if __name__=='__main__':
    raise SystemExit(main())
