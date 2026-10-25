"""Validate single-container startup integrity and whole-application shutdown."""
import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from runtime import launcher


class RuntimeTests(unittest.TestCase):
    def test_shared_model_checked_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'model.gguf').write_bytes(b'fixture')
            check = root / 'checks.txt'
            check.write_text(f'7 {hashlib.sha256(b"fixture").hexdigest()} model.gguf\n')
            original = launcher.hashlib.file_digest
            with patch.object(launcher.hashlib, 'file_digest', wraps=original) as digest:
                launcher.verify_models([check, check], root)
                self.assertEqual(digest.call_count, 1)
            (root / 'model.gguf').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'checksum'):
                launcher.verify_models([check], root)

    def test_model_paths_and_sizes_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            check = root / 'checks.txt'
            check.write_text('1 ignored ../outside.gguf\n')
            with self.assertRaisesRegex(ValueError, 'outside'):
                launcher.verify_models([check], root)
            (root / 'model.gguf').write_bytes(b'fixture')
            check.write_text('1 ignored model.gguf\n')
            with self.assertRaisesRegex(ValueError, 'size'):
                launcher.verify_models([check], root)

    def observe_processes(self):
        children = []
        real_popen = subprocess.Popen
        def spawn(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            children.append(process)
            return process
        return children, patch.object(launcher.subprocess, 'Popen', side_effect=spawn)

    def test_backend_failure_stops_other_processes(self):
        children, capture = self.observe_processes()
        with capture:
            status = launcher.supervise({'peer': [sys.executable, '-c', 'import time; time.sleep(60)'],
                'failed': [sys.executable, '-c', 'raise SystemExit(7)']}, threading.Event(), grace=1)
        self.assertEqual(status, 7)
        self.assertTrue(all(child.poll() is not None for child in children))

    def test_unexpected_clean_exit_is_failure(self):
        self.assertEqual(launcher.supervise({'worker': [sys.executable, '-c', 'pass']}, threading.Event(), grace=1), 1)

    def test_shutdown_stops_and_reaps_every_child(self):
        stop = threading.Event()
        timer = threading.Timer(.3, stop.set)
        children, capture = self.observe_processes()
        timer.start()
        try:
            with capture:
                status = launcher.supervise({'one': [sys.executable, '-c', 'import time; time.sleep(60)'],
                    'two': [sys.executable, '-c', 'import time; time.sleep(60)']}, stop, grace=1)
        finally:
            timer.cancel()
        self.assertEqual(status, 0)
        self.assertTrue(all(child.poll() is not None for child in children))

    def test_failed_spawn_also_cleans_up_started_children(self):
        children, capture = self.observe_processes()
        with capture, self.assertRaises(FileNotFoundError):
            launcher.supervise({'started': [sys.executable, '-c', 'import time; time.sleep(60)'],
                                'missing': ['/missing/image-studio-program']}, threading.Event(), grace=1)
        self.assertTrue(all(child.poll() is not None for child in children))
