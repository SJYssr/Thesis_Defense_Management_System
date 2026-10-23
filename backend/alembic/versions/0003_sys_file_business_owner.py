"""sys_file_business_owner

D70: 给 sys_file 补业务归属列, 让"上传后从未被任何业务记录引用"的孤儿文件可被定位清理。
此前 /files/upload 只把 business_type/business_id 写进日志与响应体, 库里没有落点。

Revision ID: 0003_sys_file_business_owner
Revises: 0002_user_token_version
Create Date: 2026-10-23 12:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0003_sys_file_business_owner'
down_revision: Union[str, Sequence[str], None] = '0002_user_token_version'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('sys_file', sa.Column('business_type', sa.String(length=32), nullable=True, comment='业务归属类型, 如 MATERIAL/ARCHIVE'))
    op.add_column('sys_file', sa.Column('business_id', sa.BigInteger(), nullable=True, comment='业务归属主键'))
    op.create_index('ix_sys_file_business_type', 'sys_file', ['business_type'])
    op.create_index('ix_sys_file_business_id', 'sys_file', ['business_id'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_sys_file_business_id', table_name='sys_file')
    op.drop_index('ix_sys_file_business_type', table_name='sys_file')
    op.drop_column('sys_file', 'business_id')
    op.drop_column('sys_file', 'business_type')
