"""Durable record-wise JSON transport staging; existing matching schema unchanged."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002_record_transport"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("record_transfers",
        sa.Column("submission_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("submissions.id"), primary_key=True),
        sa.Column("expected_count", sa.Integer(), nullable=False),
        sa.Column("filename", sa.String(120), nullable=False),
        sa.Column("token_version", sa.String(30), nullable=False),
        sa.Column("key_id", sa.String(120), nullable=False),
        sa.Column("method", sa.String(30), nullable=False))
    op.create_table("incoming_tokens",
        sa.Column("submission_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("record_transfers.submission_id"), primary_key=True),
        sa.Column("record_index", sa.Integer(), primary_key=True),
        sa.Column("patient_id", sa.String(200), nullable=False),
        sa.Column("token_position", sa.Integer(), nullable=False),
        sa.Column("value", sa.String(64), nullable=False))


def downgrade():
    op.drop_table("incoming_tokens")
    op.drop_table("record_transfers")
