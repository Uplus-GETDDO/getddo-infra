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
