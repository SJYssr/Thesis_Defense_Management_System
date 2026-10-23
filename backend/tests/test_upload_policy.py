"""上传策略单源化: 扩展名白名单 / 两路径同源上限 / 材料上传配额与业务归属.

修复前的实际故障:
- 后端 ALLOWED_EXTENSIONS 没有 .mp4 等媒体格式, 而前端"演示视频"的 accept 写死 .mp4
  -> 该类型永远传不上去(实测 {"code":40010,"message":"不支持的文件类型: .mp4"});
- material 上传调 read_upload(file) 不传上限 -> 无视后台系统设置里的 max_upload_size_mb;
- material 上传完全不校验日配额, 是绕过 /files/upload 配额的口子;
- material 写 SysFile 缺 storage_type 与显式 created_at(配额统计时区错位);
- 前端把 business_type/business_id 塞进 FormData 而后端不接收, sys_file 也没有这两列。
"""
import json
import shutil
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from fastapi import UploadFile
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from starlette.datastructures import Headers

from app.core.exceptions import FileException
from app.core.permissions import CurrentUser
from app.db.base import Base
from app.models import all as _all_models  # noqa: F401
from app.models.business import GraduationMaterial, GraduationMaterialFile
from app.models.system import OperationLog, SysFile, SystemConfig
from app.shared.storage import (
    ALLOWED_EXTENSIONS,
    ARCHIVE_EXTENSIONS,
    DOCUMENT_EXTENSIONS,
    IMAGE_EXTENSIONS,
    MEDIA_EXTENSIONS,
    LocalStorage,
    is_allowed_extension,
    pathlib_suffix,
    reject_unsupported,
)

STUDENT = CurrentUser(id=21, username="stu_01", real_name="学生一", role="STUDENT", profile_id=201)
KB = 1024


class AppClocks:
    """sqlite 下 BigInteger 主键不自增, 统一显式分配."""

    @staticmethod
    def attach(session):
        @event.listens_for(session, "before_flush")
        def assign_ids(sess, *_):
            next_id = getattr(assign_ids, "next_id", 900)
            for item in sess.new:
                if isinstance(item, (SysFile, OperationLog, GraduationMaterialFile)) and item.id is None:
                    next_id += 1
                    item.id = next_id
            assign_ids.next_id = next_id


class WhitelistTest(unittest.TestCase):
    """白名单必须覆盖前端声明的全部类型, 同时挡住可渲染/可执行类型."""

    def test_media_formats_are_allowed(self):
        for name in ("demo.mp4", "take.MOV", "voice.mp3", "clip.m4v", "sound.wav", "a.aac"):
            self.assertTrue(is_allowed_extension(name), f"{name} 应允许上传(演示视频/音频)")

    def test_archive_and_image_formats_are_allowed(self):
        for name in ("src.zip", "src.rar", "src.7z", "src.tar", "src.gz", "shot.webp", "shot.gif", "shot.bmp"):
            self.assertTrue(is_allowed_extension(name), f"{name} 应允许上传")

    def test_document_formats_are_allowed(self):
        for name in ("paper.pdf", "paper.docx", "paper.ppt", "note.md", "note.txt"):
            self.assertTrue(is_allowed_extension(name))

    def test_executable_and_renderable_types_rejected(self):
        for name in ("x.svg", "x.html", "x.js", "x.exe", "x.sh", "x.bat", "x.php", "x.jsp"):
            self.assertFalse(is_allowed_extension(name), f"{name} 不得进入白名单")

    def test_extension_check_is_case_insensitive(self):
        self.assertEqual(pathlib_suffix("A.MP4"), ".mp4")
        self.assertTrue(is_allowed_extension("A.MP4"))

    def test_reject_unsupported_message_lists_allowed_types(self):
        with self.assertRaises(FileException) as ctx:
            reject_unsupported("x.mp4.exe")
        self.assertEqual(ctx.exception.code, 40010)
        self.assertIn("不支持的文件类型", ctx.exception.message)
        self.assertIn(".mp4", ctx.exception.message, "提示要带上允许的类型, 否则用户无从修正")

    def test_file_without_extension_rejected(self):
        with self.assertRaises(FileException) as ctx:
            reject_unsupported("noext")
        self.assertIn("(无扩展名)", ctx.exception.message)

    def test_kind_groups_are_disjoint_and_union_matches_whitelist(self):
        groups = [DOCUMENT_EXTENSIONS, ARCHIVE_EXTENSIONS, IMAGE_EXTENSIONS, MEDIA_EXTENSIONS]
        union = frozenset().union(*groups)
        self.assertEqual(union, ALLOWED_EXTENSIONS, "白名单必须等于四个分组的并集")
        for i, left in enumerate(groups):
            for right in groups[i + 1:]:
                self.assertFalse(left & right, "同一扩展名不应落在两个分组里")


class _NoCacheRedis:
    """断掉配置缓存, 让 get_int 每次落到本次测试的 sqlite 配置行."""

    def get(self, *args, **kwargs):
        return None

    def setex(self, *args, **kwargs):
        return True

    def delete(self, *args, **kwargs):
        return 0


class PolicyEndpointTest(unittest.TestCase):
    """GET /files/upload-policy: 前端 accept 与上传前校验的唯一来源."""

    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.db.close)
        patcher = patch("app.shared.sysconfig.redis_client", _NoCacheRedis())
        patcher.start()
        self.addCleanup(patcher.stop)
        self.db.add_all([SystemConfig(key="max_upload_size_mb", value="7"),
                         SystemConfig(key="max_upload_daily_mb", value="123")])
        self.db.commit()

    def test_policy_shape_and_values(self):
        from app.modules.file.router import get_upload_policy

        body = json.loads(get_upload_policy(user=STUDENT, db=self.db).body)
        data = body["data"]
        self.assertEqual(set(data), {"allowed_extensions", "by_kind",
                                     "max_upload_size_mb", "max_upload_daily_mb"})
        self.assertEqual(set(data["by_kind"]), {"DOCUMENT", "ARCHIVE", "IMAGE", "MEDIA"})
        self.assertIn(".mp4", data["allowed_extensions"])
        self.assertNotIn(".svg", data["allowed_extensions"])
        self.assertEqual(data["max_upload_size_mb"], 7, "上限必须来自系统配置, 不能是环境变量默认值")
        self.assertEqual(data["max_upload_daily_mb"], 123)
        self.assertEqual(set(data["allowed_extensions"]),
                         set().union(*data["by_kind"].values()))


class SharedLimitTest(unittest.TestCase):
    """/files/upload 与 /student/material/files 的单文件上限必须同源."""

    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        AppClocks.attach(self.db)
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.db.close)
        for target in ("app.modules.file.router.storage", "app.modules.material.router.storage"):
            patcher = patch(target, LocalStorage(Path(self.tmpdir) / "uploads"))
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch("app.shared.sysconfig.redis_client", _NoCacheRedis())
        patcher.start()
        self.addCleanup(patcher.stop)
        # 系统配置 2MB, 环境变量默认 50MB: 只有真读配置时 3MB 才会被拒
        self.db.add_all([SystemConfig(key="max_upload_size_mb", value="2"),
                         SystemConfig(key="max_upload_daily_mb", value="100")])
        self.db.commit()

    def _upload(self, size, name="demo.mp4"):
        spool = tempfile.SpooledTemporaryFile(max_size=16 * 1024 * 1024)
        spool.write(b"x" * size)
        spool.seek(0)
        self.addCleanup(spool.close)
        return UploadFile(file=spool, filename=name,
                          headers=Headers({"content-type": "application/octet-stream"}))

    def test_file_upload_uses_configured_limit(self):
        from app.modules.file.router import upload_file

        with self.assertRaises(FileException) as ctx:
            upload_file(self._upload(3 * 1024 * KB), business_type=None, business_id=None,
                        user=STUDENT, db=self.db)
        self.assertIn("2MB", ctx.exception.message, "应为系统配置的 2MB, 不是默认 50MB")

    def test_material_upload_uses_same_configured_limit(self):
        from app.modules.material.router import upload_material_file

        with patch("app.modules.material.router.GraduationWorkflowService.require_can_submit_material"),              patch("app.modules.material.router._get_or_create", return_value=self._material()):
            with self.assertRaises(FileException) as ctx:
                upload_material_file(self._upload(3 * 1024 * KB), material_type="VIDEO",
                                     business_type=None, business_id=None, user=STUDENT, db=self.db)
        self.assertIn("2MB", ctx.exception.message, "材料上传也必须读同一份系统配置")

    def _material(self):
        m = GraduationMaterial(id=1, student_id=201, cycle_id=1, topic_id=5, teacher_id=101,
                               status="NOT_SUBMITTED")
        self.db.add(m)
        self.db.commit()
        return m

    def test_material_upload_accepts_media_extension(self):
        """修复前 .mp4 被后端白名单拒绝, "演示视频"整类材料传不上去."""
        from app.modules.material.router import upload_material_file

        with patch("app.modules.material.router.GraduationWorkflowService.require_can_submit_material"),              patch("app.modules.material.router._get_or_create", return_value=self._material()):
            resp = upload_material_file(self._upload(600 * KB), material_type="VIDEO",
                                        business_type=None, business_id=None, user=STUDENT, db=self.db)
        data = json.loads(resp.body)["data"]
        self.assertEqual(len(data["files"]), 1)
        self.assertEqual(data["files"][0]["original_name"], "demo.mp4")

    def test_material_upload_records_business_and_storage_fields(self):
        from app.modules.material.router import upload_material_file

        material = self._material()
        with patch("app.modules.material.router.GraduationWorkflowService.require_can_submit_material"),              patch("app.modules.material.router._get_or_create", return_value=material):
            upload_material_file(self._upload(600 * KB), material_type="VIDEO",
                                 business_type="MATERIAL", business_id=None, user=STUDENT, db=self.db)
        row = self.db.query(SysFile).filter(SysFile.uploader_id == STUDENT.id).one()
        self.assertEqual(row.business_type, "MATERIAL")
        self.assertEqual(row.business_id, material.id, "未显式给 business_id 时回填材料 id")
        self.assertEqual(row.storage_type, "LOCAL")
        self.assertIsNotNone(row.created_at, "必须显式写应用时钟, 否则日配额跨时区漏计")
        self.assertEqual(row.extension, "mp4")

    def test_material_upload_explicit_business_id_wins(self):
        from app.modules.material.router import upload_material_file

        with patch("app.modules.material.router.GraduationWorkflowService.require_can_submit_material"),              patch("app.modules.material.router._get_or_create", return_value=self._material()):
            upload_material_file(self._upload(600 * KB), material_type="VIDEO",
                                 business_type="MATERIAL", business_id=77, user=STUDENT, db=self.db)
        row = self.db.query(SysFile).filter(SysFile.uploader_id == STUDENT.id).one()
        self.assertEqual(row.business_id, 77)

    def test_material_upload_over_quota_leaves_no_side_effect(self):
        """配额校验必须先于状态迁移: 否则被拒的上传会留下已迁移的草稿状态."""
        from app.modules.material.router import upload_material_file

        self.db.query(SystemConfig).filter(SystemConfig.key == "max_upload_daily_mb").update({"value": "1"})
        self.db.commit()
        material = self._material()
        with patch("app.modules.material.router.GraduationWorkflowService.require_can_submit_material"),              patch("app.modules.material.router._get_or_create", return_value=material):
            with self.assertRaises(FileException) as ctx:
                upload_material_file(self._upload(2 * 1024 * KB), material_type="VIDEO",
                                     business_type=None, business_id=None, user=STUDENT, db=self.db)
        self.assertEqual(ctx.exception.code, 40013)
        self.assertEqual(self.db.query(SysFile).count(), 0, "被拒的上传不得落库")
        self.db.refresh(material)
        self.assertEqual(material.status, "NOT_SUBMITTED", "配额失败不得把材料推进到 DRAFT")


if __name__ == "__main__":
    unittest.main()
