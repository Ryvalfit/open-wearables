"""Join the remote-read fence and the deployed OW 0.10 migration chain."""

from collections.abc import Sequence

revision: str = "b83d731ac209"
down_revision: tuple[str, str] = ("f19e927b6d01", "c4e7a1d93b52")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
