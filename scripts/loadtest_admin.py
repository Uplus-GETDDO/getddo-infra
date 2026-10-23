#!/usr/bin/env python3
"""Host-side Stage operator commands. Run using the installed sudo wrapper.

Trusted team Python executes with Locust container privileges, not in a sandbox.
The root-owned operator.json is installed by the Stage lifecycle owner.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path('/opt/getddo-loadtest')
OPERATOR = Path('/etc/getddo-loadtest/operator.json')
MANIFEST = Path('/etc/getddo-loadtest/manifest.json')


def command(args, **kwargs):
    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=kwargs.pop('timeout', 180), **kwargs)
    if result.returncode:
        raise RuntimeError('operator subprocess failed: ' + Path(args[0]).name)
    return result.stdout


def configuration(path=OPERATOR):
    from loadtest.config import protected_read
    config = json.loads(protected_read(path, secret=True))
    required = {'source_dir', 'run_id', 'public_origin', 'expected_workers', 'locust_bind_ip',
                'deploy_bucket', 'region', 'db_host', 'db_name', 'db_user', 'db_password_parameter'}
    if set(config) != required or not re.fullmatch(r'[a-f0-9]{32}', config['run_id']):
        raise ValueError('invalid operator configuration')
    if config['expected_workers'] != 2 or config['region'] != 'ap-northeast-2':
        raise ValueError('unexpected Stage region or worker count')
    return config


def aws(config, args):
    return command(['aws', '--region', config['region'], *args], timeout=240)


def parameter(config, name):
    return json.loads(aws(config, ['ssm', 'get-parameter', '--name', name,
                                  '--with-decryption', '--output', 'json']))['Parameter']['Value']


def control(config):
    from loadtest.control import Control
    prefix = '/getddo/staging/locust/'
    return Control(config['public_origin'], parameter(config, prefix + 'admin-user'),
                   parameter(config, prefix + 'admin-password'))


def compose(config, *args, timeout=240):
    env = dict(os.environ, LOCUST_BIND_IP=config['locust_bind_ip'])
    return command(['docker', 'compose', '-f', 'loadtest/compose.yaml', *args],
                   cwd=config['source_dir'], env=env, timeout=timeout)


def private_write(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)


@contextmanager
def locked(root=ROOT):
    root.mkdir(parents=True, exist_ok=True)
    fd = os.open(root / '.operator.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def scenario_snapshot(path, root=ROOT):
    path = Path(path).resolve(strict=True)
    scenarios = (root / 'scenarios').resolve()
    if scenarios not in path.parents or path.suffix != '.py' or not path.is_file():
        raise ValueError('select a .py file within the team scenarios directory')
    content = path.read_bytes()
    if len(content) > 1024 * 1024:
        raise ValueError('scenario exceeds 1 MiB')
    compile(content, str(path), 'exec')
    digest = hashlib.sha256(content).hexdigest()
    release = root / 'releases' / digest
    release.mkdir(parents=True, mode=0o700, exist_ok=True)
    # Never rewrite an existing release used by running containers.
    destination = release / 'scenario.py'
    if destination.exists():
        if destination.read_bytes() != content:
            raise ValueError('snapshot hash mismatch')
    else:
        fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
    return release, digest


def verify_import(config, release):
    # Validate imports/tasks using the exact installed runtime image without
    # adding another master/worker or mounting administrator credentials.
    command(['docker', 'run', '--rm', '--network', 'none', '--read-only',
             '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
             '--memory', '256m', '--cpus', '0.5',
             '--volume', str(release) + ':/run/candidate:ro', '--entrypoint', 'python',
             'getddo-stage-locust:2.47.0', '-c',
             'from loadtest.runtime import load_personal; load_personal("/run/candidate/scenario.py")'],
            timeout=60)


def switch(release, root=ROOT):
    active = root / 'active'
    temporary = root / ('.active-' + uuid.uuid4().hex)
    temporary.symlink_to(release)
    temporary.replace(active)


def ready(config):
    import requests
    deadline = time.monotonic() + 120
    session = requests.Session()
    session.trust_env = False
    while time.monotonic() < deadline:
        try:
            response = session.get(config['public_origin'] + '/locust/health', timeout=10)
            if response.status_code == 200 and response.json().get('ready') is True:
                return
        except (requests.RequestException, ValueError):
            pass
        time.sleep(2)
    raise RuntimeError('Locust master and two workers did not become ready')


def upload_archive(config, directory):
    """Read back every uploaded file before permitting replacement/deletion."""
    prefix = ('staging/runs/' + config['run_id'] + '/results/operator/' + directory.name + '/')
    hashes = {}
    for path in sorted(directory.rglob('*')):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(directory).as_posix()
        key = prefix + relative
        content = path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        aws(config, ['s3api', 'put-object', '--bucket', config['deploy_bucket'], '--key', key,
                     '--body', str(path), '--server-side-encryption', 'AES256'])
        with tempfile.TemporaryDirectory(prefix='loadtest-readback-', dir='/run') as temp:
            downloaded = Path(temp) / 'readback'
            aws(config, ['s3api', 'get-object', '--bucket', config['deploy_bucket'], '--key', key,
                         str(downloaded)])
            if hashlib.sha256(downloaded.read_bytes()).hexdigest() != digest:
                raise RuntimeError('S3 archive readback mismatch')
        hashes[relative] = digest
    receipt = {'bucket': config['deploy_bucket'], 'prefix': prefix, 'sha256': hashes}
    receipt_path = directory / 'receipt.json'
    private_write(receipt_path, (json.dumps(receipt, indent=2) + '\n').encode())
    aws(config, ['s3api', 'put-object', '--bucket', config['deploy_bucket'], '--key', prefix + 'receipt.json',
                 '--body', str(receipt_path), '--server-side-encryption', 'AES256'])
    with tempfile.TemporaryDirectory(prefix='loadtest-receipt-', dir='/run') as temp:
        downloaded = Path(temp) / 'receipt.json'
        aws(config, ['s3api', 'get-object', '--bucket', config['deploy_bucket'],
                     '--key', prefix + 'receipt.json', str(downloaded)])
        if downloaded.read_bytes() != receipt_path.read_bytes():
            raise RuntimeError('S3 receipt readback mismatch')
    return receipt


def collect(config, client, root=ROOT):
    directory = root / 'reports' / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + uuid.uuid4().hex[:8])
    directory.mkdir(parents=True, mode=0o700)
    # The web collection clears its uncollected flag before S3 is verified.
    # Keep maintenance locked until this durable marker is cleared on success.
    private_write(root / '.archive-pending', json.dumps({
        'run_id': config['run_id'], 'directory': str(directory)}).encode())
    client.collect(directory / 'locust')
    active = root / 'active' / 'scenario.py'
    if active.is_file():
        private_write(directory / 'active-scenario.py', active.read_bytes())
    # Original editable Python and SQL files must survive final host deletion.
    for path in sorted((root / 'scenarios').rglob('*')):
        if path.is_symlink():
            raise ValueError('scenario archives cannot contain symlinks')
        if path.is_file() and path.suffix in ('.py', '.sql'):
            if path.stat().st_size > 10 * 1024 * 1024:
                raise ValueError('scenario/SQL archive member exceeds 10 MiB')
            destination = directory / 'sources' / path.relative_to(root / 'scenarios')
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            private_write(destination, path.read_bytes())
    private_write(directory / 'operator-metadata.json', json.dumps({
        'run_id': config['run_id'], 'collected_at': datetime.now(timezone.utc).isoformat(),
        'source': str((root / 'active').resolve()),
        'active_sha256': hashlib.sha256(active.read_bytes()).hexdigest() if active.is_file() else None}).encode())
    receipt = upload_archive(config, directory)
    (root / '.archive-pending').unlink()
    return receipt


def apply(config, path, root=ROOT, manifest_path=MANIFEST):
    client = control(config)
    # Server-side maintenance closes the UI-start race during collect/switch.
    client.call('POST', '/control/maintenance', json={'enabled': True})
    changed = False
    previous = (root / 'active').resolve()
    previous_manifest = manifest_path.read_bytes()
    try:
        release, digest = scenario_snapshot(path, root)
        verify_import(config, release)
        receipt = collect(config, client, root)
        changed = True
        compose(config, 'down', '--timeout', '30')
        switch(release, root)
        manifest = json.loads(previous_manifest)
        manifest.update(scenario='personal', scenario_file='/run/personal/scenario.py')
        private_write(manifest_path, json.dumps(manifest).encode())
        compose(config, 'up', '-d', '--scale', 'worker=2', '--no-build')
        ready(config)
        return {'state': 'applied', 'sha256': digest, 'workers': 2, 'previous_results': receipt,
                'url': config['public_origin'] + '/locust/'}
    except Exception:
        if changed:
            compose(config, 'down', '--timeout', '30')
            switch(previous, root)
            private_write(manifest_path, previous_manifest)
            compose(config, 'up', '-d', '--scale', 'worker=2', '--no-build')
            ready(config)
        raise
    finally:
        # A new master naturally starts outside maintenance mode.
        if not changed and not (root / '.archive-pending').exists():
            client.call('POST', '/control/maintenance', json={'enabled': False})


def stop_and_collect(config, root=ROOT):
    client = control(config)
    client.stop()
    client.call('POST', '/control/maintenance', json={'enabled': True})
    receipt = collect(config, client, root)
    client.call('POST', '/control/maintenance', json={'enabled': False})
    return receipt


def mysql_value(value):
    if '\x00' in value or '\r' in value or '\n' in value:
        raise ValueError('invalid MySQL configuration value')
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'


def database(config):
    password = parameter(config, config['db_password_parameter'])
    text = '[client]\n' + ''.join(key + '=' + mysql_value(value) + '\n' for key, value in (
        ('host', config['db_host']), ('user', config['db_user']), ('password', password),
        ('database', config['db_name']), ('protocol', 'TCP'),
        ('ssl-ca', '/etc/getddo/rds-ca.pem'), ('ssl-verify-server-cert', 'true')))
    # tmpfs and 0600; no password in argv, environment, stdout or shell history.
    with tempfile.TemporaryDirectory(prefix='loadtest-mysql-', dir='/run') as temporary:
        defaults = Path(temporary) / 'client.cnf'
        private_write(defaults, text.encode())
        result = subprocess.run(['mysql', '--defaults-extra-file=' + str(defaults)], check=False)
        return result.returncode


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('collect')
    commands.add_parser('db')
    change = commands.add_parser('apply')
    change.add_argument('file', type=Path)
    args = parser.parse_args(argv)
    try:
        if os.geteuid() != 0:
            raise ValueError('use the installed loadtest sudo wrapper')
        config = configuration()
        if args.command == 'db':
            return database(config)
        with locked():
            result = (apply(config, args.file) if args.command == 'apply'
                      else stop_and_collect(config))
        print(json.dumps(result))
        return 0
    except Exception:
        print('loadtest operation failed; check idle state, Python syntax/imports, AWS permissions and host logs.',
              file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
