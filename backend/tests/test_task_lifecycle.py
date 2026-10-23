"""D58/D59/D60: 阶段任务对象级权限、状态机化发布/关闭、版本历史分页."""
import json
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessException, PermissionDeniedException
from app.core.permissions import CurrentUser
from app.db.base import Base
from app.models import all as _all_models  # noqa: F401
from app.models.defense import StageTask, StageTaskVersion
from app.modules.task.router import (
    TaskCreate,
    TaskPatch,
    close_task,
    create_task,
    publish_task,
    task_versions,
    update_task,
)
from app.shared import state_machine as sm

TEACHER_A = CurrentUser(id=11, username="teacher_a", real_name="教师A", role="TEACHER", profile_id=101)
TEACHER_B = CurrentUser(id=12, username="teacher_b", real_name="教师B", role="TEACHER", profile_id=102)
ADMIN = CurrentUser(id=1, username="admin", real_name="管理员", role="ADMIN", profile_id=None)


def _payload(**over) -> dict:
    data = dict(academic_year="2025-2026", stage="OPENING", title="开题报告任务", description="按时提交开题报告",
                requirements=["开题报告", "文献综述"], opens_at="2026-02-01T00:00:00",
                due_at="2026-03-01T18:00:00", late_policy="DENY")
    data.update(over)
    return data


def _create_body(**over) -> TaskCreate:
    return TaskCreate(**_payload(**over))


def _patch_body(**over) -> TaskPatch:
    return TaskPatch(**_payload(**over))


def _data(resp) -> dict:
    return json.loads(resp.body)["data"]


class StageTaskLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _create(self, user=TEACHER_A, **over) -> dict:
        return _data(create_task(_create_body(**over), user, self.db))

    # ---------------- D58 对象级权限 ----------------

    def test_teacher_cannot_update_other_teacher_task(self):
        task = self._create(TEACHER_A)
        with self.assertRaises(PermissionDeniedException) as ctx:
            update_task(task["id"], _patch_body(title="被别的老师改掉的标题"), TEACHER_B, self.db)
        self.assertEqual(ctx.exception.code, 40300)
        self.assertEqual(self.db.get(StageTask, task["id"]).title, "开题报告任务")
        self.assertEqual(
            self.db.query(StageTaskVersion).filter(StageTaskVersion.task_id == task["id"]).count(), 0,
            "越权请求不得写版本快照")

    def test_teacher_cannot_publish_or_close_other_teacher_task(self):
        task = self._create(TEACHER_A)
        for action in (publish_task, close_task):
            with self.assertRaises(PermissionDeniedException) as ctx:
                action(task["id"], TEACHER_B, self.db)
            self.assertEqual(ctx.exception.code, 40300)
        self.assertEqual(self.db.get(StageTask, task["id"]).status, "DRAFT")

    def test_owner_and_admin_can_update(self):
        task = self._create(TEACHER_A)
        _data(update_task(task["id"], _patch_body(title="教师A自己改标题"), TEACHER_A, self.db))
        self.assertEqual(self.db.get(StageTask, task["id"]).title, "教师A自己改标题")
        _data(update_task(task["id"], _patch_body(title="管理员改标题"), ADMIN, self.db))
        self.assertEqual(self.db.get(StageTask, task["id"]).title, "管理员改标题")

    # ---------------- D59 状态只能经状态机 ----------------

    def test_create_defaults_to_draft(self):
        task = self._create(TEACHER_A)
        self.assertEqual(task["status"], "DRAFT")
        self.assertEqual(task["allowed_actions"], ["publish"])

    def test_legacy_published_body_publishes_through_state_machine(self):
        with patch.object(sm, "transition", wraps=sm.transition) as tr:
            task = self._create(TEACHER_A, status="PUBLISHED")
        self.assertEqual(tr.call_count, 1, "PUBLISHED 必须经 state_machine.transition 达成, 不得直接赋值")
        self.assertEqual(task["status"], "PUBLISHED")
        self.assertEqual(task["allowed_actions"], ["close"])

    def test_update_ignores_status_field(self):
        task = self._create(TEACHER_A)
        updated = _data(update_task(task["id"], _patch_body(title="只改内容不改状态", status="PUBLISHED"),
                                    TEACHER_A, self.db))
        self.assertEqual(updated["status"], "DRAFT", "改内容接口不得顺带改状态")

    def test_publish_then_close_reaches_closed(self):
        task = self._create(TEACHER_A)
        published = _data(publish_task(task["id"], TEACHER_A, self.db))
        self.assertEqual(published["status"], "PUBLISHED")
        closed = _data(close_task(task["id"], TEACHER_A, self.db))
        self.assertEqual(closed["status"], "CLOSED", "CLOSED 必须是可达状态")
        self.assertEqual(closed["allowed_actions"], [])
        with self.assertRaises(BusinessException) as ctx:
            publish_task(task["id"], TEACHER_A, self.db)
        self.assertEqual(ctx.exception.code, 40003, "关闭后再发布应被状态机拒绝")

    def test_publish_twice_rejected(self):
        task = self._create(TEACHER_A, status="PUBLISHED")
        with self.assertRaises(BusinessException) as ctx:
            publish_task(task["id"], TEACHER_A, self.db)
        self.assertEqual(ctx.exception.code, 40003)

    def test_close_draft_rejected(self):
        task = self._create(TEACHER_A)
        with self.assertRaises(BusinessException) as ctx:
            close_task(task["id"], TEACHER_A, self.db)
        self.assertEqual(ctx.exception.code, 40003)

    # ---------------- D60 版本历史分页 ----------------

    def test_version_history_paginated(self):
        task = self._create(TEACHER_A)
        for i in range(3):
            _data(update_task(task["id"], _patch_body(title=f"第{i + 1}次修改后的标题"), TEACHER_A, self.db))
        first = _data(task_versions(task["id"], 1, 2, TEACHER_A, self.db))
        self.assertEqual(first["total"], 3)
        self.assertEqual(first["page"], 1)
        self.assertEqual(first["page_size"], 2)
        self.assertEqual(len(first["items"]), 2)
        second = _data(task_versions(task["id"], 2, 2, TEACHER_A, self.db))
        self.assertEqual(len(second["items"]), 1)
        self.assertEqual(second["total"], 3)
        self.assertEqual(second["items"][0]["version_no"], 1, "页序仍按 id 倒序(最新在前)")


if __name__ == "__main__":
    unittest.main()
