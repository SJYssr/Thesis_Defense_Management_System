"""Administrator material filters and cards reflect stored records."""
import json
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.permissions import CurrentUser
from app.db.base import Base
from app.models import all as _all_models  # noqa: F401
from app.models.business import GraduationCycle, GraduationMaterial, GraduationMaterialFile
from app.models.system import SysFile
from app.models.user import StudentProfile, SysUser
from app.modules.material.router import admin_material_stats, admin_materials


class AdminMaterialsTest(unittest.TestCase):
    def test_filters_and_counts(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        with Session(engine) as db:
            db.add_all([
                SysUser(id=11, username="s1", password_hash="x", real_name="学生甲", role="STUDENT"),
                SysUser(id=12, username="s2", password_hash="x", real_name="学生乙", role="STUDENT"),
                StudentProfile(id=21, user_id=11, student_no="001"),
                StudentProfile(id=22, user_id=12, student_no="002"),
                GraduationCycle(id=31, academic_year="2025-2026", student_id=21),
                GraduationCycle(id=32, academic_year="2024-2025", student_id=22),
                GraduationMaterial(id=41, student_id=21, cycle_id=31, topic_id=1, teacher_id=1,
                                   status="SUBMITTED"),
                GraduationMaterial(id=42, student_id=22, cycle_id=32, topic_id=2, teacher_id=2,
                                   status="APPROVED"),
                SysFile(id=51, original_name="论文.pdf", stored_name="saved.pdf", storage_path="saved.pdf"),
                GraduationMaterialFile(id=61, material_id=41, file_id=51, material_type="THESIS"),
            ])
            db.commit()
            admin = CurrentUser(id=1, username="admin", real_name="管理员", role="ADMIN", profile_id=None)
            response = admin_materials(status="SUBMITTED", academic_year="2025-2026", student="001",
                                       college_id=None, major_id=None, teacher_id=None, material_type=None,
                                       from_date=None, to_date=None, page=1, page_size=20, _=admin, db=db)
            data = json.loads(response.body)["data"]
            self.assertEqual(data["total"], 1)
            self.assertEqual(data["items"][0]["student_name"], "学生甲")
            self.assertEqual(data["items"][0]["academic_year"], "2025-2026")
            self.assertEqual(data["items"][0]["files"][0]["original_name"], "论文.pdf")
            response = admin_materials(status="REJECTED,APPROVED", academic_year=None, student=None,
                                       college_id=None, major_id=None, teacher_id=None, material_type="THESIS",
                                       from_date=None, to_date=None, page=1, page_size=20, _=admin, db=db)
            self.assertEqual(json.loads(response.body)["data"]["total"], 0)
            stats = json.loads(admin_material_stats(academic_year="2025-2026", _=admin, db=db).body)["data"]
            self.assertEqual(stats["pending"], 1)
            self.assertEqual(stats["approved"], 0)
        engine.dispose()
