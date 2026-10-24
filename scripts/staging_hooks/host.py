"""Reviewed host actions. Secrets remain in process memory or short-lived /run files."""
import hashlib
import json
import os
import re
from pathlib import Path
import ssl
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from urllib.parse import urlsplit
import uuid

REPORTS = Path("/var/lib/getddo-loadtest/reports")

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
from staging_locust_install import StageRuntimeError


def command(args, *, timeout=120, data=None, cwd=None, env=None):
    result=subprocess.run(args,input=data,cwd=cwd,env=env,capture_output=True,timeout=timeout)
    if result.returncode: raise RuntimeError('Stage host action failed: '+Path(args[0]).name)
    return result.stdout


def parameters(names):
    names=['/getddo/staging/'+name for name in names]
    response=json.loads(command(['aws','--region','ap-northeast-2','ssm','get-parameters',
                          '--names',*names,'--with-decryption','--output','json']))
    if response.get('InvalidParameters') or {p['Name'] for p in response['Parameters']}!=set(names):
        raise ValueError('Required Stage settings are missing')
    return {p['Name'].rsplit('/',1)[-1]:p['Value'] for p in response['Parameters']}


def seed(manifest,actor):
    import pymysql
    d=manifest['deployment']
    if (d['db_identifier'] != 'getddo-staging-mysql' or not re.fullmatch(r'getddo-staging-mysql\.[a-z0-9]+\.ap-northeast-2\.rds\.amazonaws\.com', d['db_endpoint']) or actor.get('role') != 'USER' or actor.get('membership') != 'excellent'):
        raise ValueError('Seed must use the dedicated Stage database and USER actor')
    settings=parameters(['app/SPRING_DATASOURCE_URL','app/SPRING_DATASOURCE_USERNAME','app/SPRING_DATASOURCE_PASSWORD'])
    address=urlsplit(settings['SPRING_DATASOURCE_URL'].removeprefix('jdbc:'))
    if address.scheme!='mysql' or address.hostname!=d['db_endpoint'] or address.path!='/getddo':
        raise ValueError('Seed DB is outside this Stage deployment')
    identity=uuid.UUID(actor['user_id']).bytes
    connection=pymysql.connect(host=address.hostname,port=3306,user=settings['SPRING_DATASOURCE_USERNAME'],
        password=settings['SPRING_DATASOURCE_PASSWORD'],database='getddo',charset='utf8mb4',
        ssl=ssl.create_default_context(cafile='/etc/getddo/rds-ca.pem'),connect_timeout=10,read_timeout=15,write_timeout=15)
    with connection:
        with connection.cursor() as cursor:
            cursor.execute('SELECT name,role,status,membership FROM users WHERE id=%s',(identity,))
            current=cursor.fetchone()
            if current is None:
                cursor.execute("INSERT INTO users (id,name,role,status,membership,created_at,updated_at) VALUES (%s,'Stage smoke','USER','ACTIVE','EXCELLENT',UTC_TIMESTAMP(6),UTC_TIMESTAMP(6))",(identity,))
            elif tuple(current)!=('Stage smoke','USER','ACTIVE','EXCELLENT'):
                raise ValueError('Synthetic actor conflicts with existing data')
        connection.commit()
        access=parameters(['test-access/mysql-password'])
        with connection.cursor() as cursor:
            cursor.execute("CREATE USER IF NOT EXISTS 'stage_tester'@'%%' IDENTIFIED BY %s REQUIRE SSL",(access['mysql-password'],))
            cursor.execute("ALTER USER 'stage_tester'@'%%' IDENTIFIED BY %s REQUIRE SSL",(access['mysql-password'],))
            cursor.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON getddo.* TO 'stage_tester'@'%'")

    request=urllib.request.Request('http://127.0.0.1:8080/api/v1/events?page=1&size=1',
                                   headers={'X-User-ID':actor['user_id'],'X-User-Role':'USER','X-User-Membership':'excellent'})
    with urllib.request.urlopen(request,timeout=15) as response:
        body=json.load(response)
    if body.get('success') is not True or body.get('code')!='SUCCESS' or not isinstance(body.get('data',{}).get('items'),list):
        raise RuntimeError('Synthetic actor Stage API smoke failed')
    print('STAGE_SEED_OK')


def collect(manifest):
    """Use the authenticated public API to stop/collect the same master as the UI."""
    credentials=parameters(['locust/admin-user','locust/admin-password'])
    # Run the already pinned client in the master image. The host venv only needs
    # seed/auth helpers; raw login values travel on stdin, never Docker args/env.
    output_name = 'automation-' + uuid.uuid4().hex
    script = """import json,sys
from pathlib import Path
from loadtest.control import Control
payload=json.load(sys.stdin)
client=Control(payload['origin'],payload['username'],payload['password'])
client.stop()
client.collect(Path('/reports')/payload['output'])
"""
    payload=json.dumps({'origin':manifest['public_origin'],'username':credentials['admin-user'],
                        'password':credentials['admin-password'],'output':output_name}).encode()
    command(['docker','compose','-f',str(ROOT/'loadtest/compose.yaml'),'exec','-T','master','python3','-c',script],
            data=payload,cwd=ROOT,env=dict(os.environ,LOCUST_BIND_IP=manifest['deployment']['locust_instance']['private_ip']))
    reports=REPORTS/output_name
    with tempfile.TemporaryDirectory(prefix='collect-',dir='/run') as name:
        directory=Path(name)
        archive=directory/'locust.tar.gz'
        with tarfile.open(archive,'w:gz') as bundle:
            for path in sorted(reports.iterdir()):
                if path.is_symlink() or not path.is_file(): raise ValueError('Report path is unsafe')
                bundle.add(path,arcname=path.name,recursive=False)
            personal_root=Path('/opt/getddo-loadtest')
            sources=personal_root/'scenarios'
            candidates=list(sources.rglob('*')) if sources.is_dir() else []
            for path in sorted(candidates):
                if path.is_symlink(): raise ValueError('Scenario source symlink rejected')
                if path.is_file() and path.suffix in ('.py','.sql'):
                    if path.stat().st_size > 10*1024*1024:
                        raise ValueError('Scenario source exceeds archive limit')
                    bundle.add(path,arcname='sources/'+str(path.relative_to(sources)),recursive=False)
            active=personal_root/'active'/'scenario.py'
            if active.is_file():bundle.add(active,arcname='active-scenario.py',recursive=False)
            (directory/'run.json').write_text(json.dumps({'run_id':manifest['run_id'],
                'infra_source_sha':manifest['source_sha'],'created_at':manifest['created_at'],
                'expires_at':manifest['expires_at'],'collection':'locust'}))
            bundle.add(directory/'run.json',arcname='run.json')
        digest=hashlib.sha256()
        with archive.open('rb') as stream:
            for block in iter(lambda:stream.read(1024*1024),b''):digest.update(block)
        key=f"staging/runs/{manifest['run_id']}/results/locust-{digest.hexdigest()}.tar.gz"
        command(['aws','--region',manifest['region'],'s3api','put-object','--bucket',manifest['deploy_bucket'],
                 '--key',key,'--body',str(archive),'--expected-bucket-owner',manifest['account_id'],
                 '--server-side-encryption','AES256'],timeout=90)
        print(json.dumps({'key':key,'sha256':digest.hexdigest()}))


def main():
    from staging_lifecycle import validate_manifest
    if os.geteuid()!=0: raise RuntimeError('Stage host action requires root')
    payload=json.load(sys.stdin);m=validate_manifest(payload['manifest']);action=sys.argv[1]
    if action=='seed':seed(m,payload['actor'])
    elif action=='install':
        from staging_locust_install import install
        install(m,payload['actor'],ROOT)
    elif action=='collect':collect(m)
    else:raise ValueError('Unsupported Stage host action')


def cli():
    try:main()
    except Exception as error:
        # These codes survive in the SSM invocation after rollback deletes hosts.
        # All other exception messages/output may contain credentials.
        message = str(error) if isinstance(error, StageRuntimeError) else 'Stage host action failed: unexpected'
        print(message,file=sys.stderr)
        raise SystemExit(1) from None


if __name__=='__main__':cli()
