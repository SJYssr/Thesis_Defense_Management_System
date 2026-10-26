"""评委文件权限绑定实际流程，公开下载必须来自已确认的归档。"""
import unittest
from datetime import datetime

from sqlalchemy.orm import Session

from app.core.exceptions import PermissionDeniedException
from app.models.business import GraduationCycle, GraduationMaterial, GraduationMaterialFile
from app.models.defense import ArchiveConsent, DefenseAssignment
from app.models.system import SysFile
from app.shared.fileperm import ensure_file_access
from tests.test_archive_access import (EXAMINER, OTHER_STUDENT, OWNER, SUPERVISOR,
                                       make_package, new_engine, seed)


class FileCyclePermissionTest(unittest.TestCase):
    def setUp(self):
        self.engine = new_engine()
        self.db = Session(self.engine)
        seed(self.db)
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_old_examiner_cannot_read_new_cycle_material(self):
        self.db.add(GraduationCycle(id=2, student_id=1, teacher_id=1, topic_id=2,
                                   academic_year="2027-2028", status="ACTIVE"))
        self.db.add(GraduationMaterial(id=2, student_id=1, cycle_id=2, teacher_id=1,
                                      topic_id=2, status="APPROVED"))
        file = SysFile(id=3, original_name="新学年论文.pdf", stored_name="new.pdf", size=10,
                       storage_path="new.pdf", uploader_id=OWNER.id)
        self.db.add(file)
        self.db.add(GraduationMaterialFile(material_id=2, file_id=3, material_type="THESIS"))
        self.db.commit()
        with self.assertRaises(PermissionDeniedException):
            ensure_file_access(self.db, EXAMINER, file)
        for user in (OWNER, SUPERVISOR):
            ensure_file_access(self.db, user, file)
        self.db.add(DefenseAssignment(session_id=1, cycle_id=2))
        self.db.commit()
        ensure_file_access(self.db, EXAMINER, file)

    def test_public_consent_does_not_expose_pending_or_revoked_archive(self):
        package = make_package(self.db, status="PENDING")
        self.db.add(ArchiveConsent(package_id=package.id, student_user_id=OWNER.id,
                                   visibility="FULL_TEXT", consented_at=datetime.now()))
        self.db.commit()
        file = self.db.get(SysFile, 1)
        for status in ("PENDING", "REVOKED"):
            package.status = status  # 夹具: 包含过往未清理授权的数据。
            self.db.commit()
            with self.assertRaises(PermissionDeniedException):
                ensure_file_access(self.db, OTHER_STUDENT, file)
        package.status = "ARCHIVED"
        self.db.commit()
        ensure_file_access(self.db, OTHER_STUDENT, file)
