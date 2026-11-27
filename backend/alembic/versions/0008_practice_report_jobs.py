"""persist leases for asynchronous practice reports

Revision ID: 0008_practice_report_jobs
Revises: 0007_practice_observations
"""

import sqlalchemy as sa
from alembic import op

revision = "0008_practice_report_jobs"
down_revision = "0007_practice_observations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("ai_defense_session") as batch:
        batch.add_column(sa.Column("live_request_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("live_document_ids", sa.Text(), nullable=True))
        batch.add_column(sa.Column("report_status", sa.String(16), nullable=True))
        batch.add_column(sa.Column("report_lease_until", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("report_claim", sa.String(36), nullable=True))
        batch.add_column(sa.Column("report_attempts", sa.Integer(), nullable=False, server_default="0"))
        batch.create_unique_constraint("uq_defense_live_request", ["student_id", "live_request_id"])
        batch.create_index("ix_defense_report_due", ["report_status", "report_lease_until"])


def downgrade() -> None:
    with op.batch_alter_table("ai_defense_session") as batch:
        batch.drop_index("ix_defense_report_due")
        batch.drop_constraint("uq_defense_live_request", type_="unique")
        for name in ("report_attempts", "report_claim", "report_lease_until", "report_status",
                     "live_document_ids", "live_request_id"):
            batch.drop_column(name)
