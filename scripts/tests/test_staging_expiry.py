import copy
import datetime as dt
from pathlib import Path
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from staging_lifecycle import check_owner, expiry_fields, validate_manifest
from test_staging_lifecycle import manifest


class ExpiryTests(unittest.TestCase):
    now = dt.datetime(2026, 10, 23, 12, 0, 0, 123456, tzinfo=dt.timezone.utc)

    def test_absolute_monday_deadline_preserved_with_integer_ceiling_minutes(self):
        value = manifest()
        fields = expiry_fields(self.now, expires_at="2026-10-26T20:00:00+09:00")
        self.assertEqual(fields["expires_at"], "2026-10-26T20:00:00+09:00")
        self.assertEqual(fields["max_duration_minutes"], 4260)
        self.assertIs(type(fields["max_duration_minutes"]), int)
        value.update(fields, created_at=self.now.isoformat())
        self.assertEqual(validate_manifest(value), value)

    def test_utc_and_korean_offset_resolve_to_same_instant(self):
        utc = expiry_fields(self.now, expires_at="2026-10-26T11:00:00Z")
        korean = expiry_fields(self.now, expires_at="2026-10-26T20:00:00+09:00")
        self.assertEqual(dt.datetime.fromisoformat(utc["expires_at"]),
                         dt.datetime.fromisoformat(korean["expires_at"]))
        self.assertEqual(utc["max_duration_minutes"], korean["max_duration_minutes"])

    def test_absolute_requires_timezone_and_bounds(self):
        for expiry in ("2026-10-26T20:00:00", "bad", "2026-10-23T11:59:59Z",
                       "2026-10-23T12:14:59Z", "2026-10-27T12:00:01Z"):
            with self.subTest(expiry=expiry), self.assertRaises(ValueError):
                expiry_fields(self.now, expires_at=expiry)

    def test_exact_lower_and_upper_bounds(self):
        for minutes in (15, 5760):
            expiry = (self.now + dt.timedelta(minutes=minutes)).isoformat()
            value = manifest()
            value.update(expiry_fields(self.now, expires_at=expiry), created_at=self.now.isoformat())
            self.assertEqual(validate_manifest(value), value)
            self.assertEqual(expiry_fields(self.now, max_duration=minutes)["max_duration_minutes"], minutes)
        for minutes in (0, 14, 5761, True, 60.5):
            with self.subTest(minutes=minutes), self.assertRaises(ValueError):
                expiry_fields(self.now, max_duration=minutes)

    def test_existing_relative_manifest_keeps_exact_equality(self):
        value = manifest()
        self.assertEqual(validate_manifest(value), value)
        changed = copy.deepcopy(value)
        changed["expires_at"] = (dt.datetime.fromisoformat(value["expires_at"]) - dt.timedelta(seconds=1)).isoformat()
        with self.assertRaises(ValueError):
            validate_manifest(changed)

    def test_absolute_manifest_cannot_extend_beyond_bound_or_change_ownership(self):
        value = manifest()
        value.update(expiry_fields(self.now, expires_at="2026-10-26T20:00:00+09:00"), created_at=self.now.isoformat())
        other = dict(value, run_id="other-owner")
        with self.assertRaises(RuntimeError):
            check_owner(other, value)
        for replacement in ({"max_duration_minutes": 4261}, {"expiry_mode": "unknown"},
                            {"max_duration_minutes": 5760, "expires_at": "2026-10-27T12:00:01+00:00"}):
            with self.subTest(replacement=replacement), self.assertRaises(ValueError):
                validate_manifest(dict(value, **replacement))

    def test_exactly_one_expiry_option_required(self):
        for kwargs in ({}, {"max_duration": 60, "expires_at": "2026-10-26T20:00:00+09:00"}):
            with self.assertRaises(ValueError):
                expiry_fields(self.now, **kwargs)

    def test_cli_rejects_invalid_input_before_any_aws_client(self):
        script = str(Path(__file__).resolve().parents[1] / "staging.py")
        for args in (("up", "--bundle", "missing", "--config", "missing"),
                     ("up", "--bundle", "missing", "--config", "missing", "--expires-at", "2026-10-26T20:00:00"),
                     ("up", "--max-duration", "60", "--expires-at", "2026-10-26T20:00:00+09:00"),
                     ("status", "--max-duration", "60")):
            result = subprocess.run([sys.executable, script, *args], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
