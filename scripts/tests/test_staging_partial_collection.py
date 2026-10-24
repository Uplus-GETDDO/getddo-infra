"""Known shared-export limitations remain explicit without losing mandatory evidence."""
import datetime as dt
import hashlib
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

from botocore.response import StreamingBody

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_staging_hooks import aws_for, hooks, manifest
from staging_aws import Effects


class PartialCollectionTests(unittest.TestCase):
    def hook(self, hours=72, expired=False, corrupted=False):
        data = manifest()
        now = dt.datetime.now(dt.timezone.utc)
        created = now - dt.timedelta(hours=hours + 1) if expired else now - dt.timedelta(minutes=1)
        data.update(created_at=created.isoformat(), expires_at=(created + dt.timedelta(hours=hours)).isoformat(),
                    max_duration_minutes=hours*60)
        aws = aws_for(data)
        hook = hooks.Hooks(data, aws)
        payload = b'Locust report with Python and SQL sources'
        receipt = {'key': f"staging/runs/{data['run_id']}/results/locust-test.tar.gz",
                   'sha256': hashlib.sha256(payload).hexdigest()}
        body = io.BytesIO(payload + (b'changed' if corrupted else b''))
        aws.clients['s3'].get_object.return_value = {'Body': StreamingBody(body, len(body.getvalue()))}
        hook.runtime = Mock(return_value=('runtime-key', 'd'*64))
        hook.host_script = Mock(return_value='collect-script')
        hook.remote = Mock(return_value=json.dumps(receipt))
        hook.monitoring = Mock(side_effect=RuntimeError('must not hide this authentication or ownership failure'))
        return hook, aws.clients['s3'], body, receipt

    def metadata(self):
        return patch.object(hooks.Path, 'read_text', return_value=json.dumps({
            'source_sha': 'e'*40, 'image_id': 'sha256:'+'f'*64, 'archive_sha256': '1'*64}))

    def test_known_windows_save_verified_locust_and_mark_partial(self):
        for hours, expired, reason in ((72, False, 'lifetime_exceeds_monitoring_limit'),
                                       (72, True, 'lifetime_exceeds_monitoring_limit'),
                                       (1, True, 'expired_monitoring_registration')):
            with self.subTest(hours=hours, expired=expired):
                hook, s3, body, locust = self.hook(hours, expired)
                with self.metadata():
                    result = hook.collect()
                index_bytes = s3.put_object.call_args.kwargs['Body']
                index = json.loads(index_bytes)
                self.assertEqual(index['locust'], locust)
                self.assertEqual(index['collection_status'], 'partial')
                self.assertEqual(index['monitoring'], {'status': 'unavailable', 'reason': reason})
                self.assertEqual(result['collection_status'], 'partial')
                self.assertEqual(result['monitoring_reason'], reason)
                self.assertEqual(result['sha256'], hashlib.sha256(index_bytes).hexdigest())
                self.assertTrue(body.closed)
                hook.monitoring.assert_not_called()
                s3.get_object.assert_called_once_with(Bucket=hook.manifest['deploy_bucket'], Key=locust['key'])

    def test_partial_never_tolerates_corrupt_mandatory_evidence(self):
        hook, s3, body, _ = self.hook(corrupted=True)
        with self.metadata(), self.assertRaisesRegex(ValueError, 'Collected result readback mismatch'):
            hook.collect()
        self.assertTrue(body.closed)
        hook.monitoring.assert_not_called()
        s3.put_object.assert_not_called()

    def test_supported_window_keeps_monitoring_errors_strict_after_locust_verification(self):
        hook, s3, body, _ = self.hook(hours=1)
        with self.metadata(), self.assertRaisesRegex(RuntimeError, 'authentication or ownership'):
            hook.collect()
        self.assertTrue(body.closed)
        hook.monitoring.assert_called_once_with('collect', timeout=180)
        s3.put_object.assert_not_called()

    def test_aws_adapter_retains_partial_status_in_manifest_after_index_readback(self):
        data = manifest()
        payload = b'{"collection_status":"partial"}'
        receipt = {'key': f"staging/runs/{data['run_id']}/results/index-test.json",
                   'sha256': hashlib.sha256(payload).hexdigest(), 'collection_status': 'partial',
                   'monitoring_status': 'unavailable', 'monitoring_reason': 'lifetime_exceeds_monitoring_limit'}
        effects = object.__new__(Effects)
        effects.hook = Mock(return_value=json.dumps(receipt))
        effects.record = Mock()
        s3 = Mock()
        s3.get_object.return_value = {'Body': StreamingBody(io.BytesIO(payload), len(payload))}
        effects.clients = {'s3': s3}
        effects.collect(data, timeout=360)
        self.assertEqual(data['results'], receipt)
        effects.record.assert_called_once_with(data, 'collected')


if __name__ == '__main__':
    unittest.main()
