"""archive_package_owner

P9-A: 给 archive_package 补归属列(student_id / teacher_id)。
此前归档包只有 cycle_id, 状态机 _assert_permission 对 STUDENT 用 getattr(obj, "student_id", None)
做归属校验 —— 该列不存在时拿到 None 即"不校验", 学生 A 可 submit 学生 B 的 PENDING 归档包。
补列后同一校验自动生效, 无需再改状态机。

已有数据按 cycle_id 反查 graduation_cycle 回填, 回填完成后才把 student_id 置 NOT NULL。

Revision ID: 0004_archive_package_owner
Revises: 0003_sys_file_business_owner
Create Date: 2026-10-24 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0004_archive_package_owner'
down_revision: Union[str, Sequence[str], None] = '0003_sys_file_business_owner'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('archive_package', sa.Column('student_id', sa.BigInteger(), nullable=True,
                                               comment='归属学生 student_profile.id'))
    op.add_column('archive_package', sa.Column('teacher_id', sa.BigInteger(), nullable=True,
                                               comment='归属导师 teacher_profile.id'))
    op.create_index('ix_archive_package_student_id', 'archive_package', ['student_id'])
    op.create_index('ix_archive_package_teacher_id', 'archive_package', ['teacher_id'])

    # 回填: 纯 SQL UPDATE ... JOIN, 不走 ORM(迁移时模型可能已领先于库结构)
    # cycle_id 是 NOT NULL + FK, 故每条归档包都能查到所属流程实例。
    op.execute(
        """
        UPDATE archive_package AS p
        JOIN graduation_cycle AS c ON c.id = p.cycle_id
        SET p.student_id = c.student_id,
            p.teacher_id = c.teacher_id
        WHERE p.student_id IS NULL
        """
    )

    # 回填完成后收紧为 NOT NULL: 之后任何新的归档包都必须带归属
    op.alter_column('archive_package', 'student_id',
                    existing_type=sa.BigInteger(), nullable=False,
                    existing_comment='归属学生 student_profile.id',
                    comment='归属学生 student_profile.id')


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_archive_package_teacher_id', table_name='archive_package')
    op.drop_index('ix_archive_package_student_id', table_name='archive_package')
    op.drop_column('archive_package', 'teacher_id')
    op.drop_column('archive_package', 'student_id')
