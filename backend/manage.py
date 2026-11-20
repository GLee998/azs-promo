"""Учётные записи сотрудников и обслуживание базы.

    python manage.py add-user <логин> --role cashier|admin [--station N]   создать, печатает пароль
    python manage.py set-password <логин>                                    новый пароль, старые входы закрываются
    python manage.py disable-user <логин>                                    отключить, входы закрываются
    python manage.py purge                                                   удалить истёкшие сессии и старые счётчики
    python manage.py ensure-user <логин> --role … [--station N] --password-env VAR
                                         учётка с паролем из переменной окружения (тестовый стенд на Render)
    python manage.py adopt-legacy-db     база, созданная до миграций: дописать недостающее и пометить версией 0001

На сервере: docker compose exec api python manage.py add-user azs7 --role cashier --station 7
"""

import argparse
import os
import secrets
import sys
from datetime import timedelta

from sqlalchemy import Column, LargeBinary, MetaData, String, Table, delete, inspect, select, text
from sqlalchemy.orm import Session

import app

# Без похожих символов (0/O, 1/l/I): пароль диктуют по телефону или пишут на бумажке.
ALPHABET = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def new_password() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(12))


def find_user(db: Session, login: str) -> app.StaffUser:
    user = db.scalar(select(app.StaffUser).where(app.StaffUser.login == login.strip().lower()))
    if not user:
        sys.exit(f"Сотрудник {login} не найден")
    return user


def close_sessions(db: Session, user: app.StaffUser) -> None:
    db.execute(delete(app.StaffSession).where(app.StaffSession.user_id == user.id))


def add_user(db: Session, args) -> None:
    login = args.login.strip().lower()
    if db.scalar(select(app.StaffUser.id).where(app.StaffUser.login == login)):
        sys.exit(f"Логин {login} уже занят")
    if args.role == "cashier" and not args.station:
        print("Внимание: у кассира не указана АЗС, выдачи будут отмечаться на АЗС из QR клиента", file=sys.stderr)
    password = new_password()
    db.add(app.StaffUser(login=login, password_hash=app.hash_password(password), role=args.role,
                         station=app.clean_station(args.station), active=True))
    db.commit()
    print(f"Создан {login} ({args.role}{', АЗС №' + args.station if args.station else ''}). Пароль: {password}")


def set_password(db: Session, args) -> None:
    user = find_user(db, args.login)
    password = new_password()
    user.password_hash = app.hash_password(password)
    user.active = True
    close_sessions(db, user)
    db.commit()
    print(f"Новый пароль для {user.login}: {password}")


def disable_user(db: Session, args) -> None:
    user = find_user(db, args.login)
    user.active = False
    close_sessions(db, user)
    db.commit()
    print(f"{user.login} отключён")


def purge(db: Session, args) -> None:
    sessions = db.execute(delete(app.StaffSession).where(app.StaffSession.expires_at < app.now())).rowcount
    hits = app.purge_rate_hits(db)
    codes = db.execute(delete(app.SmsCode).where(app.SmsCode.sent_at < app.now() - timedelta(days=1))).rowcount
    db.commit()
    print(f"Удалено истёкших сессий: {sessions}, старых счётчиков попыток: {hits}, старых SMS-кодов: {codes}")


def ensure_user(db: Session, args) -> None:
    """Учётка, пароль которой хранится в переменной окружения: на бесплатном Render нет консоли,
    поэтому сотрудники тестового стенда заводятся при каждом старте. Пароль меняется, только если
    значение переменной изменилось, иначе каждый перезапуск выкидывал бы всех из системы."""
    password = os.environ.get(args.password_env, "")
    if not password:
        sys.exit(f"Переменная окружения {args.password_env} пуста")
    login = args.login.strip().lower()
    user = db.scalar(select(app.StaffUser).where(app.StaffUser.login == login))
    if user is None:
        user = app.StaffUser(login=login, password_hash=app.hash_password(password))
        db.add(user)
    elif not app.check_password(password, user.password_hash):
        user.password_hash = app.hash_password(password)
        close_sessions(db, user)
    user.role, user.station, user.active = args.role, app.clean_station(args.station), True
    db.commit()
    print(f"Учётка {login} ({args.role}) готова")


BASELINE = "0001"  # migrations/versions/0001_initial.py


def adopt_legacy_db(db: Session, args) -> None:
    """База, созданная до появления миграций (create_all старой версии), переходит под Alembic без
    потери данных: дописываем колонку превью, новые таблицы и индексы и помечаем базу версией 0001."""
    engine = app.engine
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    if "registrations" not in tables or "alembic_version" in tables:
        print("База пустая или уже под миграциями, переводить нечего")
        return
    with engine.begin() as conn:
        if "thumb" not in {c["name"] for c in insp.get_columns("registration_photos")}:
            blob = LargeBinary().compile(dialect=engine.dialect)
            conn.execute(text(f"ALTER TABLE registration_photos ADD COLUMN thumb {blob}"))
        app.Base.metadata.create_all(conn)  # только недостающие таблицы
        for index in app.Registration.__table__.indexes:
            index.create(conn, checkfirst=True)
        version = Table("alembic_version", MetaData(), Column("version_num", String(32), primary_key=True))
        version.create(conn)
        conn.execute(version.insert().values(version_num=BASELINE))
    print(f"Старая база переведена под миграции, версия {BASELINE}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Сотрудники и обслуживание базы акции")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("add-user", help="создать сотрудника")
    p.add_argument("login")
    p.add_argument("--role", choices=["cashier", "admin"], required=True)
    p.add_argument("--station", help="номер АЗС кассира")
    p.set_defaults(fn=add_user)
    p = sub.add_parser("set-password", help="выдать новый пароль")
    p.add_argument("login")
    p.set_defaults(fn=set_password)
    p = sub.add_parser("disable-user", help="отключить сотрудника")
    p.add_argument("login")
    p.set_defaults(fn=disable_user)
    sub.add_parser("purge", help="удалить истёкшие сессии и старые счётчики").set_defaults(fn=purge)
    p = sub.add_parser("ensure-user", help="учётка с паролем из переменной окружения")
    p.add_argument("login")
    p.add_argument("--role", choices=["cashier", "admin"], required=True)
    p.add_argument("--station")
    p.add_argument("--password-env", required=True, help="имя переменной окружения с паролем")
    p.set_defaults(fn=ensure_user)
    sub.add_parser("adopt-legacy-db", help="перевести старую базу под миграции").set_defaults(fn=adopt_legacy_db)
    args = parser.parse_args(argv)
    # app.engine читается при вызове: тесты подменяют базу.
    with Session(app.engine) as db:
        args.fn(db, args)


if __name__ == "__main__":
    main()
