import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.cv_manager import add_cv, is_allowed_cv_file  # noqa: E402
from src.database import Database  # noqa: E402

TEST_DB_PATH = os.path.join(os.path.dirname(__file__), ".tmp", "test_cv_validation.db")


class TestCvValidation(unittest.TestCase):
    def setUp(self):
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)
        self.db = Database(TEST_DB_PATH)
        self.tmp_dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_pdf_allowed(self):
        self.assertTrue(is_allowed_cv_file("resume.pdf"))

    def test_docx_allowed(self):
        self.assertTrue(is_allowed_cv_file("resume.docx"))

    def test_exe_rejected(self):
        self.assertFalse(is_allowed_cv_file("virus.exe"))

    def test_image_rejected(self):
        self.assertFalse(is_allowed_cv_file("photo.jpg"))

    def test_add_cv_rejects_bad_extension(self):
        bad_file = self.tmp_dir / "resume.exe"
        bad_file.write_text("not really a cv")
        with self.assertRaises(ValueError):
            add_cv(self.db, bad_file, "test", cv_dir=self.tmp_dir / "cv_store")

    def test_add_cv_accepts_pdf(self):
        good_file = self.tmp_dir / "resume.pdf"
        good_file.write_text("fake pdf content")
        cv_id = add_cv(self.db, good_file, "test", cv_dir=self.tmp_dir / "cv_store")
        self.assertIsInstance(cv_id, int)


if __name__ == "__main__":
    unittest.main()
