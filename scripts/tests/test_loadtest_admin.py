"""Operator transaction tests; no Docker, network, AWS or database required."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch, Mock

spec = importlib.util.spec_from_file_location('loadtest_admin', Path(__file__).resolve().parents[1] / 'loadtest_admin.py')
admin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(admin)


class OperatorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.scenarios = self.root / 'scenarios' / 'gyeongju'
        self.scenarios.mkdir(parents=True)
        self.file = self.scenarios / 'events.py'
        self.file.write_text('value = 1\n')
        self.previous = self.root / 'releases' / 'original'
        self.previous.mkdir(parents=True)
        (self.root / 'active').symlink_to(self.previous)
        self.manifest = self.root / 'manifest.json'
        self.manifest.write_text(json.dumps({'scenario': 'smoke_events'}))
        self.config = {'run_id': 'a' * 32, 'public_origin': 'https://fixture.cloudfront.net',
                       'deploy_bucket': 'fixture-bucket', 'region': 'ap-northeast-2'}

    def test_release_hash_immutable_and_selection(self):
        release, digest = admin.scenario_snapshot(self.file, self.root)
        self.assertEqual(digest, hashlib.sha256(self.file.read_bytes()).hexdigest())
        self.assertEqual((release / 'scenario.py').read_bytes(), self.file.read_bytes())
        self.file.write_text('value = 2\n')
        self.assertNotEqual(admin.scenario_snapshot(self.file, self.root)[1], digest)
        self.assertEqual((release / 'scenario.py').read_text(), 'value = 1\n')
        admin.switch(release, self.root)
        self.assertEqual((self.root / 'active').resolve(), release)

    def test_invalid_syntax_does_not_switch(self):
        self.file.write_text('for broken')
        with self.assertRaises(SyntaxError):
            admin.scenario_snapshot(self.file, self.root)
        self.assertEqual((self.root / 'active').resolve(), self.previous)

    def test_outside_source_and_symlink_escape_rejected(self):
        outside = self.root / 'outside.py'
        outside.write_text('value = 1')
        self.file.unlink()
        self.file.symlink_to(outside)
        with self.assertRaises(ValueError):
            admin.scenario_snapshot(self.file, self.root)

    def test_apply_orders_collection_before_restart(self):
        calls = []
        client = Mock()
        with patch.object(admin, 'control', return_value=client), \
                patch.object(admin, 'verify_import'), \
                patch.object(admin, 'collect', side_effect=lambda *a: calls.append('collect') or {}), \
                patch.object(admin, 'compose', side_effect=lambda *a, **kw: calls.append(a[1])), \
                patch.object(admin, 'ready'):
            result = admin.apply(self.config, self.file, self.root, self.manifest)
        self.assertEqual(calls, ['collect', 'down', 'up'])
        self.assertEqual(result['state'], 'applied')
        self.assertEqual(json.loads(self.manifest.read_text())['scenario'], 'personal')
        client.call.assert_called_once_with('POST', '/control/maintenance', json={'enabled': True})

    def test_running_test_refusal_leaves_everything_untouched(self):
        client = Mock()
        client.call.side_effect = ValueError('control request refused (HTTP 409)')
        with patch.object(admin, 'control', return_value=client), patch.object(admin, 'compose') as compose:
            with self.assertRaises(ValueError):
                admin.apply(self.config, self.file, self.root, self.manifest)
        compose.assert_not_called()
        self.assertEqual((self.root / 'active').resolve(), self.previous)

    def test_failed_readback_never_restarts_or_switches(self):
        client = Mock()
        with patch.object(admin, 'control', return_value=client), \
                patch.object(admin, 'verify_import'), \
                patch.object(admin, 'upload_archive', side_effect=RuntimeError('readback mismatch')), \
                patch.object(admin, 'compose') as compose:
            with self.assertRaises(RuntimeError):
                admin.apply(self.config, self.file, self.root, self.manifest)
        compose.assert_not_called()
        self.assertEqual((self.root / 'active').resolve(), self.previous)
        client.call.assert_called_once_with('POST', '/control/maintenance', json={'enabled': True})
        self.assertTrue((self.root / '.archive-pending').is_file())

    def test_invalid_candidate_releases_maintenance_only_without_pending_archive(self):
        self.file.write_text('for broken')
        for pending in (False, True):
            if pending:
                (self.root / '.archive-pending').write_text('pending')
            client = Mock()
            with patch.object(admin, 'control', return_value=client):
                with self.assertRaises(SyntaxError):
                    admin.apply(self.config, self.file, self.root, self.manifest)
            self.assertEqual(client.call.call_count, 1 if pending else 2)
            client.call.assert_called_with('POST', '/control/maintenance',
                                           json={'enabled': pending})

    def test_failed_collection_keeps_lock_and_retry_clears_pending_after_readback(self):
        client = Mock()
        with patch.object(admin, 'control', return_value=client), \
                patch.object(admin, 'upload_archive', side_effect=RuntimeError('S3 unavailable')):
            with self.assertRaises(RuntimeError):
                admin.stop_and_collect(self.config, self.root)
        client.call.assert_called_once_with('POST', '/control/maintenance', json={'enabled': True})
        self.assertTrue((self.root / '.archive-pending').is_file())
        client.reset_mock()
        with patch.object(admin, 'control', return_value=client), \
                patch.object(admin, 'upload_archive', return_value={'verified': True}):
            self.assertEqual(admin.stop_and_collect(self.config, self.root), {'verified': True})
        self.assertFalse((self.root / '.archive-pending').exists())
        client.call.assert_called_with('POST', '/control/maintenance', json={'enabled': False})

    def test_failed_new_workers_restore_previous_manifest_and_release(self):
        original = self.manifest.read_bytes()
        client = Mock()
        with patch.object(admin, 'control', return_value=client), \
                patch.object(admin, 'verify_import'), patch.object(admin, 'collect', return_value={}), \
                patch.object(admin, 'compose') as compose, \
                patch.object(admin, 'ready', side_effect=[RuntimeError('readiness failed'), None]):
            with self.assertRaises(RuntimeError):
                admin.apply(self.config, self.file, self.root, self.manifest)
        self.assertEqual([call.args[1] for call in compose.call_args_list], ['down', 'up', 'down', 'up'])
        self.assertEqual((self.root / 'active').resolve(), self.previous)
        self.assertEqual(self.manifest.read_bytes(), original)

    def test_mysql_escaping_and_no_password_in_argv(self):
        config = dict(self.config, db_host='fixture.internal', db_name='getddo', db_user='loadtest',
                      db_password_parameter='/getddo/staging/db-test-password')
        captured = {}
        def launch(args, **kwargs):
            captured['args'] = args
            path = Path(args[1].split('=', 1)[1])
            captured['contents'] = path.read_text()
            captured['mode'] = path.stat().st_mode & 0o777
            captured['path'] = path
            return subprocess.CompletedProcess(args, 0)
        original = tempfile.TemporaryDirectory
        with patch.object(admin, 'parameter', return_value='password"with\\quotes'), \
                patch.object(admin.subprocess, 'run', side_effect=launch), \
                patch.object(admin.tempfile, 'TemporaryDirectory',
                             side_effect=lambda **kw: original(dir=self.root)):
            self.assertEqual(admin.database(config), 0)
        self.assertEqual(captured['mode'], 0o600)
        self.assertNotIn('password"with', ' '.join(captured['args']))
        self.assertIn('ssl-verify-server-cert="true"', captured['contents'])
        self.assertIn('ssl-ca="/etc/getddo/rds-ca.pem"', captured['contents'])
        self.assertFalse(captured['path'].exists())
        with self.assertRaises(ValueError):
            admin.mysql_value('unsafe\nvalue')

    def test_collection_preserves_executed_snapshot_and_edited_source_sql(self):
        (self.previous / 'scenario.py').write_text('executed = True\n')
        (self.scenarios / 'seed.sql').write_text('SELECT 1;\n')
        client = Mock()
        def download(path):
            path.mkdir()
            (path / 'run_stats.csv').write_text('requests,failures\n2,0\n')
        client.collect.side_effect = download
        def archive(config, directory):
            self.assertEqual((directory / 'active-scenario.py').read_text(), 'executed = True\n')
            self.assertEqual((directory / 'sources/gyeongju/events.py').read_bytes(), self.file.read_bytes())
            self.assertEqual((directory / 'sources/gyeongju/seed.sql').read_text(), 'SELECT 1;\n')
            metadata = json.loads((directory / 'operator-metadata.json').read_text())
            self.assertEqual(metadata['run_id'], self.config['run_id'])
            self.assertEqual(metadata['active_sha256'], hashlib.sha256(b'executed = True\n').hexdigest())
            return {'verified': True}
        with patch.object(admin, 'upload_archive', side_effect=archive):
            self.assertEqual(admin.collect(self.config, client, self.root), {'verified': True})

    def test_upload_verifies_evidence_and_receipt(self):
        directory = self.root / 'proof'
        directory.mkdir()
        (directory / 'proof.txt').write_text('expected')
        objects = {}
        downloads = []
        def aws(config, args):
            key = args[args.index('--key') + 1]
            if args[1] == 'put-object':
                objects[key] = Path(args[args.index('--body') + 1]).read_bytes()
            else:
                downloads.append(key)
                Path(args[-1]).write_bytes(objects[key])
            return b'{}'
        original = tempfile.TemporaryDirectory
        with patch.object(admin, 'aws', side_effect=aws), \
                patch.object(admin.tempfile, 'TemporaryDirectory',
                             side_effect=lambda **kw: original(dir=self.root)):
            receipt = admin.upload_archive(self.config, directory)
        self.assertEqual(len(downloads), 2)
        self.assertEqual(receipt['sha256']['proof.txt'], hashlib.sha256(b'expected').hexdigest())
        self.assertTrue(downloads[-1].endswith('/receipt.json'))

    def test_upload_detects_mismatched_readback(self):
        directory = self.root / 'proof'
        directory.mkdir()
        (directory / 'proof.txt').write_text('expected')
        def aws(config, args):
            if args[1] == 'get-object':
                Path(args[-1]).write_text('corrupted')
            return b'{}'
        original = tempfile.TemporaryDirectory
        with patch.object(admin, 'aws', side_effect=aws), \
                patch.object(admin.tempfile, 'TemporaryDirectory',
                             side_effect=lambda **kw: original(dir=self.root)):
            with self.assertRaisesRegex(RuntimeError, 'mismatch'):
                admin.upload_archive(self.config, directory)


if __name__ == '__main__':
    unittest.main()
