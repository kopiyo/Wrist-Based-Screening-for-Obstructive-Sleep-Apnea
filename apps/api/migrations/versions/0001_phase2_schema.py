"""Create Phase 2 account, consent, history, audit and alert tables."""
from alembic import op

from apps.api.models import Base

revision = "0001_phase2_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
