"""Provider ingestion deletion fence."""
from alembic import op
import sqlalchemy as sa
revision = "f19e927b6d01"
down_revision = "c4e1a7b93d20"
branch_labels = None
depends_on = None
def upgrade() -> None:
    op.add_column("user_connection",sa.Column("ingest_generation",sa.Integer(),nullable=False,server_default="0"))
    op.add_column("user_connection",sa.Column("ingest_blocked_at",sa.DateTime(timezone=True),nullable=True))
def downgrade() -> None:
    op.drop_column("user_connection","ingest_blocked_at")
    op.drop_column("user_connection","ingest_generation")
