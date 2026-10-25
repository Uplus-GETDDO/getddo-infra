"""Local contract checks; no AWS calls or public HTTP requests."""
import argparse
import copy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

SPEC = importlib.util.spec_from_file_location('waf_verify', Path(__file__).with_name('verify-waf-rate-limit.py'))
waf = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(waf)
RUN = '12345678-1234-4234-8234-123456789abc'
ARN = f'arn:aws:wafv2:us-east-1:{waf.ACCOUNT}:global/webacl/{waf.ACL_NAME}/acl-id'
LOG_ARN = f'arn:aws:logs:us-east-1:{waf.ACCOUNT}:log-group:{waf.LOG_GROUP}'


def args(root='/unused'):
    return argparse.Namespace(base_url='https://' + waf.HOST, probe_path='/api/__waf_probe/' + RUN,
        web_acl_id='acl-id', distribution_id=waf.DISTRIBUTION, region='us-east-1',
        origin='https://example.com', profile='getddo-gyeongju', evidence_dir=root)


def fixtures():
    scope = {'AndStatement': {'Statements': [
        {'ByteMatchStatement': {'SearchString': '/api/', 'FieldToMatch': {'UriPath': {}},
         'TextTransformations': [{'Priority': 0, 'Type': 'NONE'}], 'PositionalConstraint': 'STARTS_WITH'}},
        {'NotStatement': {'Statement': {'ByteMatchStatement': {'SearchString': 'OPTIONS',
         'FieldToMatch': {'Method': {}}, 'TextTransformations': [{'Priority': 0, 'Type': 'NONE'}],
         'PositionalConstraint': 'EXACTLY'}}}}]}}
    visibility = {'SampledRequestsEnabled': False}
    acl = {'ARN': ARN, 'DefaultAction': {'Allow': {}}, 'VisibilityConfig': visibility,
        'Rules': [{'Name': waf.RULE, 'VisibilityConfig': visibility,
            'Statement': {'RateBasedStatement': {'Limit': 600, 'EvaluationWindowSec': 60,
                'AggregateKeyType': 'IP', 'ScopeDownStatement': scope}},
            'Action': {'Block': {'CustomResponse': {'ResponseCode': 429,
                'CustomResponseBodyKey': 'error', 'ResponseHeaders': [
                    {'Name': 'Access-Control-Allow-Origin', 'Value': '*'}]}}}}],
        'CustomResponseBodies': {'error': {'Content': json.dumps(waf.ERROR), 'ContentType': 'APPLICATION_JSON'}}}
    return {
        'get-caller-identity': {'Account': waf.ACCOUNT, 'Arn': f'arn:aws:iam::{waf.ACCOUNT}:user/gyeongju'},
        'get-distribution': {'Distribution': {'Status': 'Deployed', 'DomainName': waf.HOST,
            'DistributionConfig': {'WebACLId': ARN, 'Enabled': True}}},
        'get-web-acl': {'WebACL': acl},
        'get-logging-configuration': {'LoggingConfiguration': {
            'LogDestinationConfigs': [LOG_ARN], 'LoggingFilter': {'DefaultBehavior': 'DROP',
                'Filters': [{'Behavior': 'KEEP', 'Requirement': 'MEETS_ALL',
                    'Conditions': [{'ActionCondition': {'Action': 'BLOCK'}}]}]},
            'RedactedFields': [{'QueryString': {}}] + [{'SingleHeader': {'Name': name}} for name in waf.REDACTED]}},
        'describe-log-groups': {'logGroups': [{'logGroupName': waf.LOG_GROUP, 'retentionInDays': 7, 'arn': LOG_ARN + ':*'}]},
        'describe-load-balancers': {'LoadBalancers': [{'LoadBalancerArn': 'balancer'}]},
        'describe-listeners': {'Listeners': [{'Port': 80, 'ListenerArn': 'listener'}]},
        'describe-rules': {'Rules': [{'Priority': '10', 'Conditions': [
            {'Field': 'path-pattern', 'Values': [args().probe_path]},
            {'Field': 'http-request-method', 'HttpRequestMethodConfig': {'Values': ['GET']}}],
            'Actions': [{'Type': 'fixed-response', 'FixedResponseConfig': {'StatusCode': '200',
                'ContentType': 'text/plain', 'MessageBody': 'waf-probe:' + RUN}}]}]},
        'filter-log-events': {'events': [{'message': json.dumps({'timestamp': 1, 'action': 'BLOCK',
            'terminatingRuleId': waf.RULE, 'responseCodeSent': 429,
            'httpRequest': {'uri': args().probe_path, 'httpMethod': 'GET',
                'headers': [{'name': 'cookie', 'value': 'do-not-output'}]}})}]}}


def good_block():
    return {'status': 429, 'error_body': True, 'cors': True,
            'retry_after_absent': True, 'credentials_absent': True, 'json_content_type': True}


class VerificationTests(unittest.TestCase):
    def test_target_rejects_alternatives_before_aws_or_http(self):
        for field, value in [('base_url', 'https://evil.example'), ('base_url', 'https://' + waf.HOST + '/api'),
                             ('base_url', 'http://' + waf.HOST), ('distribution_id', 'other'),
                             ('region', 'ap-northeast-2'), ('probe_path', '/api/__waf_probe/no'),
                             ('probe_path', args().probe_path + '?x=y'), ('origin', 'https://example.com/path')]:
            with self.subTest(field=field, value=value):
                arguments = args()
                setattr(arguments, field, value)
                with patch.object(waf, 'aws') as aws, patch.object(waf.Runner, 'perform') as http:
                    with self.assertRaises(waf.Incomplete):
                        waf.execute(arguments)
                    aws.assert_not_called()
                    http.assert_not_called()

    def test_actual_preflight(self):
        values = fixtures()
        with patch.object(waf, 'aws', side_effect=lambda _, service, op, *rest, **kw: values[op]):
            waf.preflight(args())

    def test_cli_base64_search_strings(self):
        values = fixtures()
        scope = values['get-web-acl']['WebACL']['Rules'][0]['Statement']['RateBasedStatement']['ScopeDownStatement']
        statements = scope['AndStatement']['Statements']
        statements[0]['ByteMatchStatement']['SearchString'] = 'L2FwaS8='
        statements[1]['NotStatement']['Statement']['ByteMatchStatement']['SearchString'] = 'T1BUSU9OUw=='
        with patch.object(waf, 'aws', side_effect=lambda _, service, op, *rest, **kw: values[op]):
            waf.preflight(args())

    def test_preflight_rejects_drift(self):
        mutations = [
            lambda f: f['get-caller-identity'].update(Account='000000000000'),
            lambda f: f['get-distribution']['Distribution'].update(Status='InProgress'),
            lambda f: f['get-distribution']['Distribution']['DistributionConfig'].update(WebACLId='wrong'),
            lambda f: f['get-web-acl']['WebACL']['Rules'][0]['Statement']['RateBasedStatement'].update(Limit=100),
            lambda f: f['get-web-acl']['WebACL']['Rules'][0]['Statement']['RateBasedStatement'].update(EvaluationWindowSec=300),
            lambda f: f['get-web-acl']['WebACL']['Rules'][0]['Statement']['RateBasedStatement'].update(AggregateKeyType='FORWARDED_IP'),
            lambda f: f['get-web-acl']['WebACL']['Rules'][0].update(Action={'Count': {}}),
            lambda f: f['get-web-acl']['WebACL']['VisibilityConfig'].update(SampledRequestsEnabled=True),
            lambda f: f['get-web-acl']['WebACL']['CustomResponseBodies']['error'].update(Content='{}'),
            lambda f: f['describe-log-groups']['logGroups'][0].update(retentionInDays=30),
            lambda f: f['get-logging-configuration']['LoggingConfiguration'].update(RedactedFields=[]),
            lambda f: f['get-logging-configuration']['LoggingConfiguration']['LoggingFilter'].update(DefaultBehavior='KEEP'),
            lambda f: f['describe-rules']['Rules'][0]['Conditions'][0].update(Values=['/api/other']),
            lambda f: f['describe-rules']['Rules'][0]['Actions'][0].update(Type='forward'),
        ]
        for mutation in mutations:
            values = copy.deepcopy(fixtures())
            mutation(values)
            with self.subTest(mutation=mutations.index(mutation)), patch.object(waf, 'aws', side_effect=lambda _, service, op, *rest, **kw: values[op]):
                with self.assertRaises(waf.Incomplete):
                    waf.preflight(args())

    def test_aws_uses_read_only_cli_with_no_retries(self):
        with patch.object(waf.subprocess, 'run') as run:
            run.return_value.stdout = '{}'
            waf.aws(args(), 'logs', 'describe-log-groups')
            command = run.call_args.args[0]
            self.assertIn('describe-log-groups', command)
            self.assertEqual(run.call_args.kwargs['env']['AWS_MAX_ATTEMPTS'], '1')
            self.assertEqual(run.call_args.kwargs['timeout'], 30)

    def test_no_redirect(self):
        self.assertIsNone(waf.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://example.com'))

    def test_http_timeout_and_no_sensitive_body_output(self):
        response = io.BytesIO(b'sensitive body')
        response.code = 401
        response.headers = {'Set-Cookie': 'private'}
        with patch.object(waf.urllib.request, 'build_opener') as opener:
            opener.return_value.open.return_value = response
            runner = waf.Runner(args(), RUN, io.StringIO())
            record = runner.one('baseline')
            self.assertEqual(opener.return_value.open.call_args.kwargs['timeout'], 3)
            self.assertNotIn('sensitive', json.dumps(record))
            self.assertNotIn('private', json.dumps(record))

    def test_error_requires_json_boolean(self):
        self.assertTrue(waf.matches_error(json.dumps(waf.ERROR)))
        self.assertFalse(waf.matches_error(json.dumps({**waf.ERROR, 'success': 0})))
        self.assertFalse(waf.matches_error(b'not json'))

    def test_interrupt_saves_incomplete_summary(self):
        values = fixtures()
        with tempfile.TemporaryDirectory() as directory, patch.object(waf, 'aws', side_effect=lambda _, service, op, *rest, **kw: values[op]), patch.object(waf.Runner, 'perform', return_value={'status': 200, 'probe_body': True}), patch.object(waf.Runner, 'load', side_effect=KeyboardInterrupt):
            root = Path(directory) / 'evidence'
            self.assertEqual(waf.execute(args(str(root))), 2)
            result = json.loads((root / 'verification.json').read_text())
            self.assertEqual(result['checks']['block'], 'NOT_RUN')
            self.assertEqual(result['total_requests'], 1)

    def test_bad_baseline_sends_no_load(self):
        values = fixtures()
        with tempfile.TemporaryDirectory() as directory, patch.object(waf, 'aws', side_effect=lambda _, service, op, *rest, **kw: values[op]), patch.object(waf.Runner, 'perform', return_value={'status': 200, 'probe_body': False}), patch.object(waf.Runner, 'load') as load:
            root = Path(directory) / 'evidence'
            self.assertEqual(waf.execute(args(str(root))), 2)
            load.assert_not_called()
            self.assertNotIn('PASS', (root / 'verification.json').read_text())

    def test_429_needs_exact_body(self):
        self.assertTrue(waf.valid_block(good_block()))
        self.assertFalse(waf.valid_block({'status': 429, 'error_body': False}))
        for key in ('cors', 'retry_after_absent', 'credentials_absent', 'json_content_type'):
            value = good_block()
            value[key] = False
            self.assertFalse(waf.block_contract(value))

    def test_shared_pacing_and_concurrency(self):
        runner = waf.Runner(args(), RUN, io.StringIO())
        starts = []
        active, maximum = 0, 0
        lock = threading.Lock()
        def perform(scheduled):
            nonlocal active, maximum
            with lock:
                starts.append(scheduled[-1])
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.12)
            with lock:
                active -= 1
            return {'status': 200, 'probe_body': True}
        with patch.object(runner, 'perform', side_effect=perform), patch.object(waf, 'MAX_REQUESTS', 6):
            blocked, count = runner.load()
        self.assertIsNone(blocked)
        self.assertEqual(count, 6)
        self.assertLessEqual(maximum, 2)
        self.assertTrue(all(b - a >= waf.INTERVAL - 0.001 for a, b in zip(starts, starts[1:])))

    def test_probe_departure_stops_load_and_joins(self):
        for response in ({'status': None}, {'status': 401}, {'status': 404},
                         {'status': 503}, {'status': 429, 'error_body': False},
                         {'status': 200, 'probe_body': False}):
            with self.subTest(response=response):
                stream = io.StringIO()
                runner = waf.Runner(args(), RUN, stream)
                active = []
                def perform(scheduled):
                    active.append(scheduled)
                    return response
                with patch.object(runner, 'perform', side_effect=perform):
                    with self.assertRaises(waf.Incomplete):
                        runner.load()
                self.assertLessEqual(runner.load_sent, 2)
                self.assertEqual(len(active), runner.load_sent)
                self.assertEqual(len(stream.getvalue().splitlines()), runner.load_sent)

    def test_first_valid_block_stops_load_and_joins(self):
        runner = waf.Runner(args(), RUN, io.StringIO())
        with patch.object(runner, 'perform', return_value=good_block()):
            blocked, count = runner.load()
        self.assertTrue(waf.valid_block(blocked))
        self.assertLessEqual(count, 2)
        self.assertEqual(runner.sent, count)

    def test_elapsed_limit_stops_load(self):
        runner = waf.Runner(args(), RUN, io.StringIO())
        with patch.object(runner, 'perform', return_value={'status': 200, 'probe_body': True}), patch.object(waf, 'MAX_SECONDS', 0.25):
            _, count = runner.load()
        self.assertLessEqual(count, 3)

    def test_log_proof_omits_headers(self):
        values = fixtures()
        with patch.object(waf, 'aws', side_effect=lambda _, service, op, *rest, **kw: values[op]):
            proof = waf.block_log(args(), 0)
        self.assertNotIn('do-not-output', json.dumps(proof))
        self.assertNotIn('headers', proof)

    def test_missing_log_never_passes(self):
        with patch.object(waf, 'aws', return_value={'events': []}), patch.object(waf.time, 'monotonic', side_effect=[0, 301]):
            with self.assertRaises(waf.Incomplete):
                waf.block_log(args(), 0)

    def test_private_files_and_incomplete_preflight(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'evidence'
            with patch.object(waf, 'preflight', side_effect=waf.Incomplete('Not confirmed')), patch.object(waf.Runner, 'perform') as http:
                self.assertEqual(waf.execute(args(str(root))), 2)
                http.assert_not_called()
            self.assertEqual(root.stat().st_mode & 0o777, 0o700)
            self.assertEqual((root / 'verification.json').stat().st_mode & 0o777, 0o600)
            self.assertNotIn('PASS', (root / 'verification.json').read_text())

    def test_automatic_release_requires_three_consecutive_unique_responses(self):
        values = fixtures()
        responses = iter([{'status': 200, 'probe_body': True}, good_block(),
            {'status': 200, 'probe_body': True}, {'status': 200, 'probe_body': False},
            {'status': 200, 'probe_body': True}, {'status': 200, 'probe_body': True},
            {'status': 200, 'probe_body': True}])
        with tempfile.TemporaryDirectory() as directory, patch.object(waf, 'aws', side_effect=lambda _, service, op, *rest, **kw: values[op]), patch.object(waf.Runner, 'load', return_value=(good_block(), 2)), patch.object(waf.Runner, 'perform', side_effect=lambda scheduled: next(responses)), patch.object(waf.time, 'sleep'):
            root = Path(directory) / 'evidence'
            self.assertEqual(waf.execute(args(str(root))), 0)
            result = json.loads((root / 'verification.json').read_text())
            self.assertEqual(result['checks']['automatic_release'], 'PASS')
            self.assertEqual(result['checks']['browser'], 'NOT_RUN')
            for path in root.iterdir():
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)


if __name__ == '__main__':
    unittest.main()
