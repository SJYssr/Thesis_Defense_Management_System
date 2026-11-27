"""生产 Worker 独立启动也能识别索引和定时任务。"""
import os
import subprocess
import sys
import unittest
from unittest.mock import patch

from app.tasks import dispatcher


class WorkerRegistrationTest(unittest.TestCase):
    def test_broker_and_results_use_configured_redis_password_and_databases(self):
        for password in ("p@ss:/%?#", ""):
            with self.subTest(protected=bool(password)):
                result = subprocess.run([sys.executable, "-c", """
import os
from app.tasks.celery_app import celery_app
broker = celery_app.connection_for_read()
backend = celery_app.backend.connparams
assert (broker.hostname, broker.port, broker.virtual_host) == ("127.0.0.1", 18183, "4")
assert (backend["host"], backend["port"], backend["db"]) == ("127.0.0.1", 18183, 5)
assert (broker.password or "") == (backend.get("password") or "") == os.environ["REDIS_PASSWORD"]
"""], env={**os.environ, "REDIS_HOST": "127.0.0.1", "REDIS_PORT": "18183", "REDIS_DB": "3",
           "REDIS_PASSWORD": password}, capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)

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
