"""教师/管理员审核列表过滤回归用例(§9, 缺陷 D10/D33 的同类问题).

覆盖四个端点(两模块 × 教师/管理员), 全部要求"先过滤后分页, total 与过滤同口径":

- selection: teacher_selections / admin_selections
  时间语义列 = topic_selection.created_at(申请提交时间)
- material : teacher_materials / admin_materials
  时间语义列 = graduation_material.submitted_at(材料提交时间, 可空)

数据形态: 30 条记录, 第 i 条的学生姓名 学生{i:02d} / 学号 S{i:03d} / 提交时刻
2026-06-01 09:30 起每天一条; 第 22..30 号题目名以"重合"开头(恰好 9 条, 供翻页语义用例).
另有一条 2027 年的"别组"记录(teacher_id=2, 学生 别组学生/X001), 用于归属约束与
"日期窗口之外"的边界。
"""
import json
import unittest
from datetime import datetime as dt, timedelta

from sqlalchemy import BigInteger, Integer, create_engine
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessException
from app.core.permissions import CurrentUser
from app.core.response import http_status_for_code
from app.db.base import Base
from app.models import all as _all_models  # noqa: F401
from app.models.business import GraduationMaterial, GraduationTopic, TopicSelection
from app.models.user import StudentProfile, SysUser, TeacherProfile
from app.modules.material.router import admin_materials, teacher_materials
from app.modules.selection.router import admin_selections, teacher_selections

N = 30
BASE_TIME = dt(2026, 6, 1, 9, 30)
OUTSIDE_TIME = dt(2027, 1, 1, 9, 30)  # 落在所有被测日期窗口之外

TEACHER = CurrentUser(id=21, username="t1", real_name="导师甲", role="TEACHER", profile_id=1)
ADMIN = CurrentUser(id=99, username="admin", real_name="管理员", role="ADMIN", profile_id=None)


def new_session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    for table in Base.metadata.tables.values():
        for column in table.primary_key.columns:
            if isinstance(column.type, BigInteger):
                column.type = Integer()
    Base.metadata.create_all(engine)
    return Session(engine)


def day(index):
    """第 index 条记录(1 起)的提交时刻: 2026-06-01 09:30 起每天一条."""
    return BASE_TIME + timedelta(days=index - 1)


def seed(db):
    db.add_all([
        SysUser(id=21, username="t1", password_hash="x", real_name="导师甲", role="TEACHER"),
        SysUser(id=22, username="t2", password_hash="x", real_name="导师乙", role="TEACHER"),
        SysUser(id=99, username="admin", password_hash="x", real_name="管理员", role="ADMIN"),
        TeacherProfile(id=1, user_id=21, teacher_no="T001"),
        TeacherProfile(id=2, user_id=22, teacher_no="T002"),
    ])
    for i in range(1, N + 1):
        db.add(SysUser(id=100 + i, username="stu%02d" % i, password_hash="x",
                       real_name="学生%02d" % i, role="STUDENT"))
        db.add(StudentProfile(id=i, user_id=100 + i, student_no="S%03d" % i))
        title = "重合题目%02d" % i if i >= 22 else "选题题目%02d" % i
        db.add(GraduationTopic(id=i, teacher_id=1, title=title, status="PUBLISHED", max_students=5))
        db.add(TopicSelection(id=i, student_id=i, topic_id=i, teacher_id=1,
                              status="APPROVED" if i % 2 else "PENDING", active_flag=1,
                              created_at=day(i), updated_at=day(i)))
        db.add(GraduationMaterial(id=i, student_id=i, topic_id=i, teacher_id=1,
                                 status="APPROVED" if i % 2 else "SUBMITTED",
                                 submitted_at=day(i), reviewed_at=day(i)))
    # 归属约束 / 日期窗口外的记录: 别组指导教师(teacher_profile.id=2)的学生
    db.add(SysUser(id=131, username="other", password_hash="x", real_name="别组学生", role="STUDENT"))
    db.add(StudentProfile(id=31, user_id=131, student_no="X001"))
    db.add(GraduationTopic(id=31, teacher_id=2, title="别组题目", status="PUBLISHED", max_students=5))
    db.add(TopicSelection(id=31, student_id=31, topic_id=31, teacher_id=2, status="PENDING",
                          active_flag=1, created_at=OUTSIDE_TIME, updated_at=OUTSIDE_TIME))
    db.add(GraduationMaterial(id=31, student_id=31, topic_id=31, teacher_id=2, status="SUBMITTED",
                             submitted_at=OUTSIDE_TIME, reviewed_at=OUTSIDE_TIME))
    db.flush()


def _data(response):
    return json.loads(response.body)["data"]


def _ids(data):
    return [item["id"] for item in data["items"]]


def sel_teacher(db, **kw):
    params = dict(status=None, keyword=None, date_from=None, date_to=None,
                  page=1, page_size=20, user=TEACHER, db=db)
    params.update(kw)
    return _data(teacher_selections(**params))


def sel_admin(db, **kw):
    params = dict(status=None, keyword=None, date_from=None, date_to=None,
                  page=1, page_size=20, _=ADMIN, db=db)
    params.update(kw)
    return _data(admin_selections(**params))


def mat_teacher(db, **kw):
    params = dict(status=None, keyword=None, date_from=None, date_to=None,
                  page=1, page_size=20, user=TEACHER, db=db)
    params.update(kw)
    return _data(teacher_materials(**params))


def mat_admin(db, **kw):
    params = dict(status=None, keyword=None, date_from=None, date_to=None,
                  page=1, page_size=20, _=ADMIN, db=db)
    params.update(kw)
    return _data(admin_materials(academic_year=None, college_id=None, major_id=None,
                                 teacher_id=None, student=None, material_type=None,
                                 from_date=None, to_date=None, **params))


class SelectionFilterTest(unittest.TestCase):
    """验收 1-6: 选题审核列表(教师端/管理员端)."""

    # ---- 验收 1: 命中项刻意排在第 25 位(第 2 页之后), total 必须是 1 ----
    def test_keyword_hit_at_25th_row_returns_one_and_total_one_teacher(self):
        with new_session() as db:
            seed(db)
            data = sel_teacher(db, keyword="S025", page=1, page_size=5)
            print("[AC1][selection/teacher] keyword=S025 page=1&page_size=5 -> "
                  "total=%s items=%s" % (data["total"], _ids(data)))
            self.assertEqual(data["total"], 1)
            self.assertEqual(_ids(data), [25])
            data = sel_teacher(db, keyword="学生25", page=1, page_size=5)
            self.assertEqual((data["total"], _ids(data)), (1, [25]))

    def test_keyword_hit_at_25th_row_returns_one_and_total_one_admin(self):
        with new_session() as db:
            seed(db)
            data = sel_admin(db, keyword="S025", page=1, page_size=5)
            print("[AC1][selection/admin] keyword=S025 page=1&page_size=5 -> "
                  "total=%s items=%s" % (data["total"], _ids(data)))
            self.assertEqual((data["total"], _ids(data)), (1, [25]))
            # 题目名路径同样命中第 25 位之外的记录(第 22..30 号题目名以"重合"开头)
            data = sel_admin(db, keyword="重合题目29", page=1, page_size=5)
            self.assertEqual((data["total"], _ids(data)), (1, [29]))

    # ---- 验收 2: 不命中 ----
    def test_keyword_miss_returns_empty_and_total_zero(self):
        with new_session() as db:
            seed(db)
            for label, data in (("teacher", sel_teacher(db, keyword="查无此人")),
                                ("admin", sel_admin(db, keyword="查无此人"))):
                print("[AC2][selection/%s] keyword=查无此人 -> total=%s items=%s"
                      % (label, data["total"], data["items"]))
                self.assertEqual(data["total"], 0, label)
                self.assertEqual(data["items"], [], label)

    # ---- 验收 3: 日期边界四种情况 ----
    def test_date_boundaries_four_cases(self):
        with new_session() as db:
            seed(db)
            # 管理员端另有一条 2027-01-01 的别组记录: 它只在"无上界"的 from 窗口内,
            # 因此 from 的期望比教师端多 1, 其余三种情况两端一致。
            cases = [
                # (label, kwargs, 期望 teacher total, 期望 admin total)
                ("只给 from", dict(date_from="2026-06-20"), 11, 12),   # 6/20..6/30
                ("只给 to", dict(date_to="2026-06-10"), 10, 10),      # 6/1..6/10
                ("都给", dict(date_from="2026-06-10", date_to="2026-06-19"), 10, 10),  # 6/10..6/19
                ("都不给", dict(), 30, 31),
            ]
            for label, kwargs, expected_teacher, expected_admin in cases:
                teacher = sel_teacher(db, **kwargs)
                admin = sel_admin(db, **kwargs)
                print("[AC3][selection] %s %s -> teacher total=%s / admin total=%s"
                      % (label, kwargs, teacher["total"], admin["total"]))
                self.assertEqual(teacher["total"], expected_teacher, label)
                self.assertEqual(admin["total"], expected_admin, label)
            # 单边下界必须包含下界当日整天, 单边上界必须包含上界当日整天
            con = sel_teacher(db, date_from="2026-06-25")
            self.assertIn(25, _ids(con))
            con = sel_teacher(db, date_to="2026-06-25")
            self.assertIn(25, _ids(con))

    def test_invalid_date_returns_40002(self):
        with new_session() as db:
            seed(db)
            for kw in (dict(date_from="2026-13-01"), dict(date_to="不是日期"), dict(date_from="--")):
                for label, call in (("selection/teacher", sel_teacher), ("selection/admin", sel_admin)):
                    with self.assertRaises(BusinessException) as ctx:
                        call(db, **kw)
                    print("[AC3][%s] %s -> code=%s http=%s"
                          % (label, kw, ctx.exception.code, http_status_for_code(ctx.exception.code)))
                    self.assertEqual(ctx.exception.code, 40002)
            # 40002 与项目参数校验错误码一致, HTTP 层落到 400
            self.assertEqual(http_status_for_code(40002), 400)

    # ---- 验收 4: 三个条件同时生效取交集 ----
    def test_keyword_status_date_intersect(self):
        with new_session() as db:
            seed(db)
            # "重合"命中 22..30(9 条) ∩ APPROVED(奇数) ∩ >=2026-06-25 → 25/27/29
            kwargs = dict(keyword="重合", status="APPROVED", date_from="2026-06-25")
            for label, data in (("teacher", sel_teacher(db, **kwargs)),
                                ("admin", sel_admin(db, **kwargs))):
                print("[AC4][selection/%s] %s -> total=%s items=%s"
                      % (label, kwargs, data["total"], _ids(data)))
                self.assertEqual(data["total"], 3, label)
                self.assertEqual(_ids(data), [29, 27, 25], label)
            # 交集为空: 偶数状态 + 只取 25 以后 + 重合
            # 交集为空: 重合 ∩ 只取 2026-06-30 之后 → 仅剩 id30(偶数 = PENDING), 与 APPROVED 无交集
            empty = sel_admin(db, keyword="重合", status="APPROVED", date_from="2026-06-30")
            print("[AC4][selection/admin] 交集为空 -> total=%s items=%s"
                  % (empty["total"], empty["items"]))
            self.assertEqual((empty["total"], empty["items"]), (0, []))

    # ---- 验收 5: 教师归属约束不被 keyword 绕过 ----
    def test_keyword_cannot_bypass_teacher_scope(self):
        with new_session() as db:
            seed(db)
            teacher = sel_teacher(db, keyword="别组学生")
            admin = sel_admin(db, keyword="别组学生")
            print("[AC5][selection] keyword=别组学生 -> teacher total=%s / admin total=%s"
                  % (teacher["total"], admin["total"]))
            self.assertEqual((teacher["total"], teacher["items"]), (0, []))
            self.assertEqual((admin["total"], _ids(admin)), (1, [31]))
            # 别组教师的记录也不能出现在教师端无过滤列表里
            self.assertEqual(sel_teacher(db)["total"], 30)
            self.assertEqual(sel_admin(db)["total"], 31)

    # ---- 验收 6: 翻页语义不变(9 条命中, 第 2 页 5 条/页 → 4 条) ----
    def test_page2_total_is_full_match_count(self):
        with new_session() as db:
            seed(db)
            for label, data in (("teacher", sel_teacher(db, keyword="重合", page=2, page_size=5)),
                                ("admin", sel_admin(db, keyword="重合", page=2, page_size=5))):
                print("[AC6][selection/%s] keyword=重合 page=2&page_size=5 -> total=%s items=%s"
                      % (label, data["total"], _ids(data)))
                self.assertEqual(data["total"], 9, label)
                self.assertEqual(len(data["items"]), 4, label)
                self.assertEqual(data["page"], 2, label)
                self.assertEqual(data["page_size"], 5, label)


class MaterialFilterTest(unittest.TestCase):
    """验收 1-6: 材料审核列表(教师端/管理员端)."""

    def test_keyword_hit_at_25th_row_returns_one_and_total_one_teacher(self):
        with new_session() as db:
            seed(db)
            data = mat_teacher(db, keyword="S025", page=1, page_size=5)
            print("[AC1][material/teacher] keyword=S025 page=1&page_size=5 -> "
                  "total=%s items=%s" % (data["total"], _ids(data)))
            self.assertEqual(data["total"], 1)
            self.assertEqual(_ids(data), [25])
            data = mat_teacher(db, keyword="学生25", page=1, page_size=5)
            self.assertEqual((data["total"], _ids(data)), (1, [25]))

    def test_keyword_hit_at_25th_row_returns_one_and_total_one_admin(self):
        with new_session() as db:
            seed(db)
            data = mat_admin(db, keyword="S025", page=1, page_size=5)
            print("[AC1][material/admin] keyword=S025 page=1&page_size=5 -> "
                  "total=%s items=%s" % (data["total"], _ids(data)))
            self.assertEqual((data["total"], _ids(data)), (1, [25]))
            data = mat_admin(db, keyword="重合题目29", page=1, page_size=5)
            self.assertEqual((data["total"], _ids(data)), (1, [29]))

    def test_keyword_miss_returns_empty_and_total_zero(self):
        with new_session() as db:
            seed(db)
            for label, data in (("teacher", mat_teacher(db, keyword="查无此人")),
                                ("admin", mat_admin(db, keyword="查无此人"))):
                print("[AC2][material/%s] keyword=查无此人 -> total=%s items=%s"
                      % (label, data["total"], data["items"]))
                self.assertEqual(data["total"], 0, label)
                self.assertEqual(data["items"], [], label)

    def test_date_boundaries_four_cases(self):
        with new_session() as db:
            seed(db)
            # 同 selection: 管理员端多一条 2027-01-01 的别组记录, 只在"无上界"的 from 窗口内
            cases = [
                ("只给 from", dict(date_from="2026-06-20"), 11, 12),
                ("只给 to", dict(date_to="2026-06-10"), 10, 10),
                ("都给", dict(date_from="2026-06-10", date_to="2026-06-19"), 10, 10),
                ("都不给", dict(), 30, 31),
            ]
            for label, kwargs, expected_teacher, expected_admin in cases:
                teacher = mat_teacher(db, **kwargs)
                admin = mat_admin(db, **kwargs)
                print("[AC3][material] %s %s -> teacher total=%s / admin total=%s"
                      % (label, kwargs, teacher["total"], admin["total"]))
                self.assertEqual(teacher["total"], expected_teacher, label)
                self.assertEqual(admin["total"], expected_admin, label)
            con = mat_teacher(db, date_from="2026-06-25")
            self.assertIn(25, _ids(con))
            con = mat_teacher(db, date_to="2026-06-25")
            self.assertIn(25, _ids(con))

    def test_invalid_date_returns_40002(self):
        with new_session() as db:
            seed(db)
            for kw in (dict(date_from="2026-13-01"), dict(date_to="不是日期"), dict(date_from="--")):
                for label, call in (("material/teacher", mat_teacher), ("material/admin", mat_admin)):
                    with self.assertRaises(BusinessException) as ctx:
                        call(db, **kw)
                    print("[AC3][%s] %s -> code=%s" % (label, kw, ctx.exception.code))
                    self.assertEqual(ctx.exception.code, 40002)

    def test_keyword_status_date_intersect(self):
        with new_session() as db:
            seed(db)
            kwargs = dict(keyword="重合", status="APPROVED", date_from="2026-06-25")
            for label, data in (("teacher", mat_teacher(db, **kwargs)),
                                ("admin", mat_admin(db, **kwargs))):
                print("[AC4][material/%s] %s -> total=%s items=%s"
                      % (label, kwargs, data["total"], _ids(data)))
                self.assertEqual(data["total"], 3, label)
                self.assertEqual(_ids(data), [29, 27, 25], label)
            empty = mat_admin(db, keyword="重合", status="APPROVED", date_from="2026-06-30")
            print("[AC4][material/admin] 交集为空 -> total=%s items=%s"
                  % (empty["total"], empty["items"]))
            self.assertEqual((empty["total"], empty["items"]), (0, []))

    def test_keyword_cannot_bypass_teacher_scope(self):
        with new_session() as db:
            seed(db)
            teacher = mat_teacher(db, keyword="别组学生")
            admin = mat_admin(db, keyword="别组学生")
            print("[AC5][material] keyword=别组学生 -> teacher total=%s / admin total=%s"
                  % (teacher["total"], admin["total"]))
            self.assertEqual((teacher["total"], teacher["items"]), (0, []))
            self.assertEqual((admin["total"], _ids(admin)), (1, [31]))
            self.assertEqual(mat_teacher(db)["total"], 30)
            self.assertEqual(mat_admin(db)["total"], 31)

    def test_page2_total_is_full_match_count(self):
        with new_session() as db:
            seed(db)
            for label, data in (("teacher", mat_teacher(db, keyword="重合", page=2, page_size=5)),
                                ("admin", mat_admin(db, keyword="重合", page=2, page_size=5))):
                print("[AC6][material/%s] keyword=重合 page=2&page_size=5 -> total=%s items=%s"
                      % (label, data["total"], _ids(data)))
                self.assertEqual(data["total"], 9, label)
                self.assertEqual(len(data["items"]), 4, label)
                self.assertEqual(data["page"], 2, label)


if __name__ == "__main__":
    unittest.main()
