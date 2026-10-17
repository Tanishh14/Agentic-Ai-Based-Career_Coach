"""accounts (name + password) and login sessions

Revision ID: 0002_accounts
Revises: 0001_initial
"""
import sqlalchemy as sa
from alembic import op

revision = "0002_accounts"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as b:
        b.add_column(sa.Column("username", sa.String(length=60), nullable=True))
        b.add_column(sa.Column("password_hash", sa.String(length=300), nullable=True))
        b.add_column(sa.Column("last_login_at", sa.String(length=40), nullable=True))
        b.create_unique_constraint("uq_users_username", ["username"])
    op.create_table(
        "user_sessions",
        sa.Column("token_hash", sa.String(length=64), primary_key=True),
        sa.Column("user_id", sa.String(length=32), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("expires_at", sa.String(length=40), nullable=False),
        sa.Column("last_seen_at", sa.String(length=40), nullable=True),
    )
    op.create_index("ix_session_user", "user_sessions", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_session_user", table_name="user_sessions")
    op.drop_table("user_sessions")
    with op.batch_alter_table("users") as b:
        b.drop_constraint("uq_users_username", type_="unique")
        b.drop_column("last_login_at")
        b.drop_column("password_hash")
        b.drop_column("username")
