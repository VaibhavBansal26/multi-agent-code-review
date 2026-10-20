"""Dashboard API router — read-only views + action endpoints for the MACRV dashboard."""
from __future__ import annotations

from pathlib import Path

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import Integer as Integer_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import Finding, PRReview, ReviewDecision, ReviewStatus, Severity
from app.db.session import get_db

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

_STATIC = Path(__file__).parent / "static"


# ---------------------------------------------------------------------------
# Serve SPA
# ---------------------------------------------------------------------------

@router.get("", include_in_schema=False)
@router.get("/", include_in_schema=False)
async def dashboard_root() -> FileResponse:
    return FileResponse(_STATIC / "index.html", media_type="text/html")


# ---------------------------------------------------------------------------
# Stats card
# ---------------------------------------------------------------------------

@router.get("/api/stats")
async def get_stats(db: AsyncSession = Depends(get_db)) -> JSONResponse:
    """Aggregate counts for the top stat cards."""
    total_reviews = (await db.execute(select(func.count(PRReview.id)))).scalar_one()
    pending = (await db.execute(
        select(func.count(PRReview.id)).where(PRReview.status == ReviewStatus.PENDING)
    )).scalar_one()
    in_progress = (await db.execute(
        select(func.count(PRReview.id)).where(PRReview.status == ReviewStatus.IN_PROGRESS)
    )).scalar_one()
    completed = (await db.execute(
        select(func.count(PRReview.id)).where(PRReview.status == ReviewStatus.COMPLETED)
    )).scalar_one()
    failed = (await db.execute(
        select(func.count(PRReview.id)).where(PRReview.status == ReviewStatus.FAILED)
    )).scalar_one()
    approved = (await db.execute(
        select(func.count(PRReview.id)).where(PRReview.decision == ReviewDecision.APPROVED)
    )).scalar_one()
    changes_requested = (await db.execute(
        select(func.count(PRReview.id)).where(PRReview.decision == ReviewDecision.CHANGES_REQUESTED)
    )).scalar_one()
    critical_findings = (await db.execute(
        select(func.count(Finding.id)).where(Finding.severity == Severity.CRITICAL)
    )).scalar_one()

    # Connected repos = distinct repo names that have at least one review
    repos_result = await db.execute(
        select(PRReview.repo_full_name).distinct()
    )
    connected_repos = [r[0] for r in repos_result.fetchall()]

    return JSONResponse({
        "total_reviews": total_reviews,
        "pending": pending,
        "in_progress": in_progress,
        "completed": completed,
        "failed": failed,
        "approved": approved,
        "changes_requested": changes_requested,
        "critical_findings": critical_findings,
        "connected_repos": connected_repos,
        "connected_repos_count": len(connected_repos),
    })


# ---------------------------------------------------------------------------
# Reviews list
# ---------------------------------------------------------------------------

@router.get("/api/reviews")
async def list_reviews(
    status: str | None = None,
    repo: str | None = None,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """List PR reviews with optional filters."""
    q = select(PRReview).options(selectinload(PRReview.findings)).order_by(
        PRReview.created_at.desc()
    )
    if status:
        try:
            q = q.where(PRReview.status == ReviewStatus(status))
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid status: {status}")
    if repo:
        q = q.where(PRReview.repo_full_name == repo)
    q = q.limit(limit).offset(offset)

    rows = (await db.execute(q)).scalars().all()
    return JSONResponse({"reviews": [_review_to_dict(r) for r in rows]})


@router.get("/api/reviews/{review_id}")
async def get_review(review_id: int, db: AsyncSession = Depends(get_db)) -> JSONResponse:
    row = (await db.execute(
        select(PRReview).options(selectinload(PRReview.findings))
        .where(PRReview.id == review_id)
    )).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Review not found")
    return JSONResponse(_review_to_dict(row, include_findings=True))


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

@router.post("/api/reviews/{review_id}/rescan", status_code=status.HTTP_202_ACCEPTED)
async def rescan_pr(review_id: int, db: AsyncSession = Depends(get_db)) -> JSONResponse:
    """Re-queue a completed/failed review for another pass."""
    row = (await db.execute(
        select(PRReview).where(PRReview.id == review_id)
    )).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Review not found")

    from app.queue.tasks import enqueue_pr_review

    task_id = enqueue_pr_review(
        repo_full_name=row.repo_full_name,
        pr_number=row.pr_number,
        head_sha=row.head_sha,
        pr_title=row.pr_title,
        author_login=row.author_login,
        author_email=row.author_email,
        diff_url=f"https://github.com/{row.repo_full_name}/pull/{row.pr_number}.diff",
        delivery_id=f"rescan-{review_id}",
    )
    log.info("dashboard_rescan", review_id=review_id, task_id=task_id)
    return JSONResponse({"task_id": task_id, "status": "queued"})


@router.post("/api/reviews/{review_id}/assign", status_code=status.HTTP_200_OK)
async def assign_human_review(
    review_id: int,
    body: dict,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Record a human reviewer assignment (stores as a log entry for now)."""
    assignee = body.get("assignee", "").strip()
    if not assignee:
        raise HTTPException(status_code=400, detail="assignee is required")
    row = (await db.execute(
        select(PRReview).where(PRReview.id == review_id)
    )).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Review not found")
    log.info(
        "dashboard_human_review_assigned",
        review_id=review_id,
        repo=row.repo_full_name,
        pr=row.pr_number,
        assignee=assignee,
    )
    return JSONResponse({
        "review_id": review_id,
        "repo": row.repo_full_name,
        "pr_number": row.pr_number,
        "assignee": assignee,
        "message": f"Human review assigned to @{assignee}",
    })


# ---------------------------------------------------------------------------
# Repos list
# ---------------------------------------------------------------------------

@router.get("/api/repos")
async def list_repos(db: AsyncSession = Depends(get_db)) -> JSONResponse:
    """Return all repos that have been reviewed, with summary counts."""
    rows = (await db.execute(
        select(
            PRReview.repo_full_name,
            func.count(PRReview.id).label("total"),
            func.sum(
                func.cast(PRReview.status == ReviewStatus.PENDING, Integer_)
            ).label("pending"),
            func.sum(
                func.cast(PRReview.decision == ReviewDecision.APPROVED, Integer_)
            ).label("approved"),
            func.sum(
                func.cast(PRReview.decision == ReviewDecision.CHANGES_REQUESTED, Integer_)
            ).label("changes_requested"),
        ).group_by(PRReview.repo_full_name).order_by(PRReview.repo_full_name)
    )).all()

    return JSONResponse({
        "repos": [
            {
                "repo": r.repo_full_name,
                "total": r.total,
                "pending": int(r.pending or 0),
                "approved": int(r.approved or 0),
                "changes_requested": int(r.changes_requested or 0),
            }
            for r in rows
        ]
    })


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------

@router.get("/api/findings")
async def list_findings(
    severity: str | None = None,
    repo: str | None = None,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    q = (
        select(Finding, PRReview.repo_full_name, PRReview.pr_number, PRReview.pr_title)
        .join(PRReview, Finding.review_id == PRReview.id)
        .order_by(Finding.created_at.desc())
    )
    if severity:
        try:
            q = q.where(Finding.severity == Severity(severity))
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid severity: {severity}")
    if repo:
        q = q.where(PRReview.repo_full_name == repo)
    q = q.limit(limit)

    rows = (await db.execute(q)).all()
    return JSONResponse({
        "findings": [
            {
                "id": f.id,
                "review_id": f.review_id,
                "repo": repo_name,
                "pr_number": pr_number,
                "pr_title": pr_title,
                "severity": f.severity,
                "title": f.title,
                "file_path": f.file_path,
                "line_start": f.line_start,
                "body": f.body,
                "source": f.source,
                "created_at": f.created_at.isoformat(),
            }
            for f, repo_name, pr_number, pr_title in rows
        ]
    })


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _review_to_dict(r: PRReview, include_findings: bool = False) -> dict:
    d = {
        "id": r.id,
        "repo": r.repo_full_name,
        "pr_number": r.pr_number,
        "pr_title": r.pr_title,
        "author": r.author_login,
        "status": r.status,
        "decision": r.decision,
        "files_reviewed": r.files_reviewed,
        "task_id": r.task_id,
        "created_at": r.created_at.isoformat(),
        "updated_at": r.updated_at.isoformat(),
        "critical": sum(1 for f in r.findings if f.severity == Severity.CRITICAL),
        "suggestive": sum(1 for f in r.findings if f.severity == Severity.SUGGESTIVE),
        "optimisation": sum(1 for f in r.findings if f.severity == Severity.OPTIMISATION),
    }
    if include_findings:
        d["findings"] = [
            {
                "id": f.id,
                "severity": f.severity,
                "title": f.title,
                "file_path": f.file_path,
                "line_start": f.line_start,
                "body": f.body,
                "source": f.source,
            }
            for f in r.findings
        ]
    return d
