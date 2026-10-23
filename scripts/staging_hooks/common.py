"""Immutable-bundle staging integration. Never forwards secret-bearing diagnostics."""
import base64
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'scripts'))


def command(argv, *, cwd=ROOT):
    result = subprocess.run(argv, cwd=cwd, capture_output=True, timeout=1800)
    if result.returncode:
        raise RuntimeError('Stage integration command failed: '+Path(argv[0]).name)
    return result.stdout


def checksum(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def inside(path):
    target = (ROOT/path).resolve()
    if not target.is_relative_to(ROOT) or target == ROOT:
        raise ValueError('Integration input must be inside immutable bundle')
    return target


class Hooks:
    def __init__(self, manifest, aws=None):
        from staging_lifecycle import validate_manifest, check_owner
        self.manifest = validate_manifest(manifest)
        if aws is None:
            from staging_aws import AWS
            aws = AWS()
        self.aws = aws
        check_owner(self.aws.owner(), manifest)
        self.clients = self.aws.clients

    def remote(self, instance, script, timeout=900):
        """Only exactly identified Stage EC2 in the current run may execute commands."""
        m = self.manifest; d = m['deployment']
        expected = {host['id'] for host in d['api_instances'].values()} | {d['locust_instance']['id']}
        if instance not in expected:
            raise ValueError('Remote target is outside Stage manifest')
        rows = [i for r in self.clients['ec2'].describe_instances(InstanceIds=[instance])['Reservations'] for i in r['Instances']]
        if len(rows) != 1:
            raise ValueError('Remote target response mismatch')
        tags = {t['Key']:t['Value'] for t in rows[0].get('Tags', [])}
        if rows[0]['VpcId'] != d['vpc_id'] or rows[0]['State']['Name'] != 'running' or tags.get('Environment') != 'staging' or tags.get('RunId') != m['run_id']:
            raise ValueError('Remote Stage ownership mismatch')
        ssm = self.clients['ssm']
        invocation = ssm.send_command(InstanceIds=[instance], DocumentName='AWS-RunShellScript',
            Parameters={'commands':['set -eu\numask 077\n'+script], 'executionTimeout':[str(timeout)]})['Command']['CommandId']
        deadline = time.monotonic()+timeout+30
        while time.monotonic()<deadline:
            try:
                response = ssm.get_command_invocation(CommandId=invocation, InstanceId=instance)
            except ssm.exceptions.InvocationDoesNotExist:
                time.sleep(2); continue
            if response['Status']=='Success' and response.get('ResponseCode')==0:
                return response.get('StandardOutputContent','')
            if response['Status'] not in ('Pending','InProgress','Delayed'):
                raise RuntimeError('Stage remote integration failed: '+response['Status'])
            time.sleep(3)
        raise RuntimeError('Stage remote integration outcome is unresolved')

    def monitoring(self, action, timeout=120):
        m=self.manifest
        if 'deployment' not in m:
            return None
        request={'action':action, 'manifest':dict(m['deployment'], run_id=m['run_id'],
                    expires_at=m['expires_at'], created_at=m['created_at'])}
        if action == 'collect':
            import uuid
            request['request_id'] = uuid.uuid4().hex
        payload=json.dumps(request,sort_keys=True,separators=(',',':')).encode()
        digest=hashlib.sha256(payload).hexdigest()
        self.clients['s3'].put_object(Bucket=m['deploy_bucket'],Key='staging/monitoring/request.json',
                                      Body=payload,ServerSideEncryption='AES256')
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            listed = self.clients['s3'].list_objects_v2(Bucket=m['deploy_bucket'],
                Prefix='staging/monitoring/status.json', MaxKeys=1)
            if not any(item['Key'] == 'staging/monitoring/status.json' for item in listed.get('Contents', [])):
                time.sleep(3)
                continue
            try:
                status,_=self.aws.get('staging/monitoring/status.json')
            except self.clients['s3'].exceptions.NoSuchKey:
                time.sleep(3);continue
            if status.get('request_sha256')==digest and status.get('run_id')==m['run_id']:
                if status.get('status')!='applied':
                    raise RuntimeError('Stage monitoring request was rejected')
                return status
            time.sleep(3)
        raise RuntimeError('Stage monitoring acknowledgement timed out')

    def runtime(self):
        """Send the small reviewed host runtime, excluding app image and any secrets."""
        with tempfile.TemporaryDirectory(prefix='stage-runtime-') as name:
            archive=Path(name)/'runtime.tar.gz'
            with tarfile.open(archive,'w:gz') as bundle:
                files=list((ROOT/'loadtest').rglob('*'))+[ROOT/'scripts/staging_locust_install.py',
                       ROOT/'scripts/staging_lifecycle.py', ROOT/'scripts/loadtest_admin.py',ROOT/'scripts/staging_hooks/host.py']
                for path in files:
                    if path.is_symlink(): raise ValueError('Runtime source symlink rejected')
                    if path.is_file() and '__pycache__' not in path.parts:
                        bundle.add(path,arcname=str(path.relative_to(ROOT)),recursive=False)
            digest=checksum(archive);key=f"staging/runs/{self.manifest['run_id']}/runtime/{digest}.tar.gz"
            try:
                with archive.open('rb') as stream:
                    self.clients['s3'].put_object(Bucket=self.manifest['deploy_bucket'],Key=key,Body=stream,
                                               ServerSideEncryption='AES256',IfNoneMatch='*')
            except self.clients['s3'].exceptions.ClientError as error:
                if error.response['Error']['Code'] not in ('PreconditionFailed', '412'):
                    raise
                response = self.clients['s3'].get_object(Bucket=self.manifest['deploy_bucket'], Key=key)
                existing = hashlib.sha256()
                body = response['Body']
                try:
                    for block in body.iter_chunks(1024*1024):
                        existing.update(block)
                finally:
                    body.close()
                if existing.hexdigest() != digest:
                    raise ValueError('Existing runtime bundle integrity mismatch')
        return key,digest

    def host_script(self, key, digest, action, actor):
        m=self.manifest
        if not re.fullmatch(r'[a-f0-9]{64}',digest) or key!=f"staging/runs/{m['run_id']}/runtime/{digest}.tar.gz":
            raise ValueError('Runtime immutable identity mismatch')
        directory='/opt/getddo/staging/integration/'+digest
        payload=base64.b64encode(json.dumps({'manifest':m,'actor':actor}).encode()).decode()
        return f'''install -d -m 0700 {directory}
aws --region {m['region']} s3api get-object --bucket {m['deploy_bucket']} --key {key} --expected-bucket-owner {m['account_id']} {directory}/runtime.tar.gz >/dev/null
printf '%s  %s\\n' {digest} {directory}/runtime.tar.gz | sha256sum -c - >/dev/null
tar -xzf {directory}/runtime.tar.gz -C {directory}
printf %s {shlex.quote(payload)} | base64 -d | /opt/getddo-staging-venv/bin/python {directory}/scripts/staging_hooks/host.py {action}
'''

    def prepare_test_access(self):
        import secrets
        from staging_cleanup import Orphans, tags
        name = '/getddo/staging/test-access/mysql-password'
        client = self.clients['ssm']
        try:
            client.get_parameter(Name=name, WithDecryption=False)
        except client.exceptions.ParameterNotFound:
            client.put_parameter(Name=name, Value=secrets.token_urlsafe(36), Type='SecureString',
                Tags=[{'Key':'Environment','Value':'staging'},
                      {'Key':'RunId','Value':self.manifest['run_id']}])
        else:
            actual = client.list_tags_for_resource(ResourceType='Parameter',ResourceId=name)['TagList']
            Orphans(self.clients,self.manifest['run_id']).require(tags(actual))

    def prepare(self):
        metadata=json.loads((ROOT/'scripts/staging_hooks/backend.json').read_text())
        if set(metadata)!={'source_sha','image_id','archive_sha256','artifact_dir','checkout_dir'}:
            raise ValueError('Immutable backend identities required')
        source=inside(metadata['checkout_dir']);artifact=inside(metadata['artifact_dir'])
        m=self.manifest;d=m['deployment']
        config={key:d[key] for key in ('account_id','region','vpc_id','target_group_arn')}
        config.update(run_id=m['run_id'],deploy_bucket=m['deploy_bucket'],
                      instances={s:h['id'] for s,h in d['api_instances'].items()},
                      expected_names={s:'getddo-staging-api-'+s for s in ('a','c')})
        with tempfile.TemporaryDirectory(prefix='stage-deploy-') as name:
            path=Path(name)/'manifest.json';path.write_text(json.dumps(config))
            command([sys.executable,str(source/'infra/scripts/deploy-staging.py'),'--manifest',str(path),
                     '--source-sha',metadata['source_sha'],'--image-id',metadata['image_id'],
                     '--archive-sha256',metadata['archive_sha256'],'--artifact-dir',str(artifact)],cwd=source)
        import uuid
        actor={'user_id':str(uuid.uuid5(uuid.NAMESPACE_URL,'getddo-stage-smoke:'+m['run_id'])),'role':'USER','membership':'excellent'}
        self.prepare_test_access()
        key,digest=self.runtime()
        self.remote(d['api_instances']['a']['id'],self.host_script(key,digest,'seed',actor))
        self.remote(d['locust_instance']['id'],self.host_script(key,digest,'install',actor),timeout=1200)
        deadline=time.monotonic()+180
        while time.monotonic()<deadline:
            try:
                with urllib.request.urlopen(m['public_origin']+'/locust/health',timeout=10) as response:
                    ready=json.load(response)
                if ready.get('alive') and ready.get('ready'): break
            except (urllib.error.URLError,ValueError): pass
            time.sleep(3)
        else: raise RuntimeError('Locust HTTPS readiness failed')
        self.monitoring('publish')

    def collect(self):
        m=self.manifest
        if 'deployment' not in m:
            payload=json.dumps({'run_id':m['run_id'],'phase':m['phase'],'deployment_outputs_available':False}).encode()
            key=f"staging/runs/{m['run_id']}/results/incomplete-preparation.json"
            self.clients['s3'].put_object(Bucket=m['deploy_bucket'],Key=key,Body=payload,ServerSideEncryption='AES256')
            return {'key':key,'sha256':hashlib.sha256(payload).hexdigest()}
        # Reuse the exact runtime installed by prepare; no executable source supplied by collect.
        key,digest=self.runtime()
        output=self.remote(m['deployment']['locust_instance']['id'],self.host_script(key,digest,'collect',{}),timeout=120)
        receipt=json.loads(output.strip().splitlines()[-1])
        if set(receipt)!={'key','sha256'} or not receipt['key'].startswith(f"staging/runs/{m['run_id']}/results/") or not re.fullmatch(r'[a-f0-9]{64}',receipt['sha256']):
            raise ValueError('Stage report receipt mismatch')
        monitoring=self.monitoring('collect', timeout=180)
        # One receipt references both immutable report objects; AWS adapter readback verifies this index.
        metadata=json.loads((ROOT/'scripts/staging_hooks/backend.json').read_text())
        index={'run_id':m['run_id'],'infra_source_sha':m['source_sha'],
               'backend':{key:metadata[key] for key in ('source_sha','image_id','archive_sha256')},
               'locust':receipt,'monitoring':monitoring['results']}
        for item in (index['locust'],index['monitoring']):
            response=self.clients['s3'].get_object(Bucket=m['deploy_bucket'],Key=item['key'])
            digest=hashlib.sha256()
            stream=response['Body']
            try:
                for block in stream.iter_chunks(1024*1024):digest.update(block)
            finally:
                stream.close()
            if digest.hexdigest()!=item['sha256']:
                raise ValueError('Collected result readback mismatch')
        payload=json.dumps(index,sort_keys=True,separators=(',',':')).encode()
        digest=hashlib.sha256(payload).hexdigest();key=f"staging/runs/{m['run_id']}/results/index-{digest}.json"
        self.clients['s3'].put_object(Bucket=m['deploy_bucket'],Key=key,Body=payload,ServerSideEncryption='AES256')
        return {'key':key,'sha256':digest}


def main(action):
    hook=Hooks(json.load(sys.stdin))
    if action=='prepare': hook.prepare()
    elif action=='detach': hook.monitoring('remove')
    elif action=='collect': print(json.dumps(hook.collect()))
    else: raise ValueError('Unsupported integration action')
