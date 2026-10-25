#!/usr/bin/env python3
"""Check an isolated CloudFront probe with bounded traffic and read-only AWS calls."""
import argparse
import base64
import concurrent.futures
import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

ACCOUNT = '942466928325'
DISTRIBUTION = 'E1RJ1BFYNZ30P1'
HOST = 'dzc8oneey9mmx.cloudfront.net'
ACL_NAME = 'getddo-dev-api-waf'
RULE = 'ApiIpRateLimit'
LOG_GROUP = 'aws-waf-logs-getddo-dev-api'
ERROR = {'success': False, 'code': 'COMMON-006',
         'message': '요청이 너무 많습니다. 잠시 후 다시 시도해 주세요.', 'data': None}
REDACTED = {'authorization', 'proxy-authorization', 'cookie', 'x-api-key',
            'x-user-id', 'x-user-role', 'x-user-membership'}
MAX_REQUESTS = 1800
MAX_SECONDS = 150
INTERVAL = 1 / 12


class Incomplete(Exception):
    pass


def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def private_json(root, name, value):
    path = root / name
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def aws(args, service, operation, *options, region='us-east-1'):
    try:
        result = subprocess.run(
            ['aws', service, operation, *options, '--profile', args.profile,
             '--region', region, '--output', 'json', '--no-cli-pager'],
            capture_output=True, text=True, timeout=30, check=True,
            env={**os.environ, 'AWS_MAX_ATTEMPTS': '1', 'AWS_PAGER': ''})
        return json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise Incomplete('AWS read could not be verified: ' + operation) from exc


def matches_error(body):
    try:
        value = json.loads(body)
    except (ValueError, UnicodeError):
        return False
    return (isinstance(value, dict) and set(value) == set(ERROR) and
            value.get('success') is False and value.get('data') is None and
            value.get('code') == ERROR['code'] and value.get('message') == ERROR['message'])


def require(condition, message):
    if not condition:
        raise Incomplete(message)


def validate_target(args):
    parsed = urllib.parse.urlsplit(args.base_url)
    require(parsed.scheme == 'https' and parsed.netloc == HOST and
            parsed.path in ('', '/') and not parsed.query and not parsed.fragment,
            'Base URL must match the approved HTTPS distribution')
    require(args.distribution_id == DISTRIBUTION and args.region == 'us-east-1',
            'Distribution or region mismatch')
    prefix = '/api/__waf_probe/'
    require(args.probe_path.startswith(prefix), 'Probe path mismatch')
    run_id = args.probe_path[len(prefix):]
    try:
        require(str(uuid.UUID(run_id)) == run_id, 'Probe UUID must be canonical')
    except ValueError as exc:
        raise Incomplete('Probe UUID is invalid') from exc
    origin = urllib.parse.urlsplit(args.origin)
    require(origin.scheme == 'https' and origin.netloc and not origin.username and
            not origin.password and not origin.path and not origin.query and
            not origin.fragment, 'Origin must be an HTTPS origin')
    return run_id


def normalize_search_strings(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key == 'SearchString' and child not in ('/api/', 'OPTIONS'):
                try:
                    value[key] = base64.b64decode(child, validate=True).decode('ascii')
                except (ValueError, UnicodeError):
                    pass
            else:
                normalize_search_strings(child)
    elif isinstance(value, list):
        for child in value:
            normalize_search_strings(child)


def preflight(args):
    identity = aws(args, 'sts', 'get-caller-identity')
    require(identity.get('Account') == ACCOUNT and
            identity.get('Arn') == f'arn:aws:iam::{ACCOUNT}:user/gyeongju',
            'AWS identity mismatch')
    distribution = aws(args, 'cloudfront', 'get-distribution', '--id', args.distribution_id)['Distribution']
    acl = aws(args, 'wafv2', 'get-web-acl', '--name', ACL_NAME,
              '--id', args.web_acl_id, '--scope', 'CLOUDFRONT')['WebACL']
    require(distribution.get('Status') == 'Deployed' and
            distribution.get('DomainName') == HOST and
            distribution['DistributionConfig'].get('Enabled') is True and
            distribution['DistributionConfig'].get('WebACLId') == acl.get('ARN') and
            acl.get('ARN', '').startswith(f'arn:aws:wafv2:us-east-1:{ACCOUNT}:global/webacl/{ACL_NAME}/'),
            'Deployed distribution and Web ACL do not match')
    rules = acl.get('Rules', [])
    require(len(rules) == 1 and rules[0].get('Name') == RULE, 'Unexpected WAF rules')
    rule = rules[0]
    rate = rule.get('Statement', {}).get('RateBasedStatement', {})
    actual_scope = json.loads(json.dumps(rate.get('ScopeDownStatement')))
    normalize_search_strings(actual_scope)
    expected_scope = {'AndStatement': {'Statements': [
        {'ByteMatchStatement': {'SearchString': '/api/', 'FieldToMatch': {'UriPath': {}},
         'TextTransformations': [{'Priority': 0, 'Type': 'NONE'}], 'PositionalConstraint': 'STARTS_WITH'}},
        {'NotStatement': {'Statement': {'ByteMatchStatement': {'SearchString': 'OPTIONS',
         'FieldToMatch': {'Method': {}}, 'TextTransformations': [{'Priority': 0, 'Type': 'NONE'}],
         'PositionalConstraint': 'EXACTLY'}}}}]}}
    require(rate.get('Limit') == 600 and rate.get('EvaluationWindowSec') == 60 and
            rate.get('AggregateKeyType') == 'IP' and
            actual_scope == expected_scope and
            set(rule.get('Action', {})) == {'Block'}, 'Actual rate rule is not 600/60/IP/Block')
    response = rule['Action']['Block'].get('CustomResponse', {})
    body = acl.get('CustomResponseBodies', {}).get(response.get('CustomResponseBodyKey'), {})
    valid_body = matches_error(body.get('Content', ''))
    headers = {h['Name'].lower(): h['Value'] for h in response.get('ResponseHeaders', [])}
    require(response.get('ResponseCode') == 429 and valid_body and
            body.get('ContentType') == 'APPLICATION_JSON' and
            headers == {'access-control-allow-origin': '*'}, 'WAF error contract mismatch')
    require(acl.get('DefaultAction') == {'Allow': {}} and all(
        value.get('VisibilityConfig', {}).get('SampledRequestsEnabled') is False
        for value in (acl, rule)), 'Default action or request sampling mismatch')
    logging = aws(args, 'wafv2', 'get-logging-configuration', '--resource-arn', acl['ARN'])['LoggingConfiguration']
    groups = aws(args, 'logs', 'describe-log-groups', '--log-group-name-prefix', LOG_GROUP)['logGroups']
    group = next((g for g in groups if g.get('logGroupName') == LOG_GROUP), {})
    expected_filter = {'DefaultBehavior': 'DROP', 'Filters': [{'Behavior': 'KEEP',
        'Requirement': 'MEETS_ALL', 'Conditions': [{'ActionCondition': {'Action': 'BLOCK'}}]}]}
    fields = logging.get('RedactedFields', [])
    require(group.get('retentionInDays') == 7 and
            logging.get('LogDestinationConfigs') == [group.get('arn', '').removesuffix(':*')] and
            logging.get('LoggingFilter') == expected_filter and
            {'QueryString': {}} in fields and
            REDACTED <= {f.get('SingleHeader', {}).get('Name') for f in fields},
            'Actual logging destination, filter, retention or redaction mismatch')
    balancers = aws(args, 'elbv2', 'describe-load-balancers', '--names',
                    'getddo-dev-api-alb', region='ap-northeast-2')['LoadBalancers']
    require(len(balancers) == 1, 'Probe load balancer is not unique')
    listeners = aws(args, 'elbv2', 'describe-listeners', '--load-balancer-arn',
                    balancers[0]['LoadBalancerArn'], region='ap-northeast-2')['Listeners']
    listeners = [item for item in listeners if item.get('Port') == 80]
    require(len(listeners) == 1, 'Probe listener is not unique')
    listener_rules = aws(args, 'elbv2', 'describe-rules', '--listener-arn',
                         listeners[0]['ListenerArn'], region='ap-northeast-2')['Rules']
    run_id = validate_target(args)
    matching = []
    for item in listener_rules:
        conditions = item.get('Conditions', [])
        paths = [c.get('PathPatternConfig', {}).get('Values', c.get('Values'))
                 for c in conditions if c.get('Field') == 'path-pattern']
        methods = [c.get('HttpRequestMethodConfig', {}).get('Values', c.get('Values'))
                   for c in conditions if c.get('Field') == 'http-request-method']
        if paths == [[args.probe_path]] and methods == [['GET']] and len(conditions) == 2:
            matching.append(item)
    require(len(matching) == 1, 'Actual probe UUID or method does not match')
    probe = matching[0]
    actions = probe.get('Actions', [])
    require(probe.get('Priority') in [str(i) for i in range(10, 20)] and
            len(actions) == 1 and actions[0].get('Type') == 'fixed-response' and
            actions[0].get('FixedResponseConfig') == {'StatusCode': '200',
                'ContentType': 'text/plain', 'MessageBody': 'waf-probe:' + run_id},
            'Actual probe response or priority does not match')
    return identity, acl, logging


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Runner:
    def __init__(self, args, run_id, request_file):
        self.args, self.run_id, self.request_file = args, run_id, request_file
        self.last_start = None
        self.sent = 0
        self.load_sent = 0

    def request(self, phase, headers=None):
        # Called only by the scheduler; one shared pacing clock covers all phases.
        now = time.monotonic()
        if self.last_start is not None:
            time.sleep(max(0, self.last_start + INTERVAL - now))
        self.last_start = time.monotonic()
        self.sent += 1
        number = self.sent
        started = utc()
        request = urllib.request.Request(self.args.base_url.rstrip('/') + self.args.probe_path,
                                         headers={'Origin': self.args.origin, **(headers or {})})
        return request, phase, number, started, self.last_start

    def perform(self, scheduled):
        request, phase, number, started, tick = scheduled
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
            try:
                response = opener.open(request, timeout=3)
            except urllib.error.HTTPError as exc:
                response = exc
            with response:
                status = response.code
                headers = {k.lower(): v for k, v in response.headers.items()}
                body = response.read(4097)
            error = matches_error(body)
            record = {'number': number, 'time': started, 'phase': phase, 'status': status,
                      'elapsed_seconds': round(time.monotonic() - tick, 3),
                      'probe_body': body == ('waf-probe:' + self.run_id).encode(),
                      'error_body': error,
                      'cors': headers.get('access-control-allow-origin') == '*',
                      'retry_after_absent': 'retry-after' not in headers,
                      'credentials_absent': 'access-control-allow-credentials' not in headers,
                      'json_content_type': headers.get('content-type', '').split(';')[0] == 'application/json'}
        except (OSError, ValueError):
            record = {'number': number, 'time': started, 'phase': phase, 'status': None,
                      'elapsed_seconds': round(time.monotonic() - tick, 3)}
        return record

    def save(self, record):
        self.request_file.write(json.dumps(record) + '\n')
        self.request_file.flush()
        return record

    def one(self, phase, headers=None):
        return self.save(self.perform(self.request(phase, headers)))

    def load(self):
        start = time.monotonic()
        count = 0
        blocked = None
        invalid_response = False
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            pending = set()
            while pending or (blocked is None and not invalid_response and count < MAX_REQUESTS and time.monotonic() - start < MAX_SECONDS):
                done = {future for future in pending if future.done()}
                for future in done:
                    record = self.save(future.result())
                    if valid_block(record):
                        blocked = record
                    elif not (record.get('status') == 200 and record.get('probe_body') is True):
                        invalid_response = True
                pending -= done
                if blocked is None and not invalid_response and len(pending) < 2 and count < MAX_REQUESTS and time.monotonic() - start + INTERVAL < MAX_SECONDS:
                    scheduled = self.request('load')
                    # Observe completions again after pacing, before starting more traffic.
                    if any(f.done() and not (f.result().get('status') == 200 and
                            f.result().get('probe_body') is True) for f in pending):
                        self.sent -= 1
                        continue
                    if time.monotonic() - start >= MAX_SECONDS:
                        self.sent -= 1
                        continue
                    pending.add(pool.submit(self.perform, scheduled))
                    count += 1
                    self.load_sent = count
                elif pending:
                    concurrent.futures.wait(pending, timeout=INTERVAL,
                                            return_when=concurrent.futures.FIRST_COMPLETED)
                else:
                    break
        if invalid_response:
            raise Incomplete('Probe response changed or failed; new traffic stopped and in-flight requests joined')
        return blocked, count


def valid_block(record):
    return record.get('status') == 429 and record.get('error_body') is True


def block_contract(record):
    return valid_block(record) and all(record.get(key) is True for key in
        ('cors', 'retry_after_absent', 'credentials_absent', 'json_content_type'))


def block_log(args, since):
    deadline = time.monotonic() + 300
    while True:
        events = aws(args, 'logs', 'filter-log-events', '--log-group-name', LOG_GROUP,
                     '--start-time', str(since), '--filter-pattern',
                     '{ $.httpRequest.uri = "' + args.probe_path + '" }').get('events', [])
        for event in events:
            try:
                value = json.loads(event['message'])
            except (KeyError, ValueError):
                continue
            request = value.get('httpRequest', {})
            if (value.get('action') == 'BLOCK' and value.get('terminatingRuleId') == RULE and
                    value.get('responseCodeSent') == 429 and request.get('uri') == args.probe_path and
                    request.get('httpMethod') == 'GET'):
                return {key: value.get(key) for key in
                        ('timestamp', 'action', 'terminatingRuleId', 'responseCodeSent')} | {
                            'uri': args.probe_path, 'method': 'GET'}
        if time.monotonic() + 10 > deadline:
            raise Incomplete('BLOCK log delivery was not confirmed within five minutes')
        time.sleep(10)


def execute(args):
    run_id = validate_target(args)
    root = Path(args.evidence_dir)
    root.mkdir(mode=0o700, parents=False, exist_ok=False)
    os.chmod(root, 0o700)
    checks = {name: 'NOT_RUN' for name in ('baseline', 'block', 'response', 'forwarded_header',
        'automatic_release', 'block_log', 'browser', 'other_ip', 'normal_reads', 'options',
        'image', 'initial_allow_redaction', 'metrics', 'cleanup')}
    result = {'checks': checks, 'limits': {'requests': MAX_REQUESTS, 'seconds': MAX_SECONDS,
              'requests_per_second': 12, 'concurrency': 2, 'timeout_seconds': 3}, 'started': utc()}
    code = 2
    runner = None
    run_metadata = None
    try:
        identity, acl, logging = preflight(args)
        private_json(root, 'waf-state.json', acl)
        private_json(root, 'logging-state.json', logging)
        revision = subprocess.run(['git', 'rev-parse', 'HEAD'], check=True,
            capture_output=True, text=True, timeout=5).stdout.strip()
        run_metadata = {'run_id': run_id, 'started': result['started'],
            'identity': identity['Arn'], 'distribution': args.distribution_id,
            'web_acl': acl['ARN'], 'probe_path': args.probe_path, 'source_sha': revision,
            'settings': {'limit': 600, 'window_seconds': 60, 'aggregation': 'IP', 'mode': 'block'}}
        since = int(time.time() * 1000)
        fd = os.open(root / 'requests.jsonl', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as stream:
            runner = Runner(args, run_id, stream)
            baseline = runner.one('baseline')
            require(baseline.get('status') == 200 and baseline.get('probe_body'), 'Unique probe baseline was not confirmed')
            checks['baseline'] = 'PASS'
            blocked, count = runner.load()
            result['load_requests'] = count
            require(blocked is not None, 'No valid block observed within traffic limits')
            checks['block'] = 'PASS'
            checks['response'] = 'PASS' if block_contract(blocked) else 'FAIL'
            forwarded = runner.one('contract', {'X-Forwarded-For': '192.0.2.123'})
            checks['forwarded_header'] = 'PASS' if valid_block(forwarded) else 'NOT_RUN'
            deadline = time.monotonic() + 300
            consecutive = 0
            while time.monotonic() + 10 <= deadline:
                time.sleep(10)
                record = runner.one('release')
                consecutive = consecutive + 1 if record.get('status') == 200 and record.get('probe_body') else 0
                if consecutive == 3:
                    checks['automatic_release'] = 'PASS'
                    result['released_at'] = utc()
                    break
            if consecutive < 3:
                checks['automatic_release'] = 'FAIL'
            result['total_requests'] = runner.sent
        # Compare actual settings again: recovery must not depend on changing the rule.
        preflight(args)
        proof = block_log(args, since)
        private_json(root, 'log-proof.json', {'block': proof})
        checks['block_log'] = 'PASS'
        core = ('baseline', 'block', 'response', 'forwarded_header', 'automatic_release', 'block_log')
        code = 1 if 'FAIL' in checks.values() else (0 if all(checks[k] == 'PASS' for k in core) else 2)
    except KeyboardInterrupt:
        result['reason'] = 'Verification interrupted; in-flight requests joined'
    except Incomplete as exc:
        result['reason'] = str(exc)
    except (OSError, KeyError, ValueError, TypeError, subprocess.SubprocessError):
        result['reason'] = 'Verification environment or response structure could not be confirmed'
    if runner is not None:
        result['total_requests'] = runner.sent
        result['load_requests'] = runner.load_sent
    result['finished'] = utc()
    if run_metadata is not None:
        run_metadata['finished'] = result['finished']
        private_json(root, 'run.json', run_metadata)
    result['exit_code'] = code
    result['scope'] = 'Automatic probe checks only; NOT_RUN cases require separate evidence'
    private_json(root, 'verification.json', result)
    return code


def main():
    if sys.version_info < (3, 12):
        print('Python 3.12 or later is required.')
        return 2
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('base-url', 'probe-path', 'web-acl-id', 'distribution-id', 'origin', 'evidence-dir'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--profile', default='getddo-gyeongju')
    parser.add_argument('--region', default='us-east-1')
    args = parser.parse_args()
    try:
        return execute(args)
    except (Incomplete, OSError):
        print('Verification could not start; check target arguments and private evidence directory.')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
