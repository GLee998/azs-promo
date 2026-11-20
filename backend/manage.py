"""Учётные записи сотрудников и обслуживание базы.

    python manage.py add-user <логин> --role cashier|admin [--station N]   создать, печатает пароль
    python manage.py set-password <логин>                                    новый пароль, старые входы закрываются
    python manage.py disable-user <логин>                                    отключить, входы закрываются
    python manage.py purge                                                   удалить истёкшие сессии и старые счётчики

На сервере: docker compose exec api python manage.py add-user azs7 --role cashier --station 7
"""

import argparse
import secrets
import sys
from datetime import timedelta

from sqlalchemy import delete, select
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
    args = parser.parse_args(argv)
    # app.engine читается при вызове: тесты подменяют базу.
    with Session(app.engine) as db:
        args.fn(db, args)


if __name__ == "__main__":
    main()
