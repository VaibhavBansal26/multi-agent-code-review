"""add connected_repos table

Revision ID: a3f2e1d4b5c6
Revises: c461bdc781c1
Create Date: 2026-10-21 00:01:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a3f2e1d4b5c6"
down_revision: str | None = "c461bdc781c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "connected_repos",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("repo_full_name", sa.String(length=255), nullable=False),
        sa.Column("hook_id", sa.BigInteger(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("repo_full_name"),
    )
    op.create_index(op.f("ix_connected_repos_repo_full_name"), "connected_repos", ["repo_full_name"])


def downgrade() -> None:
    op.drop_index(op.f("ix_connected_repos_repo_full_name"), table_name="connected_repos")
    op.drop_table("connected_repos")
