# Прод на своём сервере

Один VPS, на нём Docker Compose:

* **Caddy** выпускает HTTPS-сертификаты и отдаёт три сайта из `dist/`. Запросы `/api/*` с каждого сайта уходят в API, поэтому CORS не нужен.
* **API** — FastAPI на uvicorn, 2 воркера. Перед стартом применяет миграции базы.
* **Postgres 16**: данные в Docker volume, фото чеков в той же базе.

Сервер: 2 vCPU, 4 ГБ RAM, 60–80 ГБ NVMe, Ubuntu 24.04. Страна дата-центра зависит от требований к хранению персональных данных (вопрос юристу, см. аудит).

## 1. Подготовка сервера

```bash
# под root на новом сервере
adduser deploy && usermod -aG sudo deploy
# скопировать свой SSH-ключ: ssh-copy-id deploy@<IP> (с вашего компьютера), затем:
sed -i 's/^#\?PasswordAuthentication .*/PasswordAuthentication no/; s/^#\?PermitRootLogin .*/PermitRootLogin no/' /etc/ssh/sshd_config
systemctl restart ssh
ufw allow 22,80,443/tcp && ufw allow 443/udp && ufw enable
apt update && apt install -y unattended-upgrades git rclone && dpkg-reconfigure -plow unattended-upgrades
timedatectl set-timezone Asia/Bishkek
curl -fsSL https://get.docker.com | sh && usermod -aG docker deploy
```

## 2. DNS

A-записи трёх адресов на IP сервера, например:

| Адрес | Сайт |
|---|---|
| `karavan.example.kg` | форма клиента (на него ведёт QR на кассах) |
| `kassa.karavan.example.kg` | касса |
| `admin.karavan.example.kg` | реестр и розыгрыш |

Для тестового стенда — ещё три записи (`test-…`). Без них стенд не нужен.

## 3. Первый запуск

```bash
sudo mkdir -p /opt/karavan && sudo chown deploy /opt/karavan
git clone <репозиторий> /opt/karavan && cd /opt/karavan
cp deploy/.env.example deploy/.env && nano deploy/.env    # пароль базы (openssl rand -hex 24) и адреса
deploy/build-sites.sh
cd deploy && docker compose up -d --build
curl https://<CLIENT_HOST>/api/health                       # {"ok":true}
```

Сертификаты Caddy получает сам при первом обращении, если DNS уже указывает на сервер.

## 4. Учётки сотрудников

```bash
cd /opt/karavan/deploy
docker compose exec api python manage.py add-user marketing --role admin
docker compose exec api python manage.py add-user azs7 --role cashier --station 7   # по одной на каждую АЗС
docker compose exec api python manage.py set-password azs7     # забыли пароль
docker compose exec api python manage.py disable-user azs7     # уволился
```

Пароль печатается один раз. Передавайте его лично, не в общем чате. Все выдачи, отмены и действия маркетинга видны в журнале: админка → `/api/admin/audit.csv`.

## 5. Обновление

```bash
cd /opt/karavan && git pull && deploy/build-sites.sh && cd deploy && docker compose up -d --build
```

Миграции базы применяются сами при старте API. Новая миграция после изменения моделей: `cd backend && alembic revision --autogenerate -m "что изменилось"`, вычитать файл и закоммитить.

## 6. Регулярные задачи (crontab -e под deploy)

```cron
# бэкап каждые 6 часов на время акции
0 */6 * * * /opt/karavan/deploy/backup.sh >> /var/log/karavan/backup.log 2>&1
# старые сессии и счётчики попыток
30 4 * * * cd /opt/karavan/deploy && docker compose exec -T api python manage.py purge >> /var/log/karavan/purge.log 2>&1
```

Перед этим: `sudo mkdir -p /var/log/karavan /var/backups/karavan && sudo chown deploy /var/log/karavan /var/backups/karavan`.

Копия бэкапов вне сервера: `rclone config` (например, S3-совместимое хранилище), затем в `deploy/.env` указать `RCLONE_REMOTE=<remote>:<bucket>`. Без копии вне сервера потеря диска означает потерю всех заявок.

## 7. Мониторинг

* Внешний монитор доступности (UptimeRobot, Better Stack или аналог) опрашивает `https://<CLIENT_HOST>/api/health` раз в минуту и шлёт уведомление в Telegram или на почту. Health проверяет и API, и базу.
* Уведомление о заполнении диска больше 80%: алерт в панели провайдера или cron с `df`.
* Логи: `docker compose logs -f api` (хранятся по 50 МБ на сервис).

## 8. Проверка восстановления (сделать до старта акции)

```bash
cd /opt/karavan/deploy
./backup.sh
./restore.sh /var/backups/karavan/<последний>.dump      # в отдельную базу promo_restore, рабочая не трогается
```

Скрипт печатает число заявок в восстановленной базе. Заменить рабочую базу:

```bash
docker compose stop api
./restore.sh <файл.dump> promo --replace
docker compose start api
```

## Тестовый стенд на том же сервере

В `deploy/.env` заполнить `TEST_CLIENT_HOST`, `TEST_KASSA_HOST`, `TEST_ADMIN_HOST`, затем:

```bash
deploy/build-sites.sh                                   # соберёт и dist/test с DEMO=true
cd deploy && docker compose --profile test up -d --build
docker compose exec api-test python manage.py add-user demo --role admin
```

На стенде своя база, плашка «Тестовая версия», генератор тестовых чеков и разрешён сброс розыгрыша.
