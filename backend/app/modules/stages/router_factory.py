"""根据 StageConfig 批量生成 学生/教师/管理员 三端路由(roadmap 六十~六十二章 API 设计)."""
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.exceptions import PermissionDeniedException
from app.core.permissions import CurrentUser, PermissionService, require_admin, require_student, require_teacher
from app.core.response import ResponseUtil
from app.db.mysql import get_db
from app.modules.stages.engine import StageConfig, StageEngine


class SaveRequest(BaseModel):
    content: dict[str, str] = Field(default_factory=dict, description="字段→内容")


class ReviewRequest(BaseModel):
    comment: str | None = Field(default=None, max_length=500)
    score: int | None = Field(default=None, ge=0, le=100, description="终期评分(可选)")


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
                     user: CurrentUser = Depends(require_teacher), db: Session = Depends(get_db)):
        items, total = engine.teacher_list(db, user.profile_id, status, page, page_size)
        return ResponseUtil.page(items, total, page, page_size)

    @router.get("/{report_id}")
    def get_report(report_id: int, user: CurrentUser = Depends(require_teacher), db: Session = Depends(get_db)):
        report = engine.get_for_review(db, report_id, user.profile_id)
        item = engine._list_item(db, report)
        item["review_history"] = engine.review_history(db, report.student_id)
        return ResponseUtil.success(item)

    @router.post("/{report_id}/approve")
    def approve(report_id: int, body: ReviewRequest, user: CurrentUser = Depends(require_teacher),
                db: Session = Depends(get_db)):
        return ResponseUtil.success(
            engine.review(db, report_id, user.id, user.role, True, body.comment, user.profile_id, body.score)
        )

    @router.post("/{report_id}/reject")
    def reject(report_id: int, body: ReviewRequest, user: CurrentUser = Depends(require_teacher),
               db: Session = Depends(get_db)):
        return ResponseUtil.success(
            engine.review(db, report_id, user.id, user.role, False, body.comment, user.profile_id, None)
        )

    return router


def make_admin_router(cfg: StageConfig, path: str) -> APIRouter:
    engine = StageEngine(cfg)
    router = APIRouter(prefix=f"/admin/{path}", tags=[f"{cfg.label}-Admin"])

    @router.get("")
    def list_reports(status: str | None = None, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                     keyword: str | None = None, _: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
        items, total = engine.admin_list(db, status, page, page_size)
        if keyword:
            items = [i for i in items if keyword in (i.get("student_name") or "") or keyword in (i.get("student_no") or "")]
        return ResponseUtil.page(items, total, page, page_size)

    @router.get("/{report_id}")
    def get_report(report_id: int, _: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
        report = engine.get_for_review(db, report_id, None)
        item = engine._list_item(db, report)
        item["review_history"] = engine.review_history(db, report.student_id)
        return ResponseUtil.success(item)

    @router.post("/{report_id}/approve")
    def approve(report_id: int, body: ReviewRequest, user: CurrentUser = Depends(require_admin),
                db: Session = Depends(get_db)):
        return ResponseUtil.success(engine.review(db, report_id, user.id, user.role, True, body.comment, None, body.score))

    @router.post("/{report_id}/reject")
    def reject(report_id: int, body: ReviewRequest, user: CurrentUser = Depends(require_admin),
               db: Session = Depends(get_db)):
        return ResponseUtil.success(engine.review(db, report_id, user.id, user.role, False, body.comment, None, None))

    return router
