"""Integration target fencing and result acknowledgement; no live AWS or secrets."""
import datetime as dt
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import types
import unittest
from unittest.mock import Mock, patch

from botocore.exceptions import ClientError, IncompleteReadError
from botocore.response import StreamingBody

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('staging_hooks_common',ROOT/'scripts/staging_hooks/common.py')
hooks=importlib.util.module_from_spec(spec);spec.loader.exec_module(hooks)


def manifest():
    now=dt.datetime.now(dt.timezone.utc)
    return {'account_id':'942466928325','region':'ap-northeast-2','deploy_bucket':'getddo-deploy-942466928325-ap-northeast-2',
        'run_id':'a'*32,'bundle_sha256':'b'*64,'bundle_key':'staging/bundles/'+'b'*64+'.tar.gz','source_sha':'c'*40,
        'created_at':now.isoformat(),'expires_at':(now+dt.timedelta(minutes=60)).isoformat(),'max_duration_minutes':60,
        'phase':'ready','public_origin':'https://dzc8oneey9mmx.cloudfront.net',
        'terraform':{'ami_id':'ami-'+'a'*17,'monitoring_security_group_id':'sg-monitor','shared_alb_security_group_id':'sg-shared'},
        'hooks':{name:'scripts/staging_hooks/'+name+'.py' for name in ('prepare','collect','detach')},
        'deployment':{'account_id':'942466928325','region':'ap-northeast-2','vpc_id':'vpc-'+'d'*17,
            'api_instances':{'a':{'id':'i-'+'1'*17,'private_ip':'10.20.0.10'},'c':{'id':'i-'+'2'*17,'private_ip':'10.20.1.10'}},
            'locust_instance':{'id':'i-'+'3'*17,'private_ip':'10.20.0.20'}}}


def aws_for(data):
    result = types.SimpleNamespace(owner=lambda:{'run_id':data['run_id'],'bundle_sha256':data['bundle_sha256']},
                                 clients={'ec2':Mock(),'ssm':Mock(),'s3':Mock()})
    result.clients['s3'].list_objects_v2.return_value = {'Contents':[{'Key':'staging/monitoring/status.json'}]}
    return result


class HookTests(unittest.TestCase):
    def test_duplicate_runtime_readback_verifies_hash_and_closes_stream(self):
        for code in ('PreconditionFailed','412'):
            for outcome in ('valid','mismatch','read-error'):
                with self.subTest(code=code,outcome=outcome):
                    data=manifest();aws=aws_for(data);s3=aws.clients['s3'];bodies=[];digests=[]
                    s3.exceptions.ClientError=ClientError
                    def put(**kwargs):
                        payload=kwargs['Body'].read()
                        digests.append(hashlib.sha256(payload).hexdigest())
                        if outcome=='mismatch':payload+=b'corrupted'
                        raw=io.BytesIO(payload);bodies.append(raw)
                        s3.get_object.return_value={'Body':StreamingBody(raw,len(payload)+(outcome=='read-error'))}
                        raise ClientError({'Error':{'Code':code}},'PutObject')
                    s3.put_object.side_effect=put
                    hook=hooks.Hooks(data,aws)
                    if outcome=='valid':
                        key,digest=hook.runtime()
                        self.assertEqual(key,f"staging/runs/{data['run_id']}/runtime/{digest}.tar.gz")
                        self.assertEqual(digest,digests[0])
                    elif outcome=='mismatch':
                        with self.assertRaisesRegex(ValueError,'Existing runtime bundle integrity mismatch'):hook.runtime()
                    else:
                        with self.assertRaises(IncompleteReadError):hook.runtime()
                    self.assertTrue(bodies[0].closed)
                    s3.get_object.assert_called_once()

    def test_collect_readback_verifies_both_hashes_and_closes_streams(self):
        for outcome in ('valid','mismatch','read-error'):
            for failed_result in ('locust','monitoring'):
                with self.subTest(outcome=outcome,failed_result=failed_result):
                    data=manifest();aws=aws_for(data);hook=hooks.Hooks(data,aws);s3=aws.clients['s3']
                    receipts={};bodies=[]
                    for name in ('locust','monitoring'):
                        payload=(name+' report').encode()
                        receipts[name]={'key':f"staging/runs/{data['run_id']}/results/{name}.json",
                                        'sha256':hashlib.sha256(payload).hexdigest()}
                        if outcome=='mismatch' and name==failed_result:receipts[name]['sha256']='0'*64
                    def get(**kwargs):
                        name=Path(kwargs['Key']).stem;payload=(name+' report').encode()
                        raw=io.BytesIO(payload);bodies.append(raw)
                        return {'Body':StreamingBody(raw,len(payload)+(outcome=='read-error' and name==failed_result))}
                    s3.get_object.side_effect=get
                    hook.runtime=Mock(return_value=('runtime-key','d'*64))
                    hook.host_script=Mock(return_value='collect-script')
                    hook.remote=Mock(return_value=json.dumps(receipts['locust']))
                    hook.monitoring=Mock(return_value={'results':receipts['monitoring']})
                    metadata={'source_sha':'e'*40,'image_id':'sha256:'+'f'*64,'archive_sha256':'1'*64}
                    with patch.object(hooks.Path,'read_text',return_value=json.dumps(metadata)):
                        if outcome=='valid':
                            receipt=hook.collect()
                            payload=s3.put_object.call_args.kwargs['Body']
                            self.assertEqual(receipt['sha256'],hashlib.sha256(payload).hexdigest())
                            self.assertEqual(json.loads(payload)['locust'],receipts['locust'])
                            self.assertEqual(json.loads(payload)['monitoring'],receipts['monitoring'])
                        elif outcome=='mismatch':
                            with self.assertRaisesRegex(ValueError,'Collected result readback mismatch'):hook.collect()
                        else:
                            with self.assertRaises(IncompleteReadError):hook.collect()
                    self.assertTrue(all(raw.closed for raw in bodies))
                    self.assertEqual(len(bodies),2 if outcome=='valid' or failed_result=='monitoring' else 1)
                    if outcome!='valid':s3.put_object.assert_not_called()

    def test_remote_refuses_unknown_production_or_other_run_before_command(self):
        data=manifest()
        for failure in ('unknown-id','prod','other-run','wrong-vpc'):
            with self.subTest(failure=failure):
                aws=aws_for(data);client=aws.clients['ec2']
                client.describe_instances.return_value={'Reservations':[{'Instances':[{
                    'VpcId':'vpc-wrong' if failure=='wrong-vpc' else data['deployment']['vpc_id'],
                    'State':{'Name':'running'},'Tags':[{'Key':'Environment','Value':'prod' if failure=='prod' else 'staging'},
                    {'Key':'RunId','Value':'wrong' if failure=='other-run' else data['run_id']}]}]}]}
                hook=hooks.Hooks(data,aws)
                target='i-'+'9'*17 if failure=='unknown-id' else data['deployment']['api_instances']['a']['id']
                with self.assertRaises(ValueError):hook.remote(target,'must not execute')
                aws.clients['ssm'].send_command.assert_not_called()

    def test_matching_monitor_ack_and_unique_collection_requests(self):
        data=manifest();aws=aws_for(data);requests=[]
        def put(**kwargs):requests.append(json.loads(kwargs['Body']))
        aws.clients['s3'].put_object.side_effect=put
        def get(key):
            request=requests[-1]
            digest=hashlib.sha256(json.dumps(request,sort_keys=True,separators=(',',':')).encode()).hexdigest()
            return {'run_id':data['run_id'],'request_sha256':digest,'status':'applied'},'etag'
        aws.get=get;hook=hooks.Hooks(data,aws)
        hook.monitoring('collect');hook.monitoring('collect')
        self.assertNotEqual(requests[0]['request_id'],requests[1]['request_id'])
        self.assertEqual(requests[0]['manifest']['run_id'],data['run_id'])
        self.assertNotIn('persistent_credentials',requests[0]['manifest'])

    def test_monitor_rejection_is_not_ready(self):
        data=manifest();aws=aws_for(data);requests=[]
        aws.clients['s3'].put_object.side_effect=lambda **kw:requests.append(kw['Body'])
        aws.get=lambda key:({'run_id':data['run_id'],'request_sha256':hashlib.sha256(requests[-1]).hexdigest(),'status':'failed'},'etag')
        with self.assertRaisesRegex(RuntimeError,'rejected'):hooks.Hooks(data,aws).monitoring('publish')

    def test_runtime_script_requires_immutable_run_prefix_and_digest(self):
        data=manifest();hook=hooks.Hooks(data,aws_for(data));digest='d'*64
        with self.assertRaises(ValueError):hook.host_script('staging/other/run.tar.gz',digest,'seed',{})
        key=f"staging/runs/{data['run_id']}/runtime/{digest}.tar.gz"
        script=hook.host_script(key,digest,'seed',{})
        self.assertIn('sha256sum -c -',script)
        self.assertIn('--expected-bucket-owner 942466928325',script)
        self.assertNotIn('admin-password',script)

    def test_host_failure_codes_survive_on_stderr_without_secrets(self):
        import contextlib
        import io
        from unittest.mock import patch
        spec=importlib.util.spec_from_file_location('staging_host',ROOT/'scripts/staging_hooks/host.py')
        host=importlib.util.module_from_spec(spec);spec.loader.exec_module(host)
        for error, expected in (
                (host.StageRuntimeError('builder','incompatible'), 'STAGE_RUNTIME_FAILED stage=builder reason=incompatible'),
                (host.StageRuntimeError('compose_up','failed'), 'STAGE_RUNTIME_FAILED stage=compose_up reason=failed'),
                (RuntimeError('SECRET_PASSWORD SECRET_SESSION scrypt:SECRET_HASH'), 'Stage host action failed: unexpected')):
            with self.subTest(expected=expected), patch.object(host,'main',side_effect=error), \
                 contextlib.redirect_stderr(io.StringIO()) as stderr, contextlib.redirect_stdout(io.StringIO()) as stdout:
                with self.assertRaises(SystemExit) as caught:host.cli()
            self.assertEqual(caught.exception.code,1)
            self.assertEqual(stderr.getvalue(),expected+'\n')
            self.assertEqual(stdout.getvalue(),'')
            self.assertNotIn('SECRET',stderr.getvalue())

    def test_host_collection_keeps_credentials_out_of_args_and_reports(self):
        import contextlib
        import io
        import tempfile
        from unittest.mock import patch
        spec=importlib.util.spec_from_file_location('staging_host',ROOT/'scripts/staging_hooks/host.py')
        host=importlib.util.module_from_spec(spec);spec.loader.exec_module(host)
        secret='NEVER_PUT_CREDENTIALS_IN_ARGS_OR_REPORTS'
        calls=[]
        original_temporary_directory = tempfile.TemporaryDirectory
        with tempfile.TemporaryDirectory() as name:
            reports=Path(name);(reports/'automation-fixed').mkdir()
            (reports/'automation-fixed/run_stats.csv').write_text('test-report')
            def command(args, **kwargs):
                calls.append((args,kwargs))
                self.assertNotIn(secret,str(args))
                if args[0]=='docker':
                    self.assertEqual(json.loads(kwargs['data'])['password'],secret)
                    self.assertNotIn(secret,str(kwargs['env']))
                elif args[0]=='aws':
                    self.assertNotIn(secret.encode(),Path(args[args.index('--body')+1]).read_bytes())
                return b''
            with patch.object(host,'REPORTS',reports), patch.object(host,'parameters',return_value={'admin-user':'locust-admin','admin-password':secret}), \
                 patch.object(host.uuid,'uuid4',return_value=types.SimpleNamespace(hex='fixed')), \
                 patch.object(host,'command',side_effect=command), \
                 patch.object(host.tempfile,'TemporaryDirectory',side_effect=lambda **kwargs: original_temporary_directory(prefix='fixture-',dir=reports)), \
                 contextlib.redirect_stdout(io.StringIO()) as output:
                host.collect(manifest())
            self.assertEqual(calls[0][0][0],'docker')
            self.assertEqual(calls[1][0][0],'aws')
            self.assertIn('/results/locust-',json.loads(output.getvalue())['key'])


if __name__=='__main__':unittest.main()
