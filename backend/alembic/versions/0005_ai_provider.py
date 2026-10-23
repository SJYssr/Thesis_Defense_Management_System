"""ai_provider

P11: AI 配置从"16 个 system_config 键 + 环境变量回退"改为"供应商表 + 能力绑定键"。
一个供应商 = 一份 Base URL + 一份 API Key, 会话/向量/精排三个能力位只保存
(provider_id, model), 不再各填一遍地址与密钥。

本迁移只建表: 旧键的折算(含 API Key 加密)放在应用层
app.ai.providers.migrate_legacy_config() —— 加密需要 settings.secret_key,
而迁移脚本不该依赖应用运行配置。

Revision ID: 0005_ai_provider
Revises: 0004_archive_package_owner
Create Date: 2026-10-25 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0005_ai_provider'
down_revision: Union[str, Sequence[str], None] = '0004_archive_package_owner'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'ai_provider',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('name', sa.String(length=64), nullable=False, comment='供应商显示名, 如 智谱 / 本地 ollama'),
        sa.Column('kind', sa.String(length=32), nullable=False,
                  comment='预设类型: openai/ollama/deepseek/zhipu/dashscope/moonshot/custom'),
        sa.Column('base_url', sa.String(length=255), nullable=False,
                  comment='OpenAI 兼容基址(含 /v1); ollama 填 http://host:11434'),
        sa.Column('api_key_enc', sa.String(length=512), nullable=True,
                  comment='Fernet 密文; 空表示该供应商无需 Key'),
        sa.Column('enabled', sa.SmallInteger(), nullable=False, comment='1 启用 / 0 停用'),
        sa.Column('models_json', sa.Text(), nullable=True, comment='缓存的模型清单 JSON 数组'),
        sa.Column('models_refreshed_at', sa.DateTime(), nullable=True, comment='模型清单最近刷新时间'),
        sa.Column('last_status', sa.String(length=16), nullable=True, comment='最近探测: ok/degraded/down'),
        sa.Column('last_detail', sa.String(length=512), nullable=True, comment='最近探测说明'),
        sa.Column('last_probe_at', sa.DateTime(), nullable=True, comment='最近探测时间'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False, comment='创建时间'),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False, comment='更新时间'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name', name='uq_ai_provider_name'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('ai_provider')
