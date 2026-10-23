"""根据 StageConfig 批量生成 学生/教师/管理员 三端路由(roadmap 六十~六十二章 API 设计).

P1-A 改造点:

- 审核请求显式携带 `decision`(approve/reject/request_revision), 不再用 comment 是否为空推断(§4/D7);
- 列表关键字过滤下推 SQL: 由 `shared.query.paginate` 完成, total 与过滤结果口径一致(§9/D10);
- 审核读接口区分"可写视图"与"评委只读视图", 响应新增 `can_edit` / `is_examiner` 两个可选字段(§8)。
"""
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.permissions import CurrentUser, require_admin, require_student, require_teacher
from app.core.response import ResponseUtil
from app.db.mysql import get_db
from app.modules.stages.engine import StageConfig, StageEngine

REVIEW_DECISION = Literal["approve", "reject", "request_revision"]


class SaveRequest(BaseModel):
    content: dict[str, str] = Field(default_factory=dict, description="字段→内容")


class ReviewRequest(BaseModel):
    comment: str | None = Field(default=None, max_length=500)
    score: int | None = Field(default=None, ge=0, le=100, description="终期评分(已废弃, 仅兼容旧调用)")
    decision: REVIEW_DECISION | None = Field(
        default=None, description="审核结论: approve/reject/request_revision; 缺省时按端点语义取值")


def make_student_router(cfg: StageConfig, path: str) -> APIRouter:
    engine = StageEngine(cfg)
    router = APIRouter(prefix=f"/student/{path}", tags=[f"{cfg.label}-Student"])

    @router.get("")
    def view(user: CurrentUser = Depends(require_student), db: Session = Depends(get_db)):
        data = engine.student_view(db, user.profile_id)
        data["review_history"] = engine.review_history(db, user.profile_id)
        return ResponseUtil.success(data)

    @router.post("")
    def save(body: SaveRequest, user: CurrentUser = Depends(require_student), db: Session = Depends(get_db)):
        return ResponseUtil.success(engine.student_save(db, user.profile_id, body.content))

    @router.post("/submit")
    def submit(user: CurrentUser = Depends(require_student), db: Session = Depends(get_db)):
        return ResponseUtil.success(engine.student_submit(db, user.profile_id, user.id))

    @router.get("/versions")
    def versions(user: CurrentUser = Depends(require_student), db: Session = Depends(get_db)):
        return ResponseUtil.success(engine.student_versions(db, user.profile_id))

    return router


def make_teacher_router(cfg: StageConfig, path: str) -> APIRouter:
    engine = StageEngine(cfg)
    router = APIRouter(prefix=f"/teacher/{path}", tags=[f"{cfg.label}-Teacher"])

    @router.get("")
    def list_reports(status: str | None = None, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                     keyword: str | None = None,
                     user: CurrentUser = Depends(require_teacher), db: Session = Depends(get_db)):
        items, total = engine.teacher_list(db, user.profile_id, status, page, page_size, keyword)
        return ResponseUtil.page(items, total, page, page_size)

    @router.get("/{report_id}")
    def get_report(report_id: int, user: CurrentUser = Depends(require_teacher), db: Session = Depends(get_db)):
        view = engine.get_for_review(db, report_id, user.profile_id)
        item = engine._list_item(db, view.report)
        item["can_edit"] = view.can_edit
        item["is_examiner"] = view.is_examiner
        item["review_history"] = engine.review_history(db, view.report.student_id)
        return ResponseUtil.success(item)

    @router.post("/{report_id}/approve")
    def approve(report_id: int, body: ReviewRequest, user: CurrentUser = Depends(require_teacher),
                db: Session = Depends(get_db)):
        return ResponseUtil.success(
            engine.review(db, report_id, user.id, user.role, comment=body.comment,
                          teacher_profile_id=user.profile_id, score=body.score, decision="approve")
        )

    @router.post("/{report_id}/reject")
    def reject(report_id: int, body: ReviewRequest, user: CurrentUser = Depends(require_teacher),
               db: Session = Depends(get_db)):
        decision = "request_revision" if body.decision == "request_revision" else "reject"
        return ResponseUtil.success(
            engine.review(db, report_id, user.id, user.role, comment=body.comment,
                          teacher_profile_id=user.profile_id, decision=decision)
        )

    return router


def make_admin_router(cfg: StageConfig, path: str) -> APIRouter:
    engine = StageEngine(cfg)
    router = APIRouter(prefix=f"/admin/{path}", tags=[f"{cfg.label}-Admin"])

    @router.get("")
    def list_reports(status: str | None = None, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                     keyword: str | None = None,
                     _: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
        # 关键字过滤在 SQL 层完成, 先过滤后分页, total 与过滤口径一致(D10)
        items, total = engine.admin_list(db, status, page, page_size, keyword)
        return ResponseUtil.page(items, total, page, page_size)

    @router.get("/{report_id}")
    def get_report(report_id: int, _: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
        view = engine.get_for_review(db, report_id, None)
        item = engine._list_item(db, view.report)
        item["can_edit"] = view.can_edit
        item["is_examiner"] = view.is_examiner
        item["review_history"] = engine.review_history(db, view.report.student_id)
        return ResponseUtil.success(item)

    @router.post("/{report_id}/approve")
    def approve(report_id: int, body: ReviewRequest, user: CurrentUser = Depends(require_admin),
                db: Session = Depends(get_db)):
        return ResponseUtil.success(
            engine.review(db, report_id, user.id, user.role, comment=body.comment, decision="approve")
        )

    @router.post("/{report_id}/reject")
    def reject(report_id: int, body: ReviewRequest, user: CurrentUser = Depends(require_admin),
               db: Session = Depends(get_db)):
        decision = "request_revision" if body.decision == "request_revision" else "reject"
        return ResponseUtil.success(
            engine.review(db, report_id, user.id, user.role, comment=body.comment, decision=decision)
        )

    return router
