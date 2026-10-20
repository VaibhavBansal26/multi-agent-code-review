"""SQLAlchemy ORM models."""
from __future__ import annotations

import enum
from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class ReviewStatus(enum.StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class ReviewDecision(enum.StrEnum):
    APPROVED = "approved"
    CHANGES_REQUESTED = "changes_requested"
    COMMENTED = "commented"


class Severity(enum.StrEnum):
    CRITICAL = "critical"
    SUGGESTIVE = "suggestive"
    OPTIMISATION = "optimisation"


class PRReview(Base):
    __tablename__ = "pr_reviews"
    __table_args__ = (
        UniqueConstraint("repo_full_name", "pr_number", "head_sha", name="uq_review"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    repo_full_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    pr_number: Mapped[int] = mapped_column(Integer, nullable=False)
    head_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    pr_title: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    author_login: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    author_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[ReviewStatus] = mapped_column(
        Enum(ReviewStatus), nullable=False, default=ReviewStatus.PENDING
    )
    decision: Mapped[ReviewDecision | None] = mapped_column(Enum(ReviewDecision), nullable=True)
    task_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    files_reviewed: Mapped[int] = mapped_column(Integer, default=0)
    github_review_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    findings: Mapped[list[Finding]] = relationship(
        "Finding", back_populates="review", cascade="all, delete-orphan"
    )

    @property
    def is_terminal(self) -> bool:
        return self.status in (ReviewStatus.COMPLETED, ReviewStatus.FAILED)


class Finding(Base):
    __tablename__ = "findings"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    review_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("pr_reviews.id", ondelete="CASCADE"), nullable=False, index=True
    )
    severity: Mapped[Severity] = mapped_column(Enum(Severity), nullable=False, index=True)
    file_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    line_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    line_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    suggestion: Mapped[str | None] = mapped_column(Text, nullable=True)
    rule_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    source: Mapped[str] = mapped_column(String(50), default="llm")
    github_comment_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    review: Mapped[PRReview] = relationship("PRReview", back_populates="findings")


class ReviewReport(Base):
    __tablename__ = "review_reports"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    review_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("pr_reviews.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    markdown: Mapped[str] = mapped_column(Text, nullable=False)
    json_data: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    storage_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    review: Mapped[PRReview] = relationship("PRReview")
