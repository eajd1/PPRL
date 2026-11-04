"""Initial broker schema. SQL snapshot is independent of future ORM changes."""
from pathlib import Path
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    sql = Path(__file__).with_name("0001_schema.sql").read_text(encoding="utf-8")
    for statement in sql.split(";"):
        if statement.strip():
            op.execute(statement)


def downgrade():
    op.execute("ALTER TABLE hospital_records DROP CONSTRAINT fk_latest_version")
    op.execute("ALTER TABLE hospital_records DROP CONSTRAINT fk_matching_version")
    for table in ("audit_events", "review_cases", "match_assessments", "patient_memberships", "record_tokens",
                  "record_versions", "hospital_records", "global_patients", "submissions", "hospitals"):
        op.drop_table(table)
