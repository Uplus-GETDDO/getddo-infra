"""An extracted source bundle must remain an actual detached Git checkout."""
import hashlib
import importlib.util
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('staging_bundle_builder',
    Path(__file__).resolve().parents[1] / 'build-staging-bundle.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class StagingBundleTests(unittest.TestCase):
    def git(self, repository, *arguments):
        result = subprocess.run(['git', '-C', str(repository), *arguments],
                                capture_output=True, text=True, check=True)
        return result.stdout.strip()

    def commit(self, repository, files):
        repository.mkdir()
        self.git(repository, 'init')
        self.git(repository, 'symbolic-ref', 'HEAD', 'refs/heads/main')
        for name, content in files.items():
            path = repository / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        self.git(repository, 'add', '.')
        self.git(repository, '-c', 'user.name=Bundle fixture', '-c', 'user.email=bundle@example.invalid',
                 'commit', '-m', 'fixture')
        return self.git(repository, 'rev-parse', 'HEAD')

    def test_roundtrip_keeps_empty_git_refs_for_detached_backend(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            infra = root / 'infra'
            backend = root / 'backend'
            required = ['scripts/staging_hooks/prepare.py', 'scripts/staging_hooks/collect.py',
                        'scripts/staging_hooks/detach.py', 'scripts/staging_runner.py',
                        'loadtest/control.py', 'terraform/staging/.terraform.lock.hcl']
            infra_sha = self.commit(infra, {name: '# fixture\n' for name in required})
            # Artifact validation is outside this packaging regression. The tiny
            # fixture module stands in for its independent validation contract.
            backend_sha = self.commit(backend, {
                'infra/scripts/deploy-staging.py': 'def validate_artifact(*args):\n    pass\n',
                'README.md': 'backend fixture\n',
            })
            self.git(backend, 'checkout', '--detach', backend_sha)
            self.git(backend, 'branch', '-D', 'main')
            self.assertFalse(any(path.is_file() for path in (backend/'.git/refs').rglob('*')))
            artifact = root/'artifact'
            artifact.mkdir()
            image = b'packaging fixture, not a Docker archive'
            (artifact/'image.tar.gz').write_bytes(image)
            (artifact/'image.tar.gz.sha256').write_text(hashlib.sha256(image).hexdigest() + '  image.tar.gz\n')
            (artifact/'image-id.txt').write_text('sha256:' + 'a'*64 + '\n')
            output = root/'bundle.tar.gz'
            result = builder.package(infra, infra_sha, backend, backend_sha, artifact, output)
            extracted = root/'extracted'
            extracted.mkdir()
            with tarfile.open(output) as bundle:
                bundle.extractall(extracted, filter='data')
            checkout = extracted/'backend/source'
            self.assertEqual(self.git(checkout, 'rev-parse', 'HEAD'), backend_sha)
            self.assertEqual(self.git(checkout, 'status', '--porcelain'), '')
            self.assertTrue((checkout/'.git/refs').is_dir())
            self.assertEqual(self.git(checkout, 'remote', 'get-url', 'origin'),
                             'https://github.com/Uplus-GETDDO/getddo-be.git')
            self.assertEqual(result['bundle_sha256'], hashlib.sha256(output.read_bytes()).hexdigest())
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)


if __name__ == '__main__':
    unittest.main()
