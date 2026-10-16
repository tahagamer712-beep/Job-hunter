import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.log_rotation import rotate_all_logs, rotate_file  # noqa: E402


class TestLogRotation(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_small_file_not_rotated(self):
        log_file = self.tmp_dir / "small.log"
        log_file.write_text("just a little log data")
        rotated = rotate_file(log_file, max_bytes=1000)
        self.assertFalse(rotated)
        self.assertGreater(log_file.stat().st_size, 0)

    def test_large_file_is_rotated_and_truncated(self):
        log_file = self.tmp_dir / "big.log"
        log_file.write_text("x" * 2000)
        rotated = rotate_file(log_file, max_bytes=1000)
        self.assertTrue(rotated)
        self.assertEqual(log_file.stat().st_size, 0, "Original file must be truncated, not deleted")
        gz_files = list(self.tmp_dir.glob("big.log.*.gz"))
        self.assertEqual(len(gz_files), 1)

    def test_old_rotations_pruned_beyond_keep_limit(self):
        log_file = self.tmp_dir / "big.log"
        for i in range(5):
            log_file.write_text("x" * 2000)
            rotate_file(log_file, max_bytes=1000, keep=2)
        gz_files = list(self.tmp_dir.glob("big.log.*.gz"))
        self.assertLessEqual(len(gz_files), 2)

    def test_rotate_all_logs_processes_directory(self):
        (self.tmp_dir / "a.log").write_text("x" * 2000)
        (self.tmp_dir / "b.log").write_text("small")
        rotated = rotate_all_logs(log_dir=self.tmp_dir, max_bytes=1000)
        self.assertEqual(rotated, [str(self.tmp_dir / "a.log")])

    def test_missing_file_returns_false(self):
        self.assertFalse(rotate_file(self.tmp_dir / "does_not_exist.log"))


if __name__ == "__main__":
    unittest.main()
