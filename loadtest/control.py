"""Authenticated HTTPS control of the existing Stage master; no AWS calls."""
import argparse
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import stat
import sys
import time

import requests

REPORT_FILES = ('run_stats.csv', 'run_stats_history.csv', 'run_failures.csv',
                'run_exceptions.csv', 'run.html', 'metadata.json')


def credential(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid not in (0, os.getuid())
                or info.st_mode & 0o077 or not 0 < info.st_size <= 8192):
            raise ValueError('unsafe credential file')
        with os.fdopen(fd) as stream:
            fd = None
            return stream.read().strip()
    finally:
        if fd is not None:
            os.close(fd)


class LoginToken(HTMLParser):
    token = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'input' and attrs.get('name') == 'csrf':
            self.token = attrs.get('value')


class Control:
    def __init__(self, origin, username, password):
        if not re.fullmatch(r'https://[a-z0-9]+\.cloudfront\.net', origin):
            raise ValueError('existing CloudFront HTTPS origin required')
        self.origin = origin
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.headers['Origin'] = origin
        page = self.call('GET', '/login')
        parser = LoginToken()
        parser.feed(page.text)
        if not parser.token:
            raise ValueError('login CSRF token missing')
        self.session.headers['X-CSRF-Token'] = parser.token
        self.call('POST', '/login_submit', expected=302,
                  data={'username': username, 'password': password})
        self.session.headers['X-CSRF-Token'] = self.call('GET', '/control/session').json()['csrf']

    def call(self, method, path, expected=200, **kwargs):
        response = self.session.request(method, self.origin + '/locust' + path,
                                        timeout=90, allow_redirects=False, **kwargs)
        if response.status_code != expected:
            # Do not include response bodies, cookies, headers or credentials.
            raise ValueError(f'control request refused (HTTP {response.status_code})')
        return response

    def run(self, users, spawn_rate, duration):
        self.call('POST', '/swarm', data={'user_count': users, 'spawn_rate': spawn_rate,
                                         'run_time': duration})
        return {'state': 'start_requested'}

    def stop(self):
        self.call('POST', '/stop')
        self.wait_stopped()
        return {'state': 'stopped'}

    def wait_stopped(self):
        deadline = time.monotonic() + 90
        while True:
            state = self.call('GET', '/control/session').json()
            if state['state'] in ('ready', 'stopped') and state['collectible']:
                return
            if time.monotonic() >= deadline:
                raise ValueError('master has not stopped; collection incomplete')
            time.sleep(1)

    def collect(self, output):
        self.wait_stopped()
        files = self.call('POST', '/control/collect').json()['files']
        if set(files) != set(REPORT_FILES) or len(files) != len(REPORT_FILES):
            raise ValueError('unexpected report manifest')
        output = Path(output)
        output.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = output.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077 or any(output.iterdir())):
            raise ValueError('collection requires an empty private owned directory')
        checksums = []
        for name in REPORT_FILES:
            content = self.call('GET', '/control/reports/' + name).content
            fd = os.open(output / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(content)
            checksums.append(hashlib.sha256(content).hexdigest() + '  ' + name)
        fd = os.open(output / 'SHA256SUMS', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as stream:
            stream.write('\n'.join(checksums) + '\n')
        return {'state': 'collected', 'files': len(REPORT_FILES)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--origin', required=True)
    parser.add_argument('--username-file', required=True)
    parser.add_argument('--password-file', required=True)
    commands = parser.add_subparsers(dest='command', required=True)
    run = commands.add_parser('run')
    run.add_argument('--users', required=True, type=int)
    run.add_argument('--spawn-rate', required=True, type=float)
    run.add_argument('--duration', required=True, type=int)
    commands.add_parser('stop')
    collect = commands.add_parser('collect')
    collect.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    try:
        client = Control(args.origin, credential(args.username_file), credential(args.password_file))
        if args.command == 'run':
            result = client.run(args.users, args.spawn_rate, args.duration)
        elif args.command == 'stop':
            result = client.stop()
        else:
            result = client.collect(args.output)
        print(json.dumps(result))
    except (OSError, ValueError, KeyError, requests.RequestException):
        print('Locust control failed; check protected configuration, authentication and master readiness.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
