"""initial schema

Revision ID: c461bdc781c1
Revises:
Create Date: 2026-10-21 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c461bdc781c1"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE TYPE IF NOT EXISTS reviewstatus AS ENUM ('pending', 'in_progress', 'completed', 'failed')")
    op.execute("CREATE TYPE IF NOT EXISTS reviewdecision AS ENUM ('approved', 'changes_requested', 'commented')")
    op.execute("CREATE TYPE IF NOT EXISTS severity AS ENUM ('critical', 'suggestive', 'optimisation')")

    op.create_table(
        "pr_reviews",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("repo_full_name", sa.String(length=255), nullable=False),
        sa.Column("pr_number", sa.Integer(), nullable=False),
        sa.Column("head_sha", sa.String(length=40), nullable=False),
        sa.Column("pr_title", sa.String(length=500), nullable=False),
        sa.Column("author_login", sa.String(length=100), nullable=False),
        sa.Column("author_email", sa.String(length=255), nullable=True),
        sa.Column("status", sa.Enum("pending", "in_progress", "completed", "failed", name="reviewstatus"), nullable=False),
        sa.Column("decision", sa.Enum("approved", "changes_requested", "commented", name="reviewdecision"), nullable=True),
        sa.Column("task_id", sa.String(length=255), nullable=True),
        sa.Column("files_reviewed", sa.Integer(), nullable=False),
        sa.Column("github_review_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("repo_full_name", "pr_number", "head_sha", name="uq_review"),
    )
    op.create_index(op.f("ix_pr_reviews_repo_full_name"), "pr_reviews", ["repo_full_name"])
    op.create_index(op.f("ix_pr_reviews_task_id"), "pr_reviews", ["task_id"])

    op.create_table(
        "findings",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("review_id", sa.BigInteger(), nullable=False),
        sa.Column("severity", sa.Enum("critical", "suggestive", "optimisation", name="severity"), nullable=False),
        sa.Column("file_path", sa.String(length=1000), nullable=False),
        sa.Column("line_start", sa.Integer(), nullable=True),
        sa.Column("line_end", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("suggestion", sa.Text(), nullable=True),
        sa.Column("rule_id", sa.String(length=200), nullable=True),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("github_comment_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["review_id"], ["pr_reviews.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_findings_review_id"), "findings", ["review_id"])
    op.create_index(op.f("ix_findings_severity"), "findings", ["severity"])

    op.create_table(
        "review_reports",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("review_id", sa.BigInteger(), nullable=False),
        sa.Column("markdown", sa.Text(), nullable=False),
        sa.Column("json_data", sa.Text(), nullable=False),
        sa.Column("storage_url", sa.String(length=1000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["review_id"], ["pr_reviews.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("review_id"),
    )


def downgrade() -> None:
    op.drop_table("review_reports")
    op.drop_table("findings")
    op.drop_table("pr_reviews")
    op.execute("DROP TYPE IF EXISTS severity")
    op.execute("DROP TYPE IF EXISTS reviewdecision")
    op.execute("DROP TYPE IF EXISTS reviewstatus")
