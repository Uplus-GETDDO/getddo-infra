"""Personal references are excluded while shared documentation stays validated."""
import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import validate


class DocumentValidationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "infra"
        (self.root / "docs").mkdir(parents=True)
        (self.root / "docs/guide.md").write_text("# Guide\n")
        (self.root / "README.md").write_text(
            "[guide](docs/guide.md)\n"
            "[private](infrastructure-design-migration.html)\n"
            "[collection](local-reference/index.html)\n"
            "[servers](local-reference/servers/two-api-server-guide.html)\n"
            "[C](local-reference/servers/api-c-console-setup.html)\n"
            "[setup](local-reference/monitoring/cloudwatch-setup.html)\n"
            "[alerts](local-reference/monitoring/cloudwatch-alerts-guide.html)\n"
            "[restore](local-reference/database/rds-backup-restore-guide.html)\n"
        )

    def check_documents(self):
        with patch.object(validate, "ROOT", self.root), contextlib.redirect_stdout(io.StringIO()):
            validate.documents(Path(self.temp.name))

    def test_clean_checkout_without_private_files_passes(self):
        self.check_documents()

    def test_missing_tracked_file_still_fails(self):
        (self.root / "docs/guide.md").unlink()
        with self.assertRaisesRegex(ValueError, "docs/guide.md"):
            self.check_documents()

    def test_public_html_is_still_checked(self):
        (self.root / "docs/guide.html").write_text('<p id="duplicate"></p><p id="duplicate"></p>')
        with self.assertRaisesRegex(ValueError, "duplicate ID"):
            self.check_documents()

    def test_present_private_root_is_excluded(self):
        (self.root / "infrastructure-design-migration.html").write_text('<a href="missing.md">broken</a>')
        self.check_documents()

    def test_incomplete_private_collection_is_excluded(self):
        (self.root / "local-reference").mkdir()
        self.check_documents()

    def test_private_content_and_links_are_excluded(self):
        private = self.root / "local-reference"
        private.mkdir()
        (private / "README.md").write_text("[broken](missing.md)")
        (private / "guide.html").write_text('<p id="duplicate"></p><p id="duplicate"></p>')
        (self.root / "README.md").write_text("[personal](local-reference/unknown.html)")
        self.check_documents()


if __name__ == "__main__":
    unittest.main()
