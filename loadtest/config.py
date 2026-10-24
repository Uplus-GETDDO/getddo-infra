"""Fail-closed local configuration; never fetches AWS parameters."""
import json
import os
from pathlib import Path
import re
import stat
from urllib.parse import urlsplit
from uuid import UUID

MANIFEST = Path('/etc/getddo-loadtest/manifest.json')
SECRETS = Path('/run/secrets')


def protected_read(path, secret=False):
    path = Path(path)
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    fd = os.open(path, flags)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0:
            raise ValueError('configuration must be a root-owned regular file')
        forbidden = 0o077 if secret else 0o022
        if info.st_mode & forbidden or info.st_size > 65536:
            raise ValueError('unsafe configuration permissions or size')
        with os.fdopen(fd, 'r') as stream:
            fd = None
            return stream.read().strip()
    finally:
        if fd is not None:
            os.close(fd)


def load_manifest(path=MANIFEST):
    config = json.loads(protected_read(path))
    fields = {'environment', 'target', 'public_origin', 'expected_workers', 'scenario', 'actor_file'}
    if set(config) != fields | ({'scenario_file'} if config.get('scenario') == 'personal' else set()):
        raise ValueError('manifest fields do not match runtime contract')
    if config['environment'] != 'staging' or config['scenario'] not in ('smoke_events', 'personal'):
        raise ValueError('explicit supported staging scenario required')
    if not re.fullmatch(r'http://internal-[a-zA-Z0-9-]+\.ap-northeast-2\.elb\.amazonaws\.com', config['target']):
        raise ValueError('exact internal Stage HTTP ALB origin required')
    if config['scenario'] == 'personal' and config['scenario_file'] != '/run/personal/scenario.py':
        raise ValueError('personal scenario must use its protected mount')
    origin = urlsplit(config['public_origin'])
    if (origin.scheme != 'https' or not re.fullmatch(r'[a-z0-9]+\.cloudfront\.net', origin.netloc)
            or origin.path or origin.query or origin.fragment):
        raise ValueError('exact existing CloudFront HTTPS origin required')
    if type(config['expected_workers']) is not int or not 1 <= config['expected_workers'] <= 16:
        raise ValueError('expected_workers must be between 1 and 16')
    if config['actor_file'] != '/run/scenario/actor.json':
        raise ValueError('scenario actor must use its protected mount')
    return config


def load_actor(path):
    actor = json.loads(protected_read(path, secret=True))
    if (set(actor) != {'user_id', 'role', 'membership'} or actor['role'] != 'USER'
            or actor['membership'] not in ('excellent', 'vip', 'vvip')):
        raise ValueError('synthetic USER actor required')
    if str(UUID(actor['user_id'])) != actor['user_id']:
        raise ValueError('canonical seeded synthetic user UUID required')
    return actor


def load_auth(directory=SECRETS):
    values = [protected_read(Path(directory) / name, secret=True)
              for name in ('admin-user', 'admin-password-hash', 'session-secret')]
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', values[0]):
        raise ValueError('invalid administrator username')
    if not re.fullmatch(r'scrypt:32768:8:1\$[A-Za-z0-9]{16}\$[a-f0-9]{128}', values[1]):
        raise ValueError('Werkzeug default scrypt password hash required')
    if len(values[2]) < 32:
        raise ValueError('session secret too short')
    return values
