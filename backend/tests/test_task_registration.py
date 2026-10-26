"""生产 Worker 独立启动也能识别索引和定时任务。"""
import subprocess
import sys
import unittest
from unittest.mock import patch

from app.tasks import dispatcher


class WorkerRegistrationTest(unittest.TestCase):
    def test_fresh_worker_loads_all_production_tasks(self):
        result = subprocess.run([sys.executable, "-c", """
from app.tasks.celery_app import celery_app
celery_app.loader.import_default_modules()
for name in ('tasks.index_document', 'app.tasks.risk_scan.daily_scan', 'app.tasks.file_gc.daily_gc'):
    assert name in celery_app.tasks, name
print('registered')
"""], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("registered", result.stdout)

    def test_queue_dispatch_uses_registered_task(self):
        with patch.object(dispatcher, "CELERY_ENABLED", True), \
             patch.object(dispatcher, "index_document_task") as task, \
             patch.object(dispatcher, "celery_app") as app:
            app.conf.task_always_eager = False
            self.assertIsNone(dispatcher.dispatch_index_task(42))
            task.delay.assert_called_once_with(42)
