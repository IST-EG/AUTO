"""phase_7_3_templates

Revision ID: a1b2c3d4e5f6
Revises: 9a1b2c3d4e5f
Create Date: 2026-09-11 23:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '9a1b2c3d4e5f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create message_templates table
    op.create_table(
        'message_templates',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name', name='uq_message_template_name')
    )

    # 2. Create message_template_versions table
    op.create_table(
        'message_template_versions',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('template_id', sa.Integer(), nullable=False),
        sa.Column('version_number', sa.Integer(), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('created_by', sa.String(length=100), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['template_id'], ['message_templates.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('template_id', 'version_number', name='uq_template_version')
    )
    op.create_index('idx_template_version_lookup', 'message_template_versions', ['template_id', 'version_number'], unique=False)

    # 3. Add template_version_id to campaigns
    with op.batch_alter_table('campaigns') as batch_op:
        batch_op.add_column(sa.Column('template_version_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            'fk_campaigns_template_version_id',
            'message_template_versions',
            ['template_version_id'],
            ['id'],
            ondelete='SET NULL'
        )


def downgrade() -> None:
    with op.batch_alter_table('campaigns') as batch_op:
        batch_op.drop_constraint('fk_campaigns_template_version_id', type_='foreignkey')
        batch_op.drop_column('template_version_id')

    op.drop_index('idx_template_version_lookup', table_name='message_template_versions')
    op.drop_table('message_template_versions')
    op.drop_table('message_templates')
