"""初始化种子数据: 幂等, 重复执行会跳过(除非 --force).

演示账号:
  admin    / admin123    管理员
  teacher01/ 123456      教师 张伟
  teacher02/ 123456      教师 李娜
  20230001 / 123456      学生 王小明(演示主线: 已到毕业材料阶段, 知识库已建)
  20230002~20230006      其他各阶段学生
"""
import sys
from datetime import datetime, timedelta

sys.path.insert(0, ".")

from sqlalchemy import select

from app.core.security import hash_password
from app.db.base import Base
from app.db.mysql import SessionLocal, engine
from app.models.all import *  # noqa: F401,F403
from app.models.ai import AIDocument
from app.models.business import (
    FinalDefense,
    GraduationCycle,
    GraduationMaterial,
    GraduationMaterialFile,
    GraduationProcess,
    GraduationSchedule,
    GraduationTopic,
    MidtermReport,
    OpeningReport,
    TopicSelection,
)
from app.models.defense import (
    ArchiveFile,
    ArchivePackage,
    DefenseAssignment,
    DefenseExaminer,
    DefenseResult,
    DefenseSession,
    ExaminerScore,
    ReviewRubric,
    StageTask,
)
from app.models.enums import AIDocumentType
from app.models.system import Announcement, ReviewRecord, SysFile
from app.models.user import ClassInfo, College, Major, StudentProfile, SysUser, TeacherProfile
from app.shared.cycle import ensure_cycle
from app.shared.storage import storage

DEMO_THESIS = """# 基于LangChain与RAG的智能毕业设计管理系统设计与实现

## 第一章 绪论

### 1.1 研究背景
随着高校毕业设计管理规模的扩大, 传统的手工管理方式在流程跟踪、材料审核与质量把控方面效率低下。同时, 大语言模型技术为毕业设计的智能化辅导提供了新的可能。本课题设计并实现一个基于 LangChain 与检索增强生成(RAG)技术的智能毕业设计管理系统。

### 1.2 研究意义
系统将传统毕业设计业务流程管理与 AI 智能能力融合, 通过 RAG 技术让 AI 基于学生真实论文内容生成答辩问题与评价, 降低大模型幻觉, 提升毕业设计质量管理的智能化水平。

## 第二章 相关技术

### 2.1 FastAPI 与 SQLAlchemy
后端采用 Python FastAPI 框架, 配合 SQLAlchemy 2.x ORM 访问 MySQL 8 数据库。FastAPI 的依赖注入机制适合统一数据库会话、当前用户与权限校验。

### 2.2 Redis 向量检索
系统使用 Redis Search 的 Vector Field 与 HNSW 索引实现向量近似检索, 支持 KNN 查询与 TAG 元数据过滤, 同时承担缓存、JWT 会话与限流职能。相比引入独立的 Qdrant 或 Milvus, 减少了部署复杂度。

### 2.3 RAG 检索增强生成
RAG 流程包括: 文档解析(PyMuPDF/python-docx)、文本清洗、章节感知分块(500~900字符, overlap 120)、Embedding 向量化、向量检索、重排序与大模型生成。系统采用 Hybrid RAG: 向量检索与关键词检索通过 RRF(Reciprocal Rank Fusion)融合。

## 第三章 系统设计

### 3.1 系统架构
系统采用前后端分离架构: 前端 Vue3 + TypeScript + Element Plus, 后端 FastAPI, 数据层 MySQL + Redis, 异步任务 Celery。AI 层抽象为 LLMService、EmbeddingService、VectorStore 与 RAGService, 可切换 OpenAI/DeepSeek/智谱/Ollama 等模型供应商。

### 3.2 毕业设计状态机
系统核心是统一的状态机引擎: 学生流程为 选题→开题→中期→终期→毕业材料 五阶段, 每个阶段内部遵循 未提交→草稿→已提交→审核通过/驳回 的状态迁移。阶段前置校验在后端强制执行, 例如开题未通过时无法提交中期材料。

### 3.3 数据库设计
核心表包括: sys_user 用户表、graduation_topic 题目表、topic_selection 选题表、opening_report/midterm_report/final_defense 三阶段材料表、graduation_process 进度表、ai_document 与 ai_document_chunk 知识库表。选题表通过 UNIQUE(student_id, active_flag) 约束保证一个学生仅一条有效选题。

### 3.4 权限设计
采用 RBAC + 对象级数据权限: 管理员全局可见; 教师只能访问自己题目的选题与学生; 学生只能访问自己的材料。RAG 检索强制携带 student_id 过滤, 防止跨学生论文越权。

## 第四章 系统实现

### 4.1 文档入库流水线
学生上传论文后, 系统创建 ai_document 记录(index_status=WAITING), 由任务调度执行: 文件解析→文本清洗→章节识别→分块→Embedding→写入 Redis Vector→元数据落库, 完成后状态更新为 SUCCESS。

### 4.2 AI 模拟答辩
答辩问题按 基础理解(3)/技术选型(5)/系统设计(4)/实现细节(4)/创新(2)/不足(2)/扩展(2) 共约22题分维度生成, 每题携带论文依据 chunk。学生回答后, 系统检索论文原文进行四维评分: 知识准确性35分、逻辑完整性25分、表达清晰度20分、技术深度20分。回答模糊时触发动态追问。

### 4.3 进度风险分析
进度百分比由确定性规则引擎按阶段权重(选题10%/开题20%/中期25%/终期25%/材料20%)计算, AI 负责解释风险原因与生成教师提醒建议, 避免大模型随意编造进度。

## 第五章 系统测试

### 5.1 功能测试
对登录鉴权、题目发布、选题并发控制(名额不超卖)、阶段前置校验、数据权限隔离进行了接口测试, 全部通过。

### 5.2 检索质量测试
构建 50 个标准问题对 RAG 检索进行 Recall@5 与 MRR 评测, Hybrid RAG 相比纯向量检索 Recall@5 提升约 12%, 明显降低幻觉率。

## 第六章 总结与展望

本系统完成了毕业设计全流程管理与 RAG 智能能力的融合, 创新点包括: 基于RAG的个性化答辩问题生成、多轮动态追问机制、有依据的答辩评价、规则引擎与LLM结合的进度分析。不足在于语音答辩与真实PPT生成尚未实现, 后续可扩展多模态交互与自动化PPT导出。
"""


def seed(force: bool = False):
    db = SessionLocal()
    try:
        Base.metadata.create_all(bind=engine)
        existing = db.execute(select(SysUser).where(SysUser.username == "admin")).scalar_one_or_none()
        if existing and not force:
            print("种子数据已存在, 跳过。(删除数据后或 --force 重新执行)")
            return

        # 释放首个会话, 避免 --force 删表时被自身元数据锁阻塞
        db.rollback()
        db.close()

        if force:
            for table in reversed(Base.metadata.sorted_tables):
                table.drop(bind=engine, checkfirst=True)
            Base.metadata.create_all(bind=engine)
            db = SessionLocal()

        now = datetime.now()

        # ---------- 组织 ----------
        college = College(name="计算机学院", code="CS")
        db.add(college)
        db.flush()
        major = Major(college_id=college.id, name="软件工程", code="SE")
        major2 = Major(college_id=college.id, name="计算机科学与技术", code="CS")
        db.add_all([major, major2])
        db.flush()
        cls = ClassInfo(major_id=major.id, grade="2023", name="软件2301班")
        db.add(cls)
        db.flush()

        # ---------- 用户 ----------
        admin = SysUser(username="admin", password_hash=hash_password("admin123"),
                        real_name="系统管理员", role="ADMIN")
        db.add(admin)
        db.flush()
        db.add(__import__("app.models.user", fromlist=["AdminProfile"]).AdminProfile(
            user_id=admin.id, admin_no="A0001", department="教务处"))

        teachers = []
        for username, name, no, title, direction, mx in [
            ("teacher01", "张伟", "T2001", "教授", "大模型与应用", 6),
            ("teacher02", "李娜", "T2002", "副教授", "数据库与信息系统", 5),
        ]:
            u = SysUser(username=username, password_hash=hash_password("123456"),
                        real_name=name, role="TEACHER")
            db.add(u)
            db.flush()
            tp = TeacherProfile(user_id=u.id, teacher_no=no, title=title, department="计算机学院",
                                research_direction=direction, max_students=mx)
            db.add(tp)
            db.flush()
            teachers.append(tp)

        students = []
        for i in range(1, 8):
            u = SysUser(username=f"2023000{i}", password_hash=hash_password("123456"),
                        real_name=f"学生{i:02d}", role="STUDENT")
            db.add(u)
            db.flush()
            sp = StudentProfile(user_id=u.id, student_no=f"2023000{i}", college_id=college.id,
                                major_id=major.id if i % 2 else major2.id, class_id=cls.id, grade="2023")
            db.add(sp)
            db.flush()
            students.append(sp)

        # ---------- 学年时间配置(中期已过制造风险多样性; 选题窗口保持开放供演示/自测) ----------
        schedule = GraduationSchedule(
            academic_year="2025-2026", is_active=1,
            selection_start=now - timedelta(days=180), selection_end=now + timedelta(days=30),
            opening_start=now - timedelta(days=110), opening_end=now - timedelta(days=60),
            midterm_start=now - timedelta(days=50), midterm_end=now - timedelta(days=10),
            final_start=now - timedelta(days=5), final_end=now + timedelta(days=25),
            material_start=now + timedelta(days=10), material_end=now + timedelta(days=60),
        )
        db.add(schedule)

        # ---------- 公告 ----------
        db.add(Announcement(title="2026届毕业设计工作安排", content="请各位同学按时完成选题、开题、中期与终期各阶段任务, 逾期将影响毕业审核。",
                            priority="IMPORTANT", publisher_id=admin.id, publish_time=now))
        db.add(Announcement(title="AI模拟答辩功能上线", content="上传毕业论文后即可使用AI模拟答辩、答辩报告与PPT大纲生成功能。",
                            priority="NORMAL", publisher_id=admin.id, publish_time=now))

        # ---------- 题目 ----------
        topics = []
        for tp, title, desc, mx, mid in [
            (teachers[0], "基于LangChain与RAG的智能毕业设计管理系统设计与实现",
             "将RAG检索增强生成技术融入毕业设计管理流程, 实现AI模拟答辩、进度风险分析等智能能力。", 2, major.id),
            (teachers[0], "基于大语言模型的智能问答系统设计与实现",
             "研究RAG、多轮对话与Prompt工程, 构建可落地的领域问答系统。", 2, major.id),
            (teachers[1], "基于微服务的高校教务管理系统设计与实现",
             "采用前后端分离架构实现排课、选课与成绩管理。", 2, major2.id),
            (teachers[1], "基于协同过滤的校园图书推荐系统",
             "实现基于用户与物品协同过滤的推荐算法并进行效果评估。", 1, major2.id),
        ]:
            t = GraduationTopic(teacher_id=tp.id, title=title, description=desc,
                                requirements="熟练掌握 Web 开发与数据库设计; 了解大模型应用开发。",
                                topic_type="应用开发", difficulty="MEDIUM", major_id=mid,
                                max_students=mx, status="PUBLISHED", publish_time=now - timedelta(days=30))
            db.add(t)
            db.flush()
            topics.append(t)

        # ---------- 演示主线: 学生1 全流程到毕业材料 + 正式答辩结论 + 归档 ----------
        s1 = students[0]
        sel1 = TopicSelection(student_id=s1.id, topic_id=topics[0].id, teacher_id=teachers[0].id,
                              status="APPROVED", active_flag=1, student_reason="对大模型应用开发有浓厚兴趣, 希望深入学习RAG技术。",
                              review_comment="同意选题", review_time=now - timedelta(days=100))
        db.add(sel1)
        db.flush()
        db.add(ReviewRecord(business_type="TOPIC_SELECTION", business_id=sel1.id, student_id=s1.id,
                            action="SUBMIT", after_status="PENDING", created_at=now - timedelta(days=105)))
        cycle1 = ensure_cycle(db, s1.id, topics[0].id, teachers[0].id, academic_year="2025-2026")
        sel1.cycle_id = cycle1.id
        opening = OpeningReport(
            student_id=s1.id, cycle_id=cycle1.id, topic_id=topics[0].id, teacher_id=teachers[0].id,
            title=topics[0].title,
            research_background="传统毕业设计管理效率低, 缺乏智能化手段。",
            research_significance="将RAG融入业务系统, 提升管理智能化水平。",
            research_content="毕业流程管理+RAG知识库+AI模拟答辩。",
            research_method="文献研究法、原型法、实验对比法。",
            technical_route="Vue3+FastAPI+MySQL+Redis+LangChain。",
            schedule_plan="第1-2月调研, 第3-4月系统开发, 第5月测试, 第6月撰写论文。",
            references="[1] Lewis P, et al. Retrieval-Augmented Generation...[2] LangChain Documentation.",
            status="APPROVED", current_version=1, submitted_at=now - timedelta(days=80),
            reviewed_at=now - timedelta(days=78), review_comment="开题通过, 注意进度控制。")
        db.add(opening)
        midterm = MidtermReport(
            student_id=s1.id, cycle_id=cycle1.id, topic_id=topics[0].id, teacher_id=teachers[0].id,
            progress_description="已完成基础框架、题目选题与审核流程开发。",
            completed_content="用户权限、题目管理、选题审核、开题审核模块。",
            remaining_content="RAG知识库、AI模拟答辩、数据统计。",
            existing_problems="向量检索质量需要调优。",
            next_plan="完成RAG模块与AI答辩模块, 开展检索质量测试。",
            status="APPROVED", current_version=1, submitted_at=now - timedelta(days=30),
            reviewed_at=now - timedelta(days=28))
        db.add(midterm)
        final = FinalDefense(
            student_id=s1.id, cycle_id=cycle1.id, topic_id=topics[0].id, teacher_id=teachers[0].id,
            summary="完成了毕业设计全流程管理与RAG智能能力的融合系统。",
            innovation="基于RAG的个性化答辩问题生成与有依据评价。",
            technical_solution="FastAPI+SQLAlchemy+Redis Vector+Hybrid RAG。",
            test_result="功能测试与检索质量测试(Recall@5/MRR)均达标。",
            status="APPROVED", score=88, current_version=1,
            submitted_at=now - timedelta(days=3), reviewed_at=now - timedelta(days=1))
        db.add(final)

        # 正式终期答辩: 已发布场次 + 双评委评分 + 已发布结论(与材料审核严格分离)
        final_session = DefenseSession(
            academic_year="2025-2026", stage="FINAL", title="终期答辩第1场(软件工程)",
            starts_at=now - timedelta(days=2), ends_at=now - timedelta(days=2) + timedelta(hours=3),
            location="教学楼A-301", capacity=8, status="PUBLISHED", revision=1)
        db.add(final_session)
        db.flush()
        db.add(DefenseExaminer(session_id=final_session.id, teacher_id=teachers[0].id, examiner_role="CHAIR"))
        db.add(DefenseExaminer(session_id=final_session.id, teacher_id=teachers[1].id, examiner_role="EXAMINER"))
        seat1 = DefenseAssignment(session_id=final_session.id, cycle_id=cycle1.id,
                                  seat_status="ATTENDED", acknowledged_at=now - timedelta(days=3))
        db.add(seat1)
        db.flush()
        db.add(ExaminerScore(assignment_id=seat1.id, examiner_id=teachers[0].id, rubric_version=1,
                             scores_json='{"报告质量": 90, "系统实现": 88, "答辩表现": 86}',
                             comment="工作量饱满, 系统完成度高, 创新点阐述清晰。",
                             status="SUBMITTED", version=1, submitted_at=now - timedelta(days=1)))
        db.add(ExaminerScore(assignment_id=seat1.id, examiner_id=teachers[1].id, rubric_version=1,
                             scores_json='{"报告质量": 87, "系统实现": 85, "答辩表现": 84}',
                             comment="RAG流程设计合理, 建议补充对比实验细节。",
                             status="SUBMITTED", version=1, submitted_at=now - timedelta(days=1)))
        result1 = DefenseResult(
            assignment_id=seat1.id, cycle_id=cycle1.id, stage="FINAL",
            score=87, decision="PASS", status="PUBLISHED",
            rubric_snapshot='{"version": 1, "weights": [{"name": "报告质量", "weight": 30}, {"name": "系统实现", "weight": 40}, {"name": "答辩表现", "weight": 30}], "pass_line": 60, "min_examiners": 2}',
            score_detail_json='[{"examiner_id": %d, "version": 1, "scores": {"报告质量": 90, "系统实现": 88, "答辩表现": 86}}, {"examiner_id": %d, "version": 1, "scores": {"报告质量": 87, "系统实现": 85, "答辩表现": 84}}]' % (teachers[0].id, teachers[1].id),
            published_at=now - timedelta(hours=12), published_by=admin.id)
        db.add(result1)
        db.flush()

        # 毕业材料审核通过 + 归档包(含真实文件快照)
        demo_thesis_file = storage.save(DEMO_THESIS.encode("utf-8"), "基于LangChain与RAG的智能毕业设计管理系统-终稿.md")
        thesis_file = SysFile(original_name="基于LangChain与RAG的智能毕业设计管理系统-终稿.md",
                              stored_name=demo_thesis_file[0], extension="md", mime_type="text/markdown",
                              size=len(DEMO_THESIS.encode("utf-8")), storage_type="LOCAL",
                              storage_path=demo_thesis_file[1],
                              sha256=__import__("hashlib").sha256(DEMO_THESIS.encode("utf-8")).hexdigest(),
                              uploader_id=admin.id)
        db.add(thesis_file)
        db.flush()
        material1 = GraduationMaterial(student_id=s1.id, cycle_id=cycle1.id, topic_id=topics[0].id,
                                       teacher_id=teachers[0].id, status="APPROVED",
                                       submitted_at=now - timedelta(hours=6), reviewed_at=now - timedelta(hours=1),
                                       review_comment="材料齐全规范")
        db.add(material1)
        db.flush()
        db.add(GraduationMaterialFile(material_id=material1.id, file_id=thesis_file.id, material_type="THESIS"))
        db.add(GraduationProcess(student_id=s1.id, cycle_id=cycle1.id, topic_id=topics[0].id, teacher_id=teachers[0].id,
                                 current_stage="COMPLETED", progress_percent=100, risk_level="NORMAL",
                                 started_at=now - timedelta(days=100), completed_at=now - timedelta(hours=1)))
        pkg = ArchivePackage(cycle_id=cycle1.id, result_id=result1.id,
                             title="基于LangChain与RAG的智能毕业设计管理系统设计与实现",
                             abstract="将RAG检索增强生成技术融入毕业设计管理流程, 实现AI模拟答辩、进度风险分析等智能能力。",
                             keywords="RAG;LangChain;FastAPI;Vue3",
                             status="ARCHIVED", archived_at=now - timedelta(minutes=30), created_by=admin.id)
        db.add(pkg)
        db.flush()
        db.add(ArchiveFile(package_id=pkg.id, file_id=thesis_file.id, file_role="THESIS", version=1,
                           sha256=__import__("hashlib").sha256(DEMO_THESIS.encode("utf-8")).hexdigest()))
        db.add(StageTask(academic_year="2025-2026", stage="MATERIAL", title="提交毕业材料",
                         description="上传毕业论文终稿、源代码与说明文档, 提交后由指导教师审核。",
                         requirements_json='["毕业论文(THESIS)", "源代码(SOURCE_CODE)", "说明文档(MANUAL)"]',
                         opens_at=now - timedelta(days=7), due_at=now + timedelta(days=14),
                         late_policy="ALLOW", status="PUBLISHED", created_by=admin.id, current_version=1))
        db.add(StageTask(academic_year="2025-2026", stage="FINAL", title="终期答辩材料提交",
                         description="提交终期总结、创新点与技术方案, 并准备现场答辩。",
                         requirements_json='["终期答辩材料"]',
                         opens_at=now - timedelta(days=20), due_at=now - timedelta(days=3),
                         late_policy="DENY", status="PUBLISHED", created_by=admin.id, current_version=1))

        # 其他学生各阶段分布(供 Dashboard 演示)
        stage_setup = [
            (students[1], topics[0], "APPROVED", "OPENING", 10),      # 已选题待开题
            (students[2], topics[1], "APPROVED", "MIDTERM", 30),      # 开题通过
            (students[3], topics[2], "APPROVED", "MIDTERM", 30),      # 开题通过(教师2)
            (students[4], topics[2], "PENDING", "SELECTION", 0),      # 待审核
        ]
        cycles_by_student = {}
        for s, t, sel_status, stage, pct in stage_setup:
            sel = TopicSelection(student_id=s.id, topic_id=t.id, teacher_id=t.teacher_id,
                                 status=sel_status, active_flag=1,
                                 student_reason="对该题目方向感兴趣, 具备相关基础。")
            db.add(sel)
            db.flush()
            if sel_status == "APPROVED":
                cycle = ensure_cycle(db, s.id, t.id, t.teacher_id, academic_year="2025-2026")
                sel.cycle_id = cycle.id
                cycles_by_student[s.id] = cycle
                db.add(GraduationProcess(student_id=s.id, cycle_id=cycle.id, topic_id=t.id, teacher_id=t.teacher_id,
                                         current_stage=stage, progress_percent=pct, risk_level="NORMAL",
                                         started_at=now - timedelta(days=60)))

        # 中期答辩场次: 已发布待评审(教师1主席), 学生3/4(开题已通过)已排期待答辩
        midterm_session = DefenseSession(
            academic_year="2025-2026", stage="MIDTERM", title="中期答辩第1场",
            starts_at=now + timedelta(days=5), ends_at=now + timedelta(days=5, hours=3),
            location="教学楼B-205", capacity=10, status="PUBLISHED", revision=1)
        db.add(midterm_session)
        db.flush()
        db.add(DefenseExaminer(session_id=midterm_session.id, teacher_id=teachers[0].id, examiner_role="CHAIR"))
        db.add(DefenseExaminer(session_id=midterm_session.id, teacher_id=teachers[1].id, examiner_role="EXAMINER"))
        for sid in (students[2].id, students[3].id):
            if sid in cycles_by_student:
                db.add(DefenseAssignment(session_id=midterm_session.id, cycle_id=cycles_by_student[sid].id,
                                         seat_status="SCHEDULED"))

        # 草稿场次(管理员排期演示, 未发布)
        db.add(DefenseSession(academic_year="2025-2026", stage="FINAL", title="终期答辩第2场(草稿)",
                              starts_at=now + timedelta(days=15), ends_at=now + timedelta(days=15, hours=3),
                              location="教学楼A-302", capacity=6, status="DRAFT"))

        # 评分表配置(启用并冻结, 供评分/汇总复算)
        db.add(ReviewRubric(academic_year="2025-2026", stage="FINAL", college_id=None, version=1,
                            min_examiners=2, pass_line=60,
                            weights_json='[{"name": "报告质量", "weight": 30}, {"name": "系统实现", "weight": 40}, {"name": "答辩表现", "weight": 30}]',
                            enabled=1, frozen_at=now - timedelta(days=30)))
        # 学生5 未选题
        # 学生6/7 无选题

        db.commit()

        # ---------- 演示论文入知识库并索引 ----------
        doc = AIDocument(student_id=s1.id, topic_id=topics[0].id, document_type=AIDocumentType.THESIS.value,
                         document_name="基于LangChain与RAG的智能毕业设计管理系统(演示论文).md",
                         scope="STUDENT", index_status="WAITING", content_text=DEMO_THESIS)
        db.add(doc)
        db.commit()

        from app.ai.rag.rag_service import RAGService

        result = RAGService(db).index_document(doc.id)
        print("演示论文索引完成:", result)

        print("种子数据完成。")
        print("  管理员: admin / admin123")
        print("  教师:   teacher01 / 123456, teacher02 / 123456")
        print("  学生:   20230001~20230007 / 123456 (20230001 为主演示账号, 已内置论文知识库)")
    finally:
        db.close()


if __name__ == "__main__":
    seed(force="--force" in sys.argv)
