"""Stage monitoring ownership, validation, rollback and idempotent transport."""
import copy
import datetime as dt
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('staging_monitoring', Path(__file__).with_name('staging-monitoring.py'))
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)


def manifest():
    return {'account_id': monitor.ACCOUNT, 'region': monitor.REGION, 'vpc_id': monitor.VPC,
            'run_id': 'run-20261023-abcd',
            'expires_at': (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)).isoformat(),
            'api_instances': {'a': {'id': 'i-'+'1'*17, 'private_ip': '10.20.0.10'},
                              'c': {'id': 'i-'+'2'*17, 'private_ip': '10.20.1.10'}},
            'locust_instance': {'id': 'i-'+'3'*17, 'private_ip': '10.20.0.20'},
            'db_identifier': 'getddo-staging-mysql', 'redis_cluster_id': 'getddo-staging-redis-001',
            'load_balancer_arn': f'arn:aws:elasticloadbalancing:{monitor.REGION}:{monitor.ACCOUNT}:loadbalancer/app/getddo-staging-api/abcdef0123456789',
            'target_group_arn': f'arn:aws:elasticloadbalancing:{monitor.REGION}:{monitor.ACCOUNT}:targetgroup/getddo-staging-api/abcdef0123456789'}


def client_for(data):
    def client(*args):
        if args[:2] == ('sts', 'get-caller-identity'):
            return {'Account': monitor.ACCOUNT}
        if args[:2] == ('ec2', 'describe-instances'):
            instances = []
            for server, host in {**data['api_instances'], 'locust': data['locust_instance']}.items():
                name = 'getddo-staging-locust' if server == 'locust' else 'getddo-staging-api-'+server
                instances.append({'InstanceId': host['id'], 'PrivateIpAddress': host['private_ip'],
                                  'VpcId': monitor.VPC, 'State': {'Name': 'running'},
                                  'Tags': [{'Key': 'Name', 'Value': name}, {'Key': 'Environment', 'Value': 'staging'},
                                           {'Key': 'RunId', 'Value': data['run_id']}]})
            return {'Reservations': [{'Instances': instances}]}
        raise AssertionError(args)
    return client


class StagingMonitoringTests(unittest.TestCase):
    def test_stage_register_remove_preserves_prod_and_dashboard_history(self):
        data = manifest()
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); target = root/'targets'; target.mkdir(); dashboards = root/'dashboards'; dashboards.mkdir()
            prod = target/'api-targets.json'; prod.write_bytes(b'prod must remain byte identical\n')
            monitor.publish({'action': 'publish', 'manifest': data}, target, dashboards, client_for(data))
            self.assertEqual(len(json.loads((target/monitor.FILES[0]).read_text())), 2)
            self.assertEqual(len(json.loads((target/monitor.FILES[1]).read_text())), 3)
            historical = (dashboards/'staging-cloudwatch.json').read_bytes()
            # Removal works after resources expire/disappear, without AWS reads.
            data['expires_at'] = '2020-01-01T00:00:00Z'
            monitor.publish({'action': 'remove', 'manifest': data}, target, dashboards,
                            lambda *args: self.fail('remove must not query destroyed hosts'))
            for filename in monitor.FILES:
                self.assertEqual(json.loads((target/filename).read_text()), [])
            self.assertEqual(prod.read_bytes(), b'prod must remain byte identical\n')
            self.assertEqual((dashboards/'staging-cloudwatch.json').read_bytes(), historical)
            self.assertFalse((target/'.staging-owner.json').exists())

    def test_reject_wrong_run_owner_for_publish_and_remove(self):
        data = manifest()
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); (root/'.staging-owner.json').write_text('{"run_id":"another-run"}')
            for action in ('publish', 'remove'):
                with self.subTest(action=action), self.assertRaisesRegex(ValueError, 'another run'):
                    monitor.publish({'action': action, 'manifest': data}, root, root, client_for(data))

    def test_reject_production_identity_wrong_run_stopped_and_expired(self):
        data = manifest()
        for field, value in [('Environment', 'prod'), ('Name', 'getddo-dev-api-a'), ('RunId', 'other-run')]:
            client = client_for(data)
            def changed(*args):
                response = client(*args)
                if args[:2] == ('ec2', 'describe-instances'):
                    tags = response['Reservations'][0]['Instances'][0]['Tags']
                    next(tag for tag in tags if tag['Key'] == field)['Value'] = value
                return response
            with self.subTest(field=field), self.assertRaises(ValueError):
                monitor.render(data, changed)
        expired = copy.deepcopy(data); expired['expires_at'] = '2020-01-01T00:00:00Z'
        with self.assertRaisesRegex(ValueError, 'expired'):
            monitor.render(expired, client_for(data))

    def test_duplicate_and_public_addresses_rejected(self):
        for address in ('203.0.113.1', '10.20.0.10'):
            data = manifest(); data['locust_instance']['private_ip'] = address
            with self.assertRaises(ValueError):
                monitor.render(data, client_for(data))

    def test_atomic_failure_restores_all_existing_bytes(self):
        data = manifest()
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            for filename in monitor.FILES:
                (root/filename).write_bytes(b'[]\n')
            original = monitor.atomic; calls = []
            def fail_second(path, value):
                calls.append(path)
                if len(calls) == 2:
                    raise OSError('disk failure')
                original(path, value)
            with patch.object(monitor, 'atomic', side_effect=fail_second), self.assertRaises(OSError):
                monitor.publish({'action': 'publish', 'manifest': data}, root, root, client_for(data))
            for filename in monitor.FILES:
                self.assertEqual((root/filename).read_bytes(), b'[]\n')
            self.assertFalse((root/'.staging-owner.json').exists())

    def test_symlink_never_overwrites_other_file(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); victim = root/'prod.json'; victim.write_bytes(b'prod')
            (root/monitor.FILES[0]).symlink_to(victim)
            with self.assertRaisesRegex(ValueError, 'symlink'):
                monitor.publish({'action': 'remove', 'manifest': manifest()}, root, root)
            self.assertEqual(victim.read_bytes(), b'prod')

    def test_cloudwatch_uses_exact_stage_dimensions_and_native_credit_period(self):
        dashboard = monitor.cloudwatch_dashboard(manifest())
        panels = dashboard['panels']; self.assertGreaterEqual(len(panels), 22)
        self.assertNotIn('getddo-dev-', json.dumps(dashboard))
        for panel in panels:
            for target in panel['targets']:
                self.assertTrue(target['matchExact'])
                if target['metricName'] == 'CPUCreditBalance':
                    self.assertEqual(target['period'], '300')
        data = manifest(); data['db_identifier'] = 'getddo-dev-mysql'
        with self.assertRaises(ValueError):
            monitor.cloudwatch_dashboard(data)

    def test_watch_no_request_is_idle_and_does_not_ack(self):
        def missing(*args):
            self.assertEqual(args[:2], ('s3api', 'list-objects-v2'))
            return {'Contents': []}
        with tempfile.TemporaryDirectory() as name, patch.object(monitor, 'publish') as publish:
            monitor.watch_once(name, name, Path(name)/'state', missing)
            publish.assert_not_called()
            self.assertFalse((Path(name)/'state/status.json').exists())

    def test_expiry_detaches_before_missing_duplicate_or_offline_request(self):
        for transport in ('missing', 'duplicate', 'offline'):
            with self.subTest(transport=transport), tempfile.TemporaryDirectory() as name:
                root = Path(name); data = manifest(); state = root/'state'; state.mkdir()
                prod = root/'api-targets.json'; prod.write_bytes(b'preserve prod\n')
                monitor.publish({'action': 'publish', 'manifest': data}, root, root, client_for(data))
                historical = (root/'staging-cloudwatch.json').read_bytes()
                owner = json.loads((root/'.staging-owner.json').read_bytes())
                owner['manifest']['expires_at'] = '2020-01-01T09:00:00+09:00'
                (root/'.staging-owner.json').write_text(json.dumps(owner))
                request = {'action': 'publish', 'manifest': data}
                (state/'applied.json').write_text(json.dumps({
                    'request_sha256': monitor.hashlib.sha256(monitor.canonical(request)).hexdigest()}))
                def client(*args):
                    for filename in monitor.FILES:
                        self.assertEqual(json.loads((root/filename).read_bytes()), [])
                    self.assertFalse((root/'.staging-owner.json').exists())
                    if transport == 'offline':
                        raise RuntimeError('S3 unavailable')
                    if args[:2] == ('s3api', 'list-objects-v2'):
                        return {'Contents': [] if transport == 'missing' else [{'Key': monitor.REQUEST}]}
                    if args[:2] == ('s3api', 'get-object'):
                        Path(args[-1]).write_text(json.dumps(request)); return {}
                    self.fail('expiry must not query AWS instances or upload a receipt')
                if transport == 'offline':
                    with self.assertRaisesRegex(RuntimeError, 'S3 unavailable'):
                        monitor.watch_once(root, root, state, client)
                else:
                    monitor.watch_once(root, root, state, client)
                self.assertEqual(prod.read_bytes(), b'preserve prod\n')
                self.assertEqual((root/'staging-cloudwatch.json').read_bytes(), historical)

    def test_unexpired_owner_survives_offline_request(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); data = manifest()
            monitor.publish({'action': 'publish', 'manifest': data}, root, root, client_for(data))
            original = {p: p.read_bytes() for p in root.iterdir() if p.is_file()}
            with self.assertRaises(RuntimeError):
                monitor.watch_once(root, root, root/'state', lambda *args: (_ for _ in ()).throw(RuntimeError('offline')))
            for path, content in original.items():
                self.assertEqual(path.read_bytes(), content)

    def test_expiry_refuses_foreign_malformed_or_symlink_owner(self):
        data = manifest(); data['expires_at'] = '2020-01-01T00:00:00Z'
        valid = {'run_id': data['run_id'], 'manifest': data}
        cases = [[], {'run_id': data['run_id']}, dict(valid, run_id='another-run'),
                 dict(valid, manifest=dict(data, account_id='000000000000')),
                 dict(valid, manifest=dict(data, region='us-east-1')),
                 dict(valid, manifest=dict(data, vpc_id='foreign-vpc')),
                 dict(valid, manifest=dict(data, expires_at='2020-01-01T00:00:00')),
                 dict(valid, manifest=dict(data, expires_at='invalid')), 'invalid-json', 'symlink']
        for value in cases:
            with self.subTest(value=value), tempfile.TemporaryDirectory() as name:
                root = Path(name); owner = root/'.staging-owner.json'
                for filename in monitor.FILES:
                    (root/filename).write_bytes(b'["preserve existing"]\n')
                if value == 'symlink':
                    victim = root/'prod.json'; victim.write_text(json.dumps(valid)); owner.symlink_to(victim)
                else:
                    owner.write_text(value if value == 'invalid-json' else json.dumps(value))
                before = {p: p.read_bytes() for p in root.iterdir()}
                with self.assertRaises((ValueError, KeyError, TypeError)):
                    monitor.watch_once(root, root, root/'state', lambda *args: self.fail('unsafe owner must fail before AWS'))
                for path, content in before.items():
                    self.assertEqual(path.read_bytes(), content)

    def test_expiry_refuses_changed_owner_under_remove_lock(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); data = manifest()
            monitor.publish({'action': 'publish', 'manifest': data}, root, root, client_for(data))
            stale = json.loads((root/'.staging-owner.json').read_bytes())
            stale['manifest']['expires_at'] = '2020-01-01T00:00:00Z'
            before = {p: p.read_bytes() for p in root.iterdir()}
            with self.assertRaisesRegex(ValueError, 'ownership changed'):
                monitor.publish({'action': 'remove', 'manifest': stale['manifest']}, root, root,
                                expected_owner=stale)
            for path, content in before.items():
                self.assertEqual(path.read_bytes(), content)

    def test_watch_success_once_and_ack_matches_input_digest(self):
        data = manifest(); request = {'action': 'publish', 'manifest': data}; uploaded = []
        def client(*args):
            if args[:2] == ('s3api', 'list-objects-v2'):
                return {'Contents': [{'Key': monitor.REQUEST}]}
            if args[:2] == ('s3api', 'get-object'):
                Path(args[-1]).write_text(json.dumps(request)); return {}
            if args[:2] == ('s3api', 'put-object'):
                uploaded.append(json.loads(Path(args[args.index('--body')+1]).read_bytes())); return {}
            return client_for(data)(*args)
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            for _ in range(2): monitor.watch_once(root, root, root/'state', client)
            self.assertEqual(len(uploaded), 1)
            self.assertEqual(uploaded[0]['status'], 'applied')
            self.assertEqual(uploaded[0]['request_sha256'], monitor.hashlib.sha256(monitor.canonical(request)).hexdigest())

    def test_failure_ack_does_not_persist_success_and_is_retried(self):
        request = {'action': 'publish', 'manifest': manifest()}; request['manifest']['account_id'] = '000000000000'
        receipts = []
        def client(*args):
            if args[:2] == ('s3api', 'list-objects-v2'):
                return {'Contents': [{'Key': monitor.REQUEST}]}
            if args[:2] == ('s3api', 'get-object'):
                Path(args[-1]).write_text(json.dumps(request)); return {}
            if args[:2] == ('s3api', 'put-object'):
                receipts.append(json.loads(Path(args[args.index('--body')+1]).read_bytes())); return {}
            self.fail('must fail before AWS instance lookups')
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            for _ in range(2): monitor.watch_once(root, root, root/'state', client)
            self.assertEqual([r['status'] for r in receipts], ['failed', 'failed'])
            self.assertFalse((root/'state/applied.json').exists())

    def test_monitor_export_preserves_queries_and_never_saves_login_secret(self):
        import gzip
        import io
        data=manifest();data['created_at']=(dt.datetime.now(dt.timezone.utc)-dt.timedelta(minutes=2)).isoformat()
        stored=[];requests=[]
        class Opener:
            def open(self, request, timeout):
                payload=json.loads(request.data);requests.append(payload)
                result=({'message':'Logged in'} if request.full_url.endswith('/login') else
                        {'results':{q['refId']:{'frames':[]} for q in payload['queries']}})
                return io.BytesIO(json.dumps(result).encode())
        def client(*args):
            if args[:2]==('ssm','get-parameter'):return {'Parameter':{'Value':'NEVER_SAVE_THIS_SECRET'}}
            if args[:2]==('s3api','put-object'):
                body=Path(args[args.index('--body')+1]).read_bytes();stored.append(body);return {}
            self.fail(str(args[:2]))
        with tempfile.TemporaryDirectory() as name:
            root=Path(name)
            (root/'overview.json').write_text(json.dumps({'uid':'stage','panels':[{'type':'timeseries',
                'datasource':{'uid':'prod-prometheus','type':'prometheus'},
                'targets':[{'expr':'up{environment="staging",server=~"$server"}'}]}]}))
            result=monitor.export_metrics(data,root,root,client,Opener())
            self.assertEqual(result['sha256'],monitor.hashlib.sha256(stored[0]).hexdigest())
            exported=gzip.decompress(stored[0])
            self.assertNotIn(b'NEVER_SAVE_THIS_SECRET',exported)
            self.assertIn(b'"interval":"5s"',exported)
            self.assertIn(b'up{environment=\\"staging\\",server=~\\".*\\"}',exported)
            self.assertEqual(len(requests),3)

    def test_weekend_export_accepts_72_and_96_hours_and_rejects_longer(self):
        import io
        from unittest.mock import Mock
        now = dt.datetime.now(dt.timezone.utc)
        for hours in (72, 96):
            with self.subTest(hours=hours), tempfile.TemporaryDirectory() as name:
                data = manifest()
                data['expires_at'] = (now - dt.timedelta(hours=1)).isoformat()
                data['created_at'] = (now - dt.timedelta(hours=hours + 1)).isoformat()
                root = Path(name)
                (root/'overview.json').write_text(json.dumps({'panels': [{
                    'type': 'timeseries', 'datasource': {'uid': 'prometheus', 'type': 'prometheus'},
                    'targets': [{'expr': 'up{environment="staging"}'}]}]}))
                submitted = []
                class Opener:
                    def open(self, request, timeout):
                        payload = json.loads(request.data)
                        submitted.append(payload)
                        result = ({'message': 'Logged in'} if request.full_url.endswith('/login') else
                                  {'results': {query['refId']: {'frames': []} for query in payload['queries']}})
                        return io.BytesIO(json.dumps(result).encode())
                client = Mock(return_value={'Parameter': {'Value': 'test-only-password'}})
                receipt = monitor.export_metrics(data, root, root, client, Opener())
                self.assertTrue(receipt['key'].startswith('staging/runs/' + data['run_id'] + '/results/monitoring-'))
                queries = submitted[1]
                self.assertEqual(int(queries['to']) - int(queries['from']), hours * 60 * 60 * 1000)
                self.assertEqual(queries['queries'][0]['maxDataPoints'], 3600)
                self.assertGreaterEqual(queries['queries'][0]['intervalMs'], hours * 1000)
                step = int(queries['queries'][0]['interval'].removesuffix('s'))
                self.assertLessEqual((hours * 60 * 60) // step + 1, 3600)
                self.assertEqual(queries['queries'][0]['intervalMs'], step * 1000)
        for duration in (dt.timedelta(hours=96, microseconds=1), dt.timedelta(days=5)):
            with self.subTest(duration=duration):
                data = manifest()
                data.update(created_at=(now - dt.timedelta(hours=1)).isoformat(),
                            expires_at=(now - dt.timedelta(hours=1) + duration).isoformat())
                client = Mock()
                opener = Mock()
                with self.assertRaisesRegex(ValueError, 'Bounded Stage export'):
                    monitor.export_metrics(data, Path('/unused'), Path('/unused'), client, opener)
                client.assert_not_called()
                opener.open.assert_not_called()

    def test_expired_targets_detach_but_exact_registered_manifest_can_be_exported(self):
        data = manifest()
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            monitor.publish({'action': 'publish', 'manifest': data}, root, root, client_for(data))
            # Move the validated local registration past its deadline without AWS access.
            data['expires_at'] = '2020-01-01T00:00:00+00:00'
            (root/'.staging-owner.json').write_text(json.dumps({'run_id': data['run_id'], 'manifest': data}))
            monitor.expire_targets(root, root)
            self.assertFalse((root/'.staging-owner.json').exists())
            self.assertEqual(json.loads((root/'.staging-last-owner.json').read_bytes())['manifest'], data)
            self.assertTrue(all(json.loads((root/f).read_bytes()) == [] for f in monitor.FILES))
            request = {'action': 'collect', 'manifest': data, 'request_id': 'a'*32}
            with patch.object(monitor, 'export_metrics', return_value={'key': 'saved'}) as export:
                self.assertEqual(monitor.publish(request, root, root)['results'], {'key': 'saved'})
                export.assert_called_once()
            altered = copy.deepcopy(request)
            altered['manifest']['locust_instance']['private_ip'] = '10.20.0.99'
            with patch.object(monitor, 'export_metrics') as export, self.assertRaisesRegex(ValueError, 'exact previously'):
                monitor.publish(altered, root, root)
            export.assert_not_called()
            # A later run invalidates the retired receipt and prevents exporting old ownership.
            new = manifest()
            new['run_id'] = 'new-run-20261026-abcd'
            monitor.publish({'action': 'publish', 'manifest': new}, root, root, client_for(new))
            self.assertFalse((root/'.staging-last-owner.json').exists())
            with patch.object(monitor, 'export_metrics') as export, self.assertRaisesRegex(ValueError, 'another run'):
                monitor.publish(request, root, root)
            export.assert_not_called()

    def test_failed_retired_receipt_write_restores_active_registration(self):
        data = manifest()
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            monitor.publish({'action': 'publish', 'manifest': data}, root, root, client_for(data))
            paths = [*(root/filename for filename in monitor.FILES),
                     root/'.staging-owner.json', root/'staging-cloudwatch.json']
            before = {path: path.read_bytes() for path in paths}
            atomic = monitor.atomic
            def fail_retirement(path, value):
                if path.name == '.staging-last-owner.json':
                    raise OSError('disk failure')
                atomic(path, value)
            with patch.object(monitor, 'atomic', side_effect=fail_retirement), self.assertRaises(OSError):
                monitor.publish({'action': 'remove', 'manifest': data}, root, root)
            self.assertFalse((root/'.staging-last-owner.json').exists())
            for path, original in before.items():
                self.assertEqual(path.read_bytes(), original)

    def test_retired_owner_symlink_is_rejected_before_any_export(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            victim = root/'victim'
            victim.write_text('{}')
            (root/'.staging-last-owner.json').symlink_to(victim)
            with patch.object(monitor, 'export_metrics') as export, self.assertRaisesRegex(ValueError, 'symlink'):
                monitor.publish({'action': 'collect', 'manifest': manifest(), 'request_id': 'a'*32}, root, root)
            export.assert_not_called()
            self.assertEqual(victim.read_text(), '{}')

    def test_collect_requires_exact_previously_registered_manifest(self):
        data=manifest()
        with tempfile.TemporaryDirectory() as name:
            root=Path(name)
            monitor.publish({'action':'publish','manifest':data},root,root,client_for(data))
            request={'action':'collect','manifest':copy.deepcopy(data),'request_id':'a'*32}
            request['manifest']['locust_instance']['private_ip']='10.20.0.99'
            with patch.object(monitor,'export_metrics') as export, self.assertRaisesRegex(ValueError,'exact previously'):
                monitor.publish(request,root,root)
            export.assert_not_called()
            request['manifest']=data
            with patch.object(monitor,'export_metrics',return_value={'key':'test','sha256':'a'*64}) as export:
                result=monitor.publish(request,root,root)
                self.assertEqual(result['results']['key'],'test')
                export.assert_called_once()


if __name__ == '__main__': unittest.main()
