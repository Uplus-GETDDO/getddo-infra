"""Exercise the executor checksum command against its actual download filename."""
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]


class ControlBootstrapTest(unittest.TestCase):
    def test_stage_buildx_checksum_is_verified_before_install(self):
        source = (ROOT/'terraform/staging/templates/host.sh').read_text()
        block = source[source.index('# Compose 2.40.3'):source.index('version=1.12.1')]
        official_digest = '7d2d7d6d4680aa349614965aaa33ccec43f1a9a21e908a5ce4cb6adfa5ad5141'
        self.assertIn('https://github.com/docker/buildx/releases/download/v0.29.1/buildx-v0.29.1.linux-amd64',block)
        self.assertIn(official_digest,block)
        payload = b'reviewed buildx fixture'
        block = block.replace(official_digest,hashlib.sha256(payload).hexdigest())
        for valid in (True,False):
            with self.subTest(valid=valid), tempfile.TemporaryDirectory() as directory:
                work = Path(directory)
                (work/'fixture').write_bytes(payload if valid else b'tampered buildx')
                script = '''set -eu
work=$1
curl() { cp "$work/fixture" "$work/docker-buildx"; }
install() {
  test "$1" = -m && test "$2" = 0755
  test "$4" = /usr/local/lib/docker/cli-plugins/docker-buildx
  touch "$work/installed"
}
docker() { test "$*" = 'buildx version'; touch "$work/verified"; }
'''+block
                result = subprocess.run(['bash','-c',script,'fixture',directory],capture_output=True,timeout=10)
                self.assertEqual(result.returncode == 0,valid)
                self.assertEqual((work/'installed').exists(),valid)
                self.assertEqual((work/'verified').exists(),valid)

    def test_download_name_matches_published_checksum_and_unzip_input(self):
        lines = (ROOT/'terraform/staging-control/main.tf').read_text().splitlines()
        commands = [json.loads(line.strip().rstrip(',')) for line in lines
                    if line.strip().startswith(('"curl ', '"cd /tmp ', '"unzip '))]
        download = next(shlex.split(command) for command in commands if 'linux_amd64.zip' in command and command.startswith('curl '))
        checksum = next(command for command in commands if command.startswith('cd /tmp '))
        unzip = next(shlex.split(command) for command in commands if command.startswith('unzip '))
        published_name = Path(urlsplit(download[-1]).path).name
        payload = b'test terraform release archive'
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            (work/Path(download[2]).name).write_bytes(payload)
            (work/'terraform.sha').write_text(hashlib.sha256(payload).hexdigest()+'  '+published_name+'\n')
            result = subprocess.run(['bash', '-c', checksum.replace('cd /tmp ', 'cd '+shlex.quote(directory)+' ', 1)],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(download[2], unzip[2])
