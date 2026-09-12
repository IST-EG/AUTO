"""phase_7_1_auth_and_sessions

Revision ID: 9a1b2c3d4e5f
Revises: 7e6cba6d608e
Create Date: 2026-09-11 22:15:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9a1b2c3d4e5f'
down_revision: Union[str, None] = '7e6cba6d608e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create users table
    op.create_table(
        'users',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('username', sa.String(length=50), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=False),
        sa.Column('password_hash', sa.String(length=255), nullable=False),
        sa.Column('role', sa.String(length=20), nullable=False, server_default='OPERATOR'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_users_username', 'users', ['username'], unique=True)
    op.create_index('ix_users_email', 'users', ['email'], unique=True)

    # 2. Create user_sessions table
    op.create_table(
        'user_sessions',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.String(length=36), nullable=False),
        sa.Column('session_token_hash', sa.String(length=64), nullable=False),
        sa.Column('ip_address', sa.String(length=45), nullable=True),
        sa.Column('user_agent', sa.String(length=255), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_active_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_user_sessions_token', 'user_sessions', ['session_token_hash'], unique=True)
    op.create_index('ix_user_sessions_user_id', 'user_sessions', ['user_id'], unique=False)
    op.create_index('ix_user_sessions_expires_at', 'user_sessions', ['expires_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_user_sessions_expires_at', table_name='user_sessions')
    op.drop_index('ix_user_sessions_user_id', table_name='user_sessions')
    op.drop_index('ix_user_sessions_token', table_name='user_sessions')
    op.drop_table('user_sessions')

    op.drop_index('ix_users_email', table_name='users')
    op.drop_index('ix_users_username', table_name='users')
    op.drop_table('users')
