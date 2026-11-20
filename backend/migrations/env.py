"""Миграции схемы базы (Alembic).

Адрес базы и модели берутся из app.py, то есть из переменной окружения DATABASE_URL.
Применить: alembic upgrade head. Новая миграция после изменения моделей:
alembic revision --autogenerate -m "что изменилось", затем вычитать сгенерированный файл.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

import app

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = app.Base.metadata


def run_migrations_offline() -> None:
    """SQL без подключения к базе: alembic upgrade head --sql."""
    context.configure(url=app.DB_URL, target_metadata=target_metadata, literal_binds=True,
                      dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(app.DB_URL, poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
