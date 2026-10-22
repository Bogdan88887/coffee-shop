# Развёртывание Coffee Shop на Debian 12 без DNS

Руководство рассчитано на VPS с публичным IPv4 или IPv6 адресом. На дату подготовки руководства Let’s Encrypt выпускает общедоверенные сертификаты прямо на IP. IP-сертификаты короткоживущие: срок действия около 160 часов, поэтому Certbot должен обновлять их автоматически. Для выпуска IP-сертификата Certbot должен быть версии 5.4 или новее и использовать профиль `shortlived`; проверка владения выполняется через HTTP-01 или TLS-ALPN-01. Ниже используется HTTP-01 через Nginx на порту 80. [Let’s Encrypt: доступность IP-сертификатов и Certbot](https://letsencrypt.org/2026/03/11/shorter-certs-certbot), [срок и политика IP-сертификатов](https://letsencrypt.org/2026/01/15/6day-and-ip-general-availability).

Заменяйте `PUBLIC_IP`, `SSH_USER` и `EMAIL` своими значениями. Сертификат выдаётся именно на публичный IP, поэтому он должен быть маршрутизируемым из интернета, закреплённым за VPS и доступным на портах 80 и 443. DNS-записи для этого варианта не нужны.

## 1. Подключитесь к VPS и откройте нужные порты

С компьютера подключитесь по SSH:

```powershell
ssh SSH_USER@PUBLIC_IP
```

На чистом Debian обновите пакеты и установите системные компоненты:

```bash
sudo apt update
sudo apt full-upgrade -y
sudo apt install -y curl ca-certificates gnupg nginx python3 python3-dev python3-venv libaugeas-dev gcc
```

Откройте SSH, HTTP и HTTPS до включения UFW. Если SSH использует нестандартный порт, сначала разрешите именно его вместо `22/tcp`, иначе можно потерять доступ к серверу:

```bash
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw enable
sudo ufw status verbose
```

Также откройте TCP 80 и 443 в сетевом firewall панели хостера, если он есть. Порт 3000 наружу открывать не нужно: NestJS будет слушать только `127.0.0.1`. Debian использует nftables как основной firewall-механизм; если на VPS уже настроен nftables, firewalld или другой firewall, внесите эти правила в него и не включайте второй независимый firewall поверх существующего. [Debian: nftables](https://wiki.debian.org/nftables).

## 2. Установите Node.js

Проект использует NestJS 12. Для сервера выберите актуальный Node.js 24 LTS. NestJS 12 требует Node.js 20.19+ (или 22.12+ для ветки 22), а текущая ветка 24 LTS удовлетворяет этим требованиям. [NestJS: системные требования](https://docs.nestjs.com/first-steps), [Node.js: статусы релизов](https://nodejs.org/en/about/previous-releases).

Добавьте официальный репозиторий NodeSource для Debian и установите Node.js:

```bash
curl -fsSL https://deb.nodesource.com/setup_24.x -o /tmp/nodesource_setup.sh
sudo bash /tmp/nodesource_setup.sh
sudo apt install -y nodejs
node --version
npm --version
```

Убедитесь, что команда `node --version` показывает версию `v24.x` (не ниже 24.15 для актуального Nest CLI). Репозиторий NodeSource предоставляет пакет Node.js 24 для Debian 12. [NodeSource: установка на Debian](https://github.com/nodesource/distributions).

## 3. Передайте проект на VPS

На Windows откройте PowerShell. Создайте архив проекта, исключив локальные зависимости и результат сборки:

```powershell
Set-Location "$env:USERPROFILE\Desktop"
tar.exe -czf coffee-shop-deploy.tar.gz --exclude=node_modules --exclude=dist --exclude=.git -C Coffee-Shop-main .
scp .\coffee-shop-deploy.tar.gz SSH_USER@PUBLIC_IP:/tmp/
```

Подключитесь к серверу и распакуйте проект:

```bash
sudo mkdir -p /opt/coffee-shop
sudo tar -xzf /tmp/coffee-shop-deploy.tar.gz -C /opt/coffee-shop
ls -la /opt/coffee-shop
```

В каталоге должны быть `package.json`, `package-lock.json`, `comments.json`, `src/` и `public/`. Исходный `comments.json` включён в архив: это начальные данные отзывов. После запуска NestJS новые записи будут сохраняться в этот файл.

Создайте системного пользователя приложения:

```bash
sudo useradd --system --home /var/lib/coffeeshop --create-home --shell /usr/sbin/nologin coffeeshop
sudo chown -R coffeeshop:coffeeshop /opt/coffee-shop
```

Установите зависимости по lock-файлу и соберите приложение от имени пользователя приложения:

```bash
sudo -u coffeeshop -H sh -c 'cd /opt/coffee-shop && npm ci && npm run build'
```

После сборки сделайте исходники доступными только для чтения, оставив файл отзывов доступным для записи:

```bash
sudo chown -R root:coffeeshop /opt/coffee-shop
sudo chmod -R a+rX,go-w /opt/coffee-shop
sudo chown coffeeshop:coffeeshop /opt/coffee-shop/comments.json
sudo chmod 640 /opt/coffee-shop/comments.json
```

`comments.json` должен существовать до запуска сервиса. Убедитесь, что у пользователя `coffeeshop` есть право записи в этот файл. Файлы исходников и frontend при этом остаются только для чтения.

## 4. Настройте NestJS как systemd-сервис

Создайте unit-файл:

```bash
sudo tee /etc/systemd/system/coffee-shop.service >/dev/null <<'UNIT'
[Unit]
Description=Coffee Shop NestJS application
After=network.target

[Service]
Type=simple
User=coffeeshop
Group=coffeeshop
WorkingDirectory=/opt/coffee-shop
Environment=NODE_ENV=production
Environment=HOST=127.0.0.1
Environment=PORT=3000
ExecStart=/usr/bin/node /opt/coffee-shop/dist/main.js
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/coffee-shop/comments.json

[Install]
WantedBy=multi-user.target
UNIT
```

Перечитайте unit-файлы, включите сервис при загрузке и запустите его:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now coffee-shop
sudo systemctl status coffee-shop --no-pager
```

Проверьте API непосредственно на сервере:

```bash
curl -i http://127.0.0.1:3000/api/comments
sudo journalctl -u coffee-shop -n 50 --no-pager
sudo ss -ltnp | grep ':3000'
```

Слушающий адрес должен быть `127.0.0.1:3000`, а не `0.0.0.0:3000`.

## 5. Настройте Nginx: статика и HTTP-01 challenge

Создайте каталог для challenge-файлов Certbot:

```bash
sudo install -d -o root -g root -m 0755 /var/www/letsencrypt
```

До получения сертификата настройте HTTP. Этот конфиг раздаёт frontend из `public/`, проксирует `/api/` в NestJS и позволяет Certbot отдавать challenge-файлы:

```bash
sudo tee /etc/nginx/sites-available/coffee-shop >/dev/null <<'NGINX'
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;

    location ^~ /.well-known/acme-challenge/ {
        root /var/www/letsencrypt;
        default_type text/plain;
        try_files $uri =404;
    }

    location = /comments.json {
        return 404;
    }

    location /api/ {
        proxy_pass http://127.0.0.1:3000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location / {
        root /opt/coffee-shop/public;
        try_files $uri $uri/ /index.html;
    }
}
NGINX

sudo ln -s /etc/nginx/sites-available/coffee-shop /etc/nginx/sites-enabled/coffee-shop
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl enable --now nginx
sudo systemctl reload nginx
```

Если у VPS выключен IPv6, уберите строку `listen [::]:80 default_server;` и аналогичную строку для 443 из следующего конфига.

Проверьте, что Nginx обслуживает сайт и challenge-путь с внешней сети:

```bash
printf 'acme-check\n' | sudo tee /var/www/letsencrypt/probe.txt >/dev/null
curl -i http://PUBLIC_IP/
curl http://PUBLIC_IP/.well-known/acme-challenge/probe.txt
curl -i http://PUBLIC_IP/api/comments
```

Первый запрос должен вернуть HTML, второй — текст `acme-check`, третий — JSON-массив. Тот же тест challenge-пути должен проходить с вашего компьютера, иначе Let's Encrypt не сможет достучаться до VPS.

## 6. Установите актуальный Certbot и получите тестовый сертификат

Debian 12 может содержать Certbot старее 5.4. Для поддержки IP-адреса с webroot установите свежий Certbot в отдельное Python-окружение, следуя инструкции EFF:

```bash
sudo python3 -m venv /opt/certbot
sudo /opt/certbot/bin/pip install --upgrade pip
sudo /opt/certbot/bin/pip install 'certbot>=5.4'
sudo ln -s /opt/certbot/bin/certbot /usr/local/bin/certbot
certbot --version
```

Версия должна быть 5.4 или выше. Новые версии Certbot поддерживают параметры `--ip-address` и webroot для IP-сертификатов. [Let’s Encrypt: Certbot 5.4+ и команда для IP](https://letsencrypt.org/2026/03/11/shorter-certs-certbot), [инструкция Certbot для venv](https://certbot.eff.org/instructions?os=pip&ws=other).

Задайте IP и email для примеров команд. Email нужен для уведомлений Let’s Encrypt; если вы не хотите указывать его, вместо `--email EMAIL` используйте `--register-unsafely-without-email`.

```bash
VPS_IP=203.0.113.10
LE_EMAIL=you@example.com
```

Сначала запросите staging-сертификат, чтобы проверить HTTP-01 без расходования лимитов выпуска production-сертификатов:

```bash
sudo certbot certonly \
  --staging \
  --preferred-profile shortlived \
  --webroot \
  --webroot-path /var/www/letsencrypt \
  --ip-address "$VPS_IP" \
  --cert-name coffee-shop-ip-staging \
  --non-interactive \
  --agree-tos \
  --email "$LE_EMAIL" \
  --no-eff-email
```

Если команда завершается успешно, выпустите production-сертификат. Не указывайте `--staging` в этой команде:

```bash
sudo certbot certonly \
  --preferred-profile shortlived \
  --webroot \
  --webroot-path /var/www/letsencrypt \
  --ip-address "$VPS_IP" \
  --cert-name coffee-shop-ip \
  --non-interactive \
  --agree-tos \
  --email "$LE_EMAIL" \
  --no-eff-email
```

Проверьте, что сертификат создан и содержит ваш IP:

```bash
sudo certbot certificates
sudo openssl x509 -in /etc/letsencrypt/live/coffee-shop-ip/fullchain.pem -noout -dates -ext subjectAltName
```

## 7. Включите HTTPS и HTTP → HTTPS redirect

Замените конфигурацию Nginx на финальный вариант. Порт 80 оставляет доступным только challenge для автоматического продления; обычные запросы перенаправляются на HTTPS:

```bash
sudo tee /etc/nginx/sites-available/coffee-shop >/dev/null <<'NGINX'
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;

    location ^~ /.well-known/acme-challenge/ {
        root /var/www/letsencrypt;
        default_type text/plain;
        try_files $uri =404;
    }

    location / {
        return 301 https://$host$request_uri;
    }
}

server {
    listen 443 ssl default_server;
    listen [::]:443 ssl default_server;
    server_name _;

    ssl_certificate /etc/letsencrypt/live/coffee-shop-ip/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/coffee-shop-ip/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;

    location = /comments.json {
        return 404;
    }

    location /api/ {
        proxy_pass http://127.0.0.1:3000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location / {
        root /opt/coffee-shop/public;
        try_files $uri $uri/ /index.html;
    }
}
NGINX

sudo nginx -t
sudo systemctl reload nginx
```

Nginx раздаёт frontend-файлы с диска, передаёт `/api/comments` с тем же URI в NestJS, а JSON-файл отзывов не находится в webroot. Схема использует стандартные директивы Nginx для static root и reverse proxy. [Nginx: static и reverse proxy](https://nginx.org/en/docs/beginners_guide.html), [Nginx: proxy_pass](https://nginx.org/en/docs/http/ngx_http_proxy_module.html).

Проверьте с локального компьютера:

```bash
curl -I http://PUBLIC_IP/
curl -I https://PUBLIC_IP/
curl https://PUBLIC_IP/api/comments
curl -i https://PUBLIC_IP/comments.json
```

Ожидаемый результат: HTTP перенаправляется на HTTPS, HTTPS открывается без предупреждения браузера, API возвращает JSON с HTTP 200, прямой запрос к `/comments.json` возвращает 404. Для строгой проверки сертификата используйте:

```bash
echo | openssl s_client -connect PUBLIC_IP:443 -verify_ip PUBLIC_IP -verify_return_error
```

Не используйте `curl -k`: он отключает проверку сертификата и скрывает проблемы доверия.

## 8. Настройте автоматическое обновление IP-сертификата

Certbot сохраняет параметры выпуска и повторно использует их командой `renew`. Для сертификатов сроком до 10 дней Certbot начинает продление, когда остаётся меньше половины срока действия; запускать проверку каждые 12 часов подходит для сертификата на 160 часов. После успешной замены сертификата Nginx нужно перечитать новые файлы. [Certbot: renew и deploy hooks](https://eff-certbot.readthedocs.io/en/stable/using.html).

Создайте deploy hook, который проверяет конфигурацию и перезагружает Nginx только после успешного обновления сертификата:

```bash
sudo install -d -m 0755 /etc/letsencrypt/renewal-hooks/deploy
sudo tee /etc/letsencrypt/renewal-hooks/deploy/reload-nginx >/dev/null <<'HOOK'
#!/bin/sh
set -eu
/usr/sbin/nginx -t
/bin/systemctl reload nginx
HOOK
sudo chmod 755 /etc/letsencrypt/renewal-hooks/deploy/reload-nginx
```

Добавьте systemd unit для проверки обновлений:

```bash
sudo tee /etc/systemd/system/certbot-renew.service >/dev/null <<'UNIT'
[Unit]
Description=Renew Let's Encrypt IP certificate
Wants=network-online.target
After=network-online.target nginx.service

[Service]
Type=oneshot
ExecStart=/opt/certbot/bin/certbot renew -q
UNIT
```

Создайте timer, который запускает её дважды в сутки:

```bash
sudo tee /etc/systemd/system/certbot-renew.timer >/dev/null <<'UNIT'
[Unit]
Description=Run Certbot renewal checks twice daily

[Timer]
OnCalendar=*-*-* 00,12:00:00
RandomizedDelaySec=30m
Persistent=true

[Install]
WantedBy=timers.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable --now certbot-renew.timer
systemctl list-timers certbot-renew.timer --no-pager
```

Проверьте продление через staging без замены рабочего сертификата:

```bash
sudo /opt/certbot/bin/certbot renew --cert-name coffee-shop-ip --dry-run
```

Посмотреть результаты работы таймера можно так:

```bash
sudo systemctl start certbot-renew.service
sudo journalctl -u certbot-renew.service -n 100 --no-pager
```

## 9. PM2 как альтернатива прямому systemd

В этой инструкции основным менеджером выбран systemd: он запускает Node.js напрямую и уже перезапускает процесс при сбое. **Не запускайте одновременно systemd-unit приложения и PM2 для одного порта.** Если предпочитаете PM2, сначала остановите и отключите `coffee-shop.service`, установите PM2 и запустите приложение от системного пользователя:

```bash
sudo npm install --global pm2
sudo systemctl disable --now coffee-shop
sudo -u coffeeshop -H env HOME=/var/lib/coffeeshop HOST=127.0.0.1 PORT=3000 NODE_ENV=production \
  pm2 start /opt/coffee-shop/dist/main.js --name coffee-shop --cwd /opt/coffee-shop
sudo -u coffeeshop -H env HOME=/var/lib/coffeeshop pm2 save
```

Затем выполните `sudo env PATH=/usr/local/bin:/usr/bin:/bin pm2 startup systemd -u coffeeshop --hp /var/lib/coffeeshop`. Если PM2 выведет дополнительную команду для включения автозапуска, выполните именно её. Проверить процессы можно через `sudo -u coffeeshop -H env HOME=/var/lib/coffeeshop pm2 status`.

Остаётся выбрать только один способ запуска: либо `coffee-shop.service`, либо PM2.

## 10. Эксплуатация и обновление проекта

Основные команды проверки:

```bash
sudo systemctl status coffee-shop nginx certbot-renew.timer --no-pager
sudo journalctl -u coffee-shop -f
sudo journalctl -u nginx -n 50 --no-pager
sudo ss -ltnp
```

При обновлении кода остановите сервис, сохраните текущие отзывы отдельно, передайте и распакуйте новую версию проекта, затем снова выполните `npm ci` и `npm run build`. Восстановите production `comments.json` и права на него до запуска сервиса:

```bash
sudo systemctl stop coffee-shop
sudo cp -a /opt/coffee-shop/comments.json /var/backups/coffee-shop-comments.json
# передайте и распакуйте новую версию исходников в /opt/coffee-shop
# если архив содержит исходный comments.json, восстановите рабочие отзывы:
sudo cp -a /var/backups/coffee-shop-comments.json /opt/coffee-shop/comments.json
sudo chown -R coffeeshop:coffeeshop /opt/coffee-shop
sudo -u coffeeshop -H sh -c 'cd /opt/coffee-shop && npm ci && npm run build'
sudo chown -R root:coffeeshop /opt/coffee-shop
sudo chmod -R a+rX,go-w /opt/coffee-shop
sudo chown coffeeshop:coffeeshop /opt/coffee-shop/comments.json
sudo chmod 640 /opt/coffee-shop/comments.json
sudo systemctl start coffee-shop
```

Делайте регулярные резервные копии `comments.json`: это файловое хранилище, и файл содержит все отзывы.

После смены публичного IP обновите IP в команде Certbot, получите новый сертификат, обновите Nginx при изменении путей и проверьте новый адрес. При недоступности порта 80 автоматическая проверка HTTP-01 не сможет обновить короткоживущий сертификат.
