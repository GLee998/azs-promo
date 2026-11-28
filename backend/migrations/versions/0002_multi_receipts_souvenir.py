"""several receipts per phone, souvenir once per phone

Условия розыгрыша (с 01.12.2026): один чек — один купон, номер может регистрировать несколько чеков,
сувенир выдаётся один раз на номер. Номер больше не блокируется (phone_lock обнуляется),
вместо этого souvenir_phone не даёт выдать второй сувенир на тот же номер.

Revision ID: 0002
Revises: 0001
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0002'
down_revision: Union[str, Sequence[str], None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('registrations') as b:
        b.add_column(sa.Column('souvenir', sa.Boolean(), nullable=False, server_default=sa.false()))
        b.add_column(sa.Column('souvenir_phone', sa.String(length=16), nullable=True))
        b.create_unique_constraint('uq_registrations_souvenir_phone', ['souvenir_phone'])
    # До этой версии подтверждённая заявка означала выданную машинку, и номер был занят ею (phone_lock).
    op.execute("UPDATE registrations SET souvenir = true, souvenir_phone = phone_lock "
               "WHERE status IN ('issued', 'disqualified') AND phone_lock IS NOT NULL")
    op.execute("UPDATE registrations SET phone_lock = NULL")


def downgrade() -> None:
    op.execute("UPDATE registrations SET phone_lock = souvenir_phone WHERE souvenir_phone IS NOT NULL")
    with op.batch_alter_table('registrations') as b:
        b.drop_constraint('uq_registrations_souvenir_phone', type_='unique')
        b.drop_column('souvenir_phone')
        b.drop_column('souvenir')
