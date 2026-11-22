"""practice observations

Revision ID: 0007_practice_observations
Revises: 0006_practice_cycle
Create Date: 2026-11-23 00:15:45.503379

"""
from alembic import op
import sqlalchemy as sa


revision = "0007_practice_observations"
down_revision = "0006_practice_cycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_defense_observation",
        sa.Column("session_id", sa.BigInteger(), nullable=False),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("no_face", sa.Integer(), nullable=False),
        sa.Column("multiple_faces", sa.Integer(), nullable=False),
        sa.Column("tab_hidden", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["ai_defense_session.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("session_id", "run_id"),
    )


def downgrade() -> None:
    op.drop_table("ai_defense_observation")
