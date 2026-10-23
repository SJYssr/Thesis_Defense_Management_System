"""后台统计口径一致性回归: D57/D63/D64/D65/D66/D72.

每个用例都对着旧实现的反例写: 关键字 total、跨学年完成数、"已毕业"柱、
归档率分母去重、pass_counts 三元组、阶段计数 active_flag/cycle 口径。
"""
import json
import unittest
from datetime import datetime
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.permissions import CurrentUser
from app.db.base import Base
from app.models import all as _all_models  # noqa: F401
from app.models.business import (GraduationCycle, GraduationMaterial, GraduationProcess,
                                 GraduationSchedule, OpeningReport, TopicSelection)
from app.models.defense import ArchivePackage, DefenseResult
from app.models.user import StudentProfile, SysUser
from app.modules.dashboard.router import admin_analytics, admin_stats, defense_stats, home
from app.modules.progress.router import admin_progress_summary, teacher_students_progress

ADMIN = CurrentUser(id=1, username="admin", real_name="管理员", role="ADMIN", profile_id=None)
TEACHER = CurrentUser(id=2, username="t1", real_name="教师甲", role="TEACHER", profile_id=9)


def _engine():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def _data(response):
    return json.loads(response.body)["data"]


class D57KeywordPaginationTest(unittest.TestCase):
    """D57: 关键字必须在 SQL 层生效, total 与分页同源(旧实现取完页才内存过滤)."""

    def test_keyword_hits_record_outside_first_page(self):
        engine = _engine()
        with Session(engine) as db:
            db.add(GraduationSchedule(id=1, academic_year="2025-2026", is_active=1))
            for i in range(1, 31):
                db.add(SysUser(id=100 + i, username=f"stu{i}", password_hash="x", real_name=f"学生{i}", role="STUDENT"))
                db.add(StudentProfile(id=i, user_id=100 + i, student_no=f"2022{i:03d}"))
                db.add(GraduationCycle(id=i, academic_year="2025-2026", student_id=i, status="ACTIVE"))
                # progress_percent 升序排序 -> 命中记录(第 25 条)落在第 2 页
                db.add(GraduationProcess(id=200 + i, student_id=i, cycle_id=i, current_stage="MIDTERM",
                                         risk_level="NORMAL", progress_percent=i))
            db.commit()
            sids = list(range(1, 31))
            with patch("app.core.permissions.PermissionService.teacher_student_ids", return_value=sids):
                data = _data(teacher_students_progress(risk_level=None, stage=None, page=1, page_size=20,
                                                       keyword="学生25", user=TEACHER, db=db))
            self.assertEqual(data["total"], 1, "total 必须是筛选后的总数, 不是整页前总数")
            self.assertEqual(len(data["items"]), 1)
            self.assertEqual(data["items"][0]["student_no"], "2022025")
            # 未命中关键字: total 回落到 30, 证明两次 count 走同一条件集合
            with patch("app.core.permissions.PermissionService.teacher_student_ids", return_value=sids):
                data = _data(teacher_students_progress(risk_level=None, stage=None, page=1, page_size=20,
                                                       keyword="不存在的名字", user=TEACHER, db=db))
            self.assertEqual(data["total"], 0)
            self.assertEqual(data["items"], [])
        engine.dispose()


class D63CompletionProjectionTest(unittest.TestCase):
    """D63: 完成数只认 completed_at(progress.compute() 投影) + 当前学年 cycle."""

    def _fixture(self, db):
        db.add(GraduationSchedule(id=1, academic_year="2025-2026", is_active=1))
        for i in (1, 2, 3):
            db.add(SysUser(id=100 + i, username=f"stu{i}", password_hash="x", real_name=f"学生{i}", role="STUDENT"))
            db.add(StudentProfile(id=i, user_id=100 + i, student_no=f"2022{i:03d}"))
        # 旧学年: current_stage 是 COMPLETED 但 completed_at 为空 -> 陈旧字面值, 不算毕业
        db.add(GraduationCycle(id=11, academic_year="2024-2025", student_id=1, status="COMPLETED"))
        db.add(GraduationProcess(id=21, student_id=1, cycle_id=11, current_stage="COMPLETED",
                                 progress_percent=100, risk_level="NORMAL"))
        # 当前学年: completed_at 已落(compute() 的完成投影) -> 算毕业, 即使 current_stage 还是 FINAL
        for idx, sid in ((2, 12), (3, 13)):
            db.add(GraduationCycle(id=sid, academic_year="2025-2026", student_id=idx, status="ACTIVE"))
            db.add(GraduationProcess(id=100 + sid, student_id=idx, cycle_id=sid, current_stage="FINAL",
                                     progress_percent=95, risk_level="NORMAL", completed_at=datetime(2026, 6, 1, 10, 0, 0)))
        db.commit()

    def test_analytics_counts_completed_at_of_current_year_only(self):
        engine = _engine()
        with Session(engine) as db:
            self._fixture(db)
            data = _data(admin_analytics(user=ADMIN, db=db))
            self.assertEqual(data["cards"]["completed_count"], 2)

    def test_admin_home_card_uses_same_source(self):
        engine = _engine()
        with Session(engine) as db:
            self._fixture(db)
            data = _data(home(user=ADMIN, db=db))
            self.assertEqual(data["cards"]["completed_count"], 2)
        engine.dispose()


class D64GraduationBarTest(unittest.TestCase):
    """D64: "已毕业"柱与 D63 同源, 不再数材料审核通过."""

    def test_graduation_bar_matches_completed_count(self):
        engine = _engine()
        with Session(engine) as db:
            db.add(GraduationSchedule(id=1, academic_year="2025-2026", is_active=1))
            for i in (1, 2):
                db.add(SysUser(id=100 + i, username=f"stu{i}", password_hash="x", real_name=f"学生{i}", role="STUDENT"))
                db.add(StudentProfile(id=i, user_id=100 + i, student_no=f"2022{i:03d}"))
                db.add(GraduationCycle(id=10 + i, academic_year="2025-2026", student_id=i, status="ACTIVE"))
                db.add(GraduationProcess(id=20 + i, student_id=i, cycle_id=10 + i, current_stage="FINAL",
                                         progress_percent=95, risk_level="NORMAL",
                                         completed_at=datetime(2026, 6, 1, 10, 0, 0)))
            # 材料只有一份通过: 旧实现会把它当成"毕业 1 人"
            db.add(GraduationMaterial(id=1, student_id=1, cycle_id=11, topic_id=1, teacher_id=1, status="APPROVED"))
            db.commit()
            data = _data(admin_stats(user=ADMIN, db=db))
            bar = data["pass_counts"][-1]
            self.assertEqual(bar["name"], "已毕业")
            self.assertEqual(bar["value"], 2)
            self.assertEqual(bar["total"], 2)
        engine.dispose()


class D65ArchiveRateTest(unittest.TestCase):
    """D65: 分母按 cycle 去重, 一个 cycle 多条结论/多份材料不得把归档率压低或抬高."""

    def test_archive_rate_not_diluted_by_duplicate_cycles(self):
        engine = _engine()
        with Session(engine) as db:
            db.add(GraduationSchedule(id=1, academic_year="2025-2026", is_active=1))
            db.add(SysUser(id=101, username="stu1", password_hash="x", real_name="学生1", role="STUDENT"))
            db.add(StudentProfile(id=1, user_id=101, student_no="2022001"))
            db.add(GraduationCycle(id=11, academic_year="2025-2026", student_id=1, status="ACTIVE"))
            db.add(GraduationMaterial(id=1, student_id=1, cycle_id=11, topic_id=1, teacher_id=1, status="APPROVED"))
            for aid in (1, 2):
                db.add(DefenseResult(id=aid, assignment_id=aid, cycle_id=11, stage="FINAL", score=88,
                                     decision="PASS", status="PUBLISHED"))
            db.add(ArchivePackage(id=1, cycle_id=11, result_id=1, status="ARCHIVED"))
            db.commit()
            data = _data(defense_stats(user=ADMIN, db=db))
            self.assertEqual(data["archive_eligible"], 1, "同一 cycle 只算一个应归档单元")
            self.assertEqual(data["archive_archived"], 1)
            self.assertEqual(data["archive_rate"], 100.0)
        engine.dispose()


class D66StatsContractTest(unittest.TestCase):
    """D66: pass_counts 返回 name/value/total 三元组, 算而未用的总量进响应."""

    def test_pass_counts_triplets_and_totals(self):
        engine = _engine()
        with Session(engine) as db:
            db.add(GraduationSchedule(id=1, academic_year="2025-2026", is_active=1))
            for i in (1, 2, 3):
                db.add(SysUser(id=100 + i, username=f"stu{i}", password_hash="x", real_name=f"学生{i}", role="STUDENT"))
                db.add(StudentProfile(id=i, user_id=100 + i, student_no=f"2022{i:03d}"))
                db.add(GraduationCycle(id=10 + i, academic_year="2025-2026", student_id=i, status="ACTIVE"))
            db.add(TopicSelection(id=1, student_id=1, cycle_id=11, topic_id=1, teacher_id=1,
                                  status="APPROVED", active_flag=1))
            db.add(TopicSelection(id=2, student_id=2, cycle_id=12, topic_id=1, teacher_id=1,
                                  status="PENDING", active_flag=1))
            db.commit()
            data = _data(admin_stats(user=ADMIN, db=db))
            for row in data["pass_counts"]:
                self.assertEqual(set(row), {"name", "value", "total"}, "不得再返回裸计数冒充比率")
            self.assertEqual(data["student_total"], 3)
            self.assertEqual(data["selected_total"], 1)
        engine.dispose()


class D72SummaryStageScopeTest(unittest.TestCase):
    """D72: 阶段计数统一带 cycle 条件, 选题只认 active_flag == 1."""

    def _fixture(self, db):
        db.add(GraduationSchedule(id=1, academic_year="2025-2026", is_active=1))
        for i in (1, 2):
            db.add(SysUser(id=100 + i, username=f"stu{i}", password_hash="x", real_name=f"学生{i}", role="STUDENT"))
            db.add(StudentProfile(id=i, user_id=100 + i, student_no=f"2022{i:03d}"))
        db.add(GraduationCycle(id=11, academic_year="2025-2026", student_id=1, status="ACTIVE"))
        db.add(GraduationCycle(id=12, academic_year="2024-2025", student_id=2, status="COMPLETED"))
        db.add(TopicSelection(id=1, student_id=1, cycle_id=11, topic_id=1, teacher_id=1,
                              status="APPROVED", active_flag=1))
        db.add(TopicSelection(id=2, student_id=1, cycle_id=11, topic_id=1, teacher_id=1,
                              status="APPROVED", active_flag=0))  # 已撤销的旧选题
        db.add(TopicSelection(id=3, student_id=2, cycle_id=12, topic_id=1, teacher_id=1,
                              status="APPROVED", active_flag=1))  # 往届选题
        db.commit()

    def test_explicit_year_counts_active_selection_only(self):
        engine = _engine()
        with Session(engine) as db:
            self._fixture(db)
            data = _data(admin_progress_summary(academic_year="2025-2026", _=ADMIN, db=db))
            selection = next(s for s in data["stages"] if s["name"] == "选题")
            self.assertEqual(selection["value"], 1, "撤销选题(active_flag=0)与往届选题都不该计入")
        engine.dispose()

    def test_default_year_is_current_academic_year(self):
        engine = _engine()
        with Session(engine) as db:
            self._fixture(db)
            data = _data(admin_progress_summary(academic_year=None, _=ADMIN, db=db))
            selection = next(s for s in data["stages"] if s["name"] == "选题")
            self.assertEqual(selection["value"], 1)
            self.assertEqual(data["student_total"], 1, "缺省学年不再跨学年累计")
            self.assertEqual(data["process_total"], 0)
        engine.dispose()


class YearScopeConsistencyTest(unittest.TestCase):
    """D73: 同一响应内的进度类口径统一为当前学年, 并在 scope_map 里显式标注全量项.

    旧实现把 stage_distribution / risk_distribution / pass_rates / month_series /
    home risk_count 都按全学年统计, 而 completed_count 已是当前学年 —— 同一屏会出现
    "完成率 40% + 阶段分布 300 人"这种自相矛盾的数字。
    """

    def _fixture(self, db):
        db.add(GraduationSchedule(id=1, academic_year="2025-2026", is_active=1))
        # 往届 5 名学生各自一条高风险进度(GraduationProcess.cycle_id 唯一, 一 cycle 一条)
        for i in range(1, 6):
            db.add(SysUser(id=100 + i, username=f"prior{i}", password_hash="x", real_name=f"往届{i}", role="STUDENT"))
            db.add(StudentProfile(id=i, user_id=100 + i, student_no=f"2021{i:03d}"))
            db.add(GraduationCycle(id=10 + i, academic_year="2024-2025", student_id=i, status="ACTIVE"))
            db.add(GraduationProcess(id=200 + i, student_id=i, cycle_id=10 + i, current_stage="MIDTERM",
                                     progress_percent=10 + i, risk_level="HIGH"))
        # 本届 1 名高风险, 阶段 FINAL
        db.add(SysUser(id=106, username="now", password_hash="x", real_name="本届乙", role="STUDENT"))
        db.add(StudentProfile(id=6, user_id=106, student_no="2022006"))
        db.add(GraduationCycle(id=21, academic_year="2025-2026", student_id=6, status="ACTIVE"))
        db.add(GraduationProcess(id=210, student_id=6, cycle_id=21, current_stage="FINAL",
                                 progress_percent=88, risk_level="HIGH"))
        # 开题: 往届 1 份通过(本月提交) + 本届 1 份通过(本月提交)
        now = datetime.now()
        # 往届开题做成 REJECTED: 若漏了 cycle 过滤, 开题通过率会被拉成 50.0 且月份计数变 2
        db.add(OpeningReport(id=1, student_id=1, cycle_id=11, topic_id=11, teacher_id=9,
                             title="往届开题", status="REJECTED", submitted_at=now))
        db.add(OpeningReport(id=2, student_id=6, cycle_id=21, topic_id=21, teacher_id=9,
                             title="本届开题", status="APPROVED", submitted_at=now))
        # 教师(profile_id=9)本届指导 1 人
        db.add(TopicSelection(id=1, student_id=6, cycle_id=21, topic_id=21, teacher_id=9,
                              status="APPROVED", active_flag=1))
        db.commit()

    def _stats(self):
        engine = _engine()
        db = Session(engine)
        self._fixture(db)
        return engine, db

    def test_stats_risk_distribution_counts_current_year_only(self):
        engine, db = self._stats()
        with db:
            data = _data(admin_stats(user=ADMIN, db=db))
            high = [r for r in data["risk_distribution"] if r["name"] == "高风险"]
            self.assertEqual(high, [{"name": "高风险", "value": 1}],
                             "往届 5 条高风险不得算进当前学年风险分布")
            self.assertEqual(data["scope_map"]["risk_distribution"], "current_year")
        engine.dispose()

    def test_stats_stage_distribution_counts_current_year_only(self):
        engine, db = self._stats()
        with db:
            data = _data(admin_stats(user=ADMIN, db=db))
            dist = {row["name"]: row["value"] for row in data["stage_distribution"]}
            self.assertEqual(dist.get("终期材料"), 1)
            self.assertFalse(dist.get("中期材料"), "往届 5 条进度不得出现在当前学年阶段分布")
            self.assertEqual(data["scope_map"]["stage_distribution"], "current_year")
        engine.dispose()

    def test_analytics_pass_rates_and_month_series_current_year_only(self):
        engine, db = self._stats()
        with db:
            data = _data(admin_analytics(user=ADMIN, db=db))
            opening = [row for row in data["pass_rates"] if row["name"] == "开题"]
            self.assertEqual(opening[0]["value"], 100.0, "通过率只统计当前学年, 往届那份不得计入口径")
            series = {s["name"]: s["data"] for s in data["month_series"]}
            self.assertEqual(series["开题"][-1], 1, "月度提交只统计当前学年 cycle 的记录")
            self.assertEqual(data["scope_map"]["pass_rates"], "current_year")
            self.assertEqual(data["scope_map"]["colleges"], "all_years")
        engine.dispose()

    def test_home_risk_count_current_year_for_admin_and_teacher(self):
        engine, db = self._stats()
        with db:
            admin_data = _data(home(user=ADMIN, db=db))
            self.assertEqual(admin_data["cards"]["risk_count"], 1,
                             "后台首页风险卡片只算当前学年")
            teacher = CurrentUser(id=2, username="t1", real_name="教师甲", role="TEACHER", profile_id=9)
            teacher_data = _data(home(user=teacher, db=db))
            self.assertEqual(teacher_data["cards"]["risk_count"], 1,
                             "教师首页风险卡片同样只算当前学年")
            self.assertEqual(teacher_data["scope_map"]["cards.risk_count"], "current_year")
        engine.dispose()

    def test_every_payload_declares_scope(self):
        engine, db = self._stats()
        with db:
            cases = (("stats", _data(admin_stats(user=ADMIN, db=db)), "ai_usage"),
                     ("analytics", _data(admin_analytics(user=ADMIN, db=db)), "colleges"),
                     ("home", _data(home(user=ADMIN, db=db)), "cards.topic_count"))
            for name, payload, full_scope_key in cases:
                self.assertEqual(payload["scope"], "current_year", f"{name} 必须声明主口径")
                self.assertEqual(payload["academic_year"], "2025-2026", f"{name} 必须回显学年")
                self.assertIsInstance(payload["scope_map"], dict, f"{name} 必须逐字段标注口径")
                self.assertEqual(payload["scope_map"][full_scope_key], "all_years",
                                 f"{name} 的全量字段必须标注 all_years: {full_scope_key}")
        engine.dispose()


if __name__ == "__main__":
    unittest.main()
