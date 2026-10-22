#!/usr/bin/env python3
"""Безопасное развёртывание Coffee Shop на Debian 12.

По умолчанию скрипт ничего не меняет. Для применения нужен --apply и
подтверждение готовности сетевого firewall. Все команды запускаются без shell.
"""

from __future__ import annotations

import argparse
import datetime as dt
import http.client
import ipaddress
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Sequence


APP_NAME = "coffee-shop"
APP_USER = "coffeeshop"
APP_GROUP = "coffeeshop"
APP_DIR = Path("/opt/coffee-shop")
DATA_FILE = "comments.json"
BUILD_DIR = Path("/var/tmp")
CERTBOT_DIR = Path("/opt/certbot")
CERTBOT_BIN = CERTBOT_DIR / "bin" / "certbot"
CERT_NAME = "coffee-shop-ip"
STAGING_CERT_NAME = "coffee-shop-ip-staging"
ACME_ROOT = Path("/var/www/letsencrypt")
NGINX_AVAILABLE = Path("/etc/nginx/sites-available/coffee-shop")
NGINX_ENABLED = Path("/etc/nginx/sites-enabled/coffee-shop")
SYSTEMD_APP = Path("/etc/systemd/system/coffee-shop.service")
SYSTEMD_RENEW = Path("/etc/systemd/system/certbot-renew.service")
SYSTEMD_TIMER = Path("/etc/systemd/system/certbot-renew.timer")
DEPLOY_HOOK = Path("/etc/letsencrypt/renewal-hooks/deploy/reload-nginx")
BACKUP_BASE = Path("/var/backups/coffee-shop-deploy")
MANAGED_MARKER = "# Managed by deploy_debian12.py"
SOURCE_FILES = (
    "package.json",
    "package-lock.json",
    "nest-cli.json",
    "tsconfig.json",
    "tsconfig.build.json",
)
SOURCE_DIRS = ("src", "public")
APP_MANAGED_ITEMS = (*SOURCE_FILES, *SOURCE_DIRS, "dist", "node_modules", DATA_FILE)
APT_BASE_PACKAGES = (
    "curl",
    "ca-certificates",
    "gnupg",
    "nginx",
    "python3",
    "python3-dev",
    "python3-venv",
    "libaugeas-dev",
    "gcc",
    "openssl",
    "iproute2",
)
NODE_SOURCE_SETUP = "https://deb.nodesource.com/setup_24.x"


class DeployError(RuntimeError):
    """Ожидаемая ошибка, при которой развёртывание нужно остановить."""


def say(message: str) -> None:
    print(message, flush=True)


def run(
    argv: Sequence[str],
    *,
    cwd: Path | None = None,
    timeout: int = 300,
    env: dict[str, str] | None = None,
    check: bool = True,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Выполняет команду без shell и сообщает полезный фрагмент ошибки."""
    command = [str(part) for part in argv]
    say("$ " + " ".join(command))
    try:
        result = subprocess.run(
            command,
            cwd=str(cwd) if cwd else None,
            env=env,
            input=input_text,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise DeployError(
            f"Истёк тайм-аут {timeout} с при выполнении команды: {command[0]}"
        ) from exc
    except OSError as exc:
        raise DeployError(f"Не удалось запустить {command[0]}: {exc}") from exc

    if check and result.returncode != 0:
        details = (result.stderr or result.stdout or "").strip()
        if len(details) > 3500:
            details = details[-3500:]
        suffix = f"\n{details}" if details else ""
        raise DeployError(
            f"Команда завершилась с кодом {result.returncode}: "
            f"{command[0]}{suffix}"
        )
    return result


def validate_source(source: Path) -> dict[str, object]:
    """Проверяет исходники и не следует по символическим ссылкам."""
    if not source.is_dir():
        raise DeployError(f"Не найден каталог проекта: {source}")

    required = [
        *(source / name for name in SOURCE_FILES),
        *(source / name for name in SOURCE_DIRS),
        source / DATA_FILE,
        source / "src" / "main.ts",
        source / "src" / "comments" / "comments.controller.ts",
        source / "src" / "comments" / "comments.service.ts",
        source / "src" / "comments" / "comments.repository.ts",
        source / "public" / "index.html",
    ]
    for path in required:
        if not path.exists():
            raise DeployError(f"Не найден обязательный файл проекта: {path}")
        if path.is_symlink():
            raise DeployError(f"Символические ссылки в исходниках не поддерживаются: {path}")

    for relative in SOURCE_DIRS:
        for path in (source / relative).rglob("*"):
            if path.is_symlink():
                raise DeployError(f"Найдена символическая ссылка в исходниках: {path}")

    try:
        package = json.loads((source / "package.json").read_text(encoding="utf-8"))
        lock = json.loads((source / "package-lock.json").read_text(encoding="utf-8"))
        comments = json.loads((source / DATA_FILE).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DeployError(f"Не удалось проверить JSON проекта: {exc}") from exc

    scripts = package.get("scripts", {})
    dependencies = package.get("dependencies", {})
    if scripts.get("build") != "nest build":
        raise DeployError("Ожидался скрипт сборки package.json: nest build.")
    if scripts.get("start") != "node dist/main.js":
        raise DeployError("Ожидалась команда запуска package.json: node dist/main.js.")
    if "@nestjs/core" not in dependencies or "@nestjs/platform-express" not in dependencies:
        raise DeployError("package.json не подтверждает backend на NestJS с Express-адаптером.")
    if not isinstance(comments, list):
        raise DeployError("comments.json должен содержать JSON-массив.")
    if lock.get("name") != package.get("name"):
        raise DeployError("package-lock.json не соответствует имени проекта в package.json.")
    return package


def parse_public_ip(value: str | None, *, prompt: bool) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    if not value and prompt and sys.stdin.isatty():
        value = input("Публичный IP-адрес VPS: ").strip()
    if not value:
        raise DeployError("Укажите фактический публичный адрес через --public-ip.")
    try:
        address = ipaddress.ip_address(value.strip())
    except ValueError as exc:
        raise DeployError("--public-ip должен быть корректным IPv4- или IPv6-адресом.") from exc
    return address


def parse_email(value: str | None, *, prompt: bool) -> str | None:
    if value is None and prompt and sys.stdin.isatty():
        value = input("Email для уведомлений Let’s Encrypt (Enter — без email): ").strip()
    if not value:
        return None
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
        raise DeployError("Проверьте адрес электронной почты в --email.")
    return value


def print_dry_run(source: Path, address: ipaddress._BaseAddress, email: str | None) -> int:
    """Показывает план, не обращаясь к ОС и не создавая временные файлы."""
    say("ПРЕДВАРИТЕЛЬНАЯ ПРОВЕРКА: состояние системы не изменяется.")
    say(f"Проект: {source}")
    say(f"Публичный IP: {address}")
    say(f"Email Let’s Encrypt: {email or 'не указан'}")
    steps = (
        "проверить Debian 12, права root, команды и занятость портов",
        "обновить индекс APT; установить Nginx, Python 3, средства сборки и диагностики",
        "при необходимости подключить NodeSource 24.x и установить Node.js 24",
        "создать системную учётную запись coffeeshop, если её ещё нет",
        "в отдельном временном каталоге выполнить npm ci --include=dev и npm run build",
        "сохранить управляемые файлы текущего размещения в /var/backups/coffee-shop-deploy",
        "обновить исходники и dist, сохранив текущий comments.json",
        "установить только production-зависимости в /opt/coffee-shop/node_modules",
        "создать systemd-службу NestJS на 127.0.0.1:3000 и проверить API",
        "настроить Nginx для статики, /api/ и HTTP-01 challenge; выполнить nginx -t",
        "установить Certbot в /opt/certbot и проверить выпуск staging-сертификата",
        "после отдельного подтверждения запросить production IP-сертификат Let’s Encrypt",
        "включить HTTPS, deploy hook и systemd-таймер продления каждые 12 часов",
        "проверить TLS, API, блокировку /comments.json и systemd-состояния",
    )
    for index, step in enumerate(steps, 1):
        say(f"{index:02d}. {step}")
    say(
        "Вручную до запуска: подтвердить открытые снаружи TCP 80/443 в firewall "
        "хостера/сети. Скрипт не меняет firewall и SSH."
    )
    if not email:
        say("При реальном запуске Certbot будет зарегистрирован без email.")
    return 0


def read_os_release() -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key] = value.strip().strip('"').strip("'")
    except OSError as exc:
        raise DeployError(f"Не удалось прочитать /etc/os-release: {exc}") from exc
    return values


def require_command(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise DeployError(f"Не найдена обязательная системная команда: {name}")
    return path


def validate_host(address: ipaddress._BaseAddress) -> None:
    info = read_os_release()
    if info.get("ID") != "debian" or info.get("VERSION_ID") != "12":
        raise DeployError(
            f"Поддерживается Debian 12; обнаружено {info.get('PRETTY_NAME', 'неизвестная ОС')}."
        )
    if os.geteuid() != 0:
        raise DeployError("Для системных изменений запустите скрипт через sudo от root.")
    if not address.is_global:
        raise DeployError("Для выпуска IP-сертификата нужен публичный глобальный IP.")
    if address.version == 6:
        disabled = Path("/proc/sys/net/ipv6/conf/all/disable_ipv6")
        if disabled.exists() and disabled.read_text(encoding="ascii").strip() == "1":
            raise DeployError("В системе отключён IPv6, поэтому IPv6-адрес использовать нельзя.")
    for name in ("apt-get", "systemctl", "useradd", "groupadd", "runuser"):
        require_command(name)


def confirm_full_upgrade() -> None:
    if not sys.stdin.isatty():
        raise DeployError(
            "--full-upgrade требует интерактивного подтверждения; запустите его из терминала."
        )
    answer = input(
        "Будет выполнено полное обновление пакетов Debian. "
        "Для продолжения введите UPGRADE: "
    ).strip()
    if answer != "UPGRADE":
        raise DeployError("Полное обновление системы отменено.")


def backup_identity() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


class BackupManager:
    """Создаёт резервные копии существующих управляемых файлов перед заменой."""

    def __init__(self) -> None:
        self.run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        self.root = BACKUP_BASE / self.run_id
        self.created = False
        self._prepared = False
        self._backed_up: set[str] = set()

    def _prepare_root(self) -> None:
        if self._prepared:
            return
        if (
            BACKUP_BASE.parent.is_symlink()
            or (BACKUP_BASE.parent.exists() and not BACKUP_BASE.parent.is_dir())
            or BACKUP_BASE.is_symlink()
            or (BACKUP_BASE.exists() and not BACKUP_BASE.is_dir())
        ):
            raise DeployError(f"Каталог резервных копий занят небезопасным путём: {BACKUP_BASE}")
        BACKUP_BASE.mkdir(parents=True, exist_ok=True)
        if self.root.is_symlink() or self.root.exists():
            raise DeployError(f"Путь резервной копии уже существует: {self.root}")
        self.root.mkdir(mode=0o700)
        self._prepared = True

    def _target(self, label: str) -> Path:
        relative = label.lstrip("/").replace(":", "_")
        return self.root / relative

    def backup_file(self, path: Path, label: str | None = None) -> None:
        if not path.exists() or path.is_symlink():
            return
        key = str(path.resolve())
        if key in self._backed_up:
            return
        self._prepare_root()
        target = self._target(label or str(path))
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        self._backed_up.add(key)
        self.created = True
        say(f"Резервная копия: {path} -> {target}")

    def backup_tree(self, path: Path, label: str | None = None) -> None:
        if not path.exists() or path.is_symlink():
            return
        key = str(path.resolve())
        if key in self._backed_up:
            return
        self._prepare_root()
        target = self._target(label or str(path))
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(path, target, symlinks=True)
        self._backed_up.add(key)
        self.created = True
        say(f"Резервная копия: {path} -> {target}")

    def backup_app(self, destination: Path) -> None:
        if not destination.exists():
            return
        if destination.is_symlink() or not destination.is_dir():
            raise DeployError(f"Путь приложения не является обычным каталогом: {destination}")
        for item in APP_MANAGED_ITEMS:
            path = destination / item
            if path.is_symlink():
                raise DeployError(f"Управляемый путь в приложении является ссылкой: {path}")
            if path.is_dir():
                self.backup_tree(path, f"opt/coffee-shop/{item}")
            elif path.is_file():
                self.backup_file(path, f"opt/coffee-shop/{item}")


def atomic_write(path: Path, content: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def is_expected_or_managed(path: Path, content: str, compatible: Sequence[str]) -> bool:
    if not path.exists():
        return True
    if path.is_symlink() or not path.is_file():
        return False
    current = path.read_text(encoding="utf-8", errors="replace")
    return current == content or MANAGED_MARKER in current or all(
        token in current for token in compatible
    )


def install_managed_file(
    path: Path,
    content: str,
    backups: BackupManager,
    *,
    compatible: Sequence[str] = (),
    mode: int = 0o644,
) -> None:
    if path.exists():
        if path.is_symlink() or not path.is_file():
            raise DeployError(f"Управляемый путь занят не обычным файлом: {path}")
        if path.read_text(encoding="utf-8", errors="replace") == content:
            return
    if not is_expected_or_managed(path, content, compatible):
        raise DeployError(
            f"Неизвестная конфигурация не изменена: {path}. "
            "Сохраните её отдельно и освободите управляемый путь вручную."
        )
    backups.backup_file(path)
    atomic_write(path, content, mode)


def get_group_gid() -> int:
    try:
        import grp

        return grp.getgrnam(APP_GROUP).gr_gid
    except (ImportError, KeyError) as exc:
        raise DeployError(f"Не удалось получить системную группу {APP_GROUP}.") from exc


def ensure_app_account(backups: BackupManager) -> None:
    user = run(["getent", "passwd", APP_USER], check=False)
    group = run(["getent", "group", APP_GROUP], check=False)
    if user.returncode == 0 and group.returncode == 0:
        fields = user.stdout.strip().split(":")
        if len(fields) < 7 or fields[5] != "/var/lib/coffeeshop" or not fields[6].endswith("nologin"):
            raise DeployError(
                f"Учётная запись {APP_USER} уже существует с другими параметрами; "
                "она не будет изменена."
            )
        return
    if user.returncode == 0 or group.returncode == 0:
        raise DeployError(
            f"Обнаружена неполная учётная запись {APP_USER}; автоматическое исправление отменено."
        )
    run(["groupadd", "--system", APP_GROUP])
    run(
        [
            "useradd",
            "--system",
            "--gid",
            APP_GROUP,
            "--home-dir",
            "/var/lib/coffeeshop",
            "--create-home",
            "--shell",
            "/usr/sbin/nologin",
            APP_USER,
        ]
    )


def apt_environment() -> dict[str, str]:
    env = os.environ.copy()
    env["DEBIAN_FRONTEND"] = "noninteractive"
    return env


def install_base_packages(full_upgrade: bool) -> None:
    run(["apt-get", "update"], timeout=600, env=apt_environment())
    if full_upgrade:
        confirm_full_upgrade()
        run(
            ["apt-get", "full-upgrade", "-y"],
            timeout=1800,
            env=apt_environment(),
        )
    run(
        ["apt-get", "install", "-y", *APT_BASE_PACKAGES],
        timeout=900,
        env=apt_environment(),
    )


def node_version() -> tuple[int, int, int] | None:
    executable = shutil.which("node")
    if not executable:
        return None
    result = run([executable, "--version"], check=False, timeout=20)
    match = re.search(r"v?(\d+)\.(\d+)\.(\d+)", result.stdout)
    if result.returncode != 0 or not match:
        return None
    return tuple(int(part) for part in match.groups())


def nodesource_configured() -> bool:
    roots = (Path("/etc/apt/sources.list.d"),)
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.iterdir():
            if not path.is_file() or path.is_symlink():
                continue
            try:
                if "deb.nodesource.com" in path.read_text(encoding="utf-8", errors="ignore"):
                    return True
            except OSError:
                continue
    try:
        return "deb.nodesource.com" in Path("/etc/apt/sources.list").read_text(
            encoding="utf-8", errors="ignore"
        )
    except OSError:
        return False


def run_nodesource_setup() -> None:
    say(f"Загружается официальный установщик NodeSource: {NODE_SOURCE_SETUP}")
    try:
        with urllib.request.urlopen(NODE_SOURCE_SETUP, timeout=45) as response:
            script = response.read(2_000_001)
    except (OSError, urllib.error.URLError) as exc:
        raise DeployError(f"Не удалось получить установщик NodeSource по HTTPS: {exc}") from exc
    if len(script) > 2_000_000 or not script:
        raise DeployError("Установщик NodeSource пуст или превышает допустимый размер.")
    fd, path = tempfile.mkstemp(prefix="nodesource-setup-", suffix=".sh", dir="/tmp")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(script)
        os.chmod(path, 0o700)
        run(["/bin/bash", path], timeout=300, env=apt_environment())
    finally:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass


def ensure_node24() -> str:
    version = node_version()
    if version is None or version < (24, 15, 0):
        if not nodesource_configured():
            run_nodesource_setup()
        run(["apt-get", "update"], timeout=600, env=apt_environment())
        run(
            ["apt-get", "install", "-y", "nodejs"],
            timeout=900,
            env=apt_environment(),
        )
    version = node_version()
    if version is None or version[0] != 24 or version < (24, 15, 0):
        shown = ".".join(map(str, version)) if version else "не установлен"
        raise DeployError(
            f"Требуется Node.js 24.15 или новее в ветке 24; обнаружено: {shown}."
        )
    npm = shutil.which("npm")
    if not npm:
        raise DeployError("После установки Node.js команда npm не найдена.")
    run([npm, "--version"], timeout=30)
    return npm


def ensure_certbot() -> None:
    if CERTBOT_DIR.is_symlink():
        raise DeployError(f"Каталог Certbot является символической ссылкой: {CERTBOT_DIR}")
    if CERTBOT_DIR.exists() and not (CERTBOT_DIR / "bin" / "python3").exists():
        raise DeployError(
            f"{CERTBOT_DIR} уже существует, но не выглядит как управляемое venv; "
            "каталог не будет перезаписан."
        )
    if not CERTBOT_DIR.exists():
        run(["python3", "-m", "venv", str(CERTBOT_DIR)], timeout=180)
    certbot_pip = CERTBOT_DIR / "bin" / "pip"
    if not certbot_pip.exists():
        raise DeployError(f"В окружении Certbot не найдена команда {certbot_pip}.")

    version_result = run([str(CERTBOT_BIN), "--version"], check=False, timeout=30)
    version_match = re.search(r"(\d+)\.(\d+)", version_result.stdout)
    current = (
        tuple(int(part) for part in version_match.groups())
        if version_result.returncode == 0 and version_match
        else None
    )
    if current is None or current < (5, 4):
        run([str(certbot_pip), "install", "--upgrade", "pip"], timeout=300)
        run(
            [str(certbot_pip), "install", "certbot>=5.4"],
            timeout=900,
        )
    version_result = run([str(CERTBOT_BIN), "--version"], timeout=30)
    version_match = re.search(r"(\d+)\.(\d+)", version_result.stdout)
    if not version_match or tuple(int(part) for part in version_match.groups()) < (5, 4):
        raise DeployError("Не удалось обеспечить Certbot версии 5.4 или новее.")

    public_link = Path("/usr/local/bin/certbot")
    if public_link.is_symlink():
        if public_link.resolve() != CERTBOT_BIN.resolve():
            raise DeployError(
                f"{public_link} уже указывает на другой Certbot; ссылка не изменена."
            )
    elif public_link.exists():
        raise DeployError(f"{public_link} уже занят обычным файлом; он не изменён.")
    else:
        public_link.symlink_to(CERTBOT_BIN)


def unit_active(name: str) -> bool:
    return run(["systemctl", "is-active", "--quiet", name], check=False, timeout=15).returncode == 0


def unit_enabled(name: str) -> bool:
    return run(["systemctl", "is-enabled", "--quiet", name], check=False, timeout=15).returncode == 0


def check_port_3000() -> None:
    result = run(["ss", "-ltnp"], check=False, timeout=20)
    if result.returncode != 0:
        return
    conflicting = [
        line.strip()
        for line in result.stdout.splitlines()
        if ":3000" in line and "LISTEN" in line
    ]
    if conflicting and not unit_active("coffee-shop.service"):
        raise DeployError(
            "Порт 3000 уже занят другим процессом; он не будет остановлен. "
            + "; ".join(conflicting[:2])
        )


def nginx_ipv6_enabled() -> bool:
    flag = Path("/proc/sys/net/ipv6/conf/all/disable_ipv6")
    try:
        return flag.read_text(encoding="ascii").strip() != "1"
    except OSError:
        return socket.has_ipv6


def current_nginx_config() -> str:
    result = run(["nginx", "-T"], check=False, timeout=45)
    if result.returncode != 0:
        raise DeployError(
            "Текущая конфигурация Nginx не проходит nginx -T; сначала исправьте её вручную."
        )
    return result.stdout


def has_foreign_tls_listener(config: str) -> bool:
    source = ""
    allowed_sources = (
        str(NGINX_AVAILABLE),
        str(NGINX_ENABLED),
    )
    for line in config.splitlines():
        if line.startswith("# configuration file ") and line.endswith(":"):
            source = line[len("# configuration file ") : -1]
            continue
        stripped = line.strip()
        if not stripped.startswith("listen"):
            continue
        if not re.search(r"(?:^|\s)(?:\[\:\:\]|(?:\*|[0-9.]+)?):?443(?:\s|;)|(?:^|\s)443(?:\s|;)", stripped):
            continue
        if source not in allowed_sources:
            return True
    return False


def has_default_tls_server(config: str) -> bool:
    for line in config.splitlines():
        stripped = line.strip()
        if not stripped.startswith("listen") or "default_server" not in stripped:
            continue
        if re.search(r"(?:^|\s)(?:\[\:\:\]|(?:\*|[0-9.]+)?):?443(?:\s|;)", stripped):
            return True
        if re.search(r"(?:^|\s)443(?:\s|;)", stripped):
            return True
    return False


def make_http_nginx_config(address: ipaddress._BaseAddress, ipv6: bool) -> str:
    listen = "listen [::]:80;" if address.version == 6 else "listen 80;"
    if address.version == 6 and not ipv6:
        raise DeployError("Невозможно слушать IPv6-порт 80: IPv6 отключён.")
    return f"""{MANAGED_MARKER}
server {{
    {listen}
    server_name {address};

    location ^~ /.well-known/acme-challenge/ {{
        root /var/www/letsencrypt;
        default_type text/plain;
        try_files $uri =404;
    }}

    location = /comments.json {{
        return 404;
    }}

    location /api/ {{
        proxy_pass http://127.0.0.1:3000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }}

    location / {{
        root /opt/coffee-shop/public;
        try_files $uri $uri/ /index.html;
    }}
}}
"""


def make_https_nginx_config(address: ipaddress._BaseAddress, ipv6: bool) -> str:
    if address.version == 6 and not ipv6:
        raise DeployError("Невозможно слушать IPv6-порт 443: IPv6 отключён.")
    listen_http = "listen [::]:80;" if address.version == 6 else "listen 80;"
    listen_tls = (
        "listen [::]:443 ssl default_server;"
        if address.version == 6
        else "listen 443 ssl default_server;"
    )
    return f"""{MANAGED_MARKER}
server {{
    {listen_http}
    server_name {address};

    location ^~ /.well-known/acme-challenge/ {{
        root /var/www/letsencrypt;
        default_type text/plain;
        try_files $uri =404;
    }}

    location / {{
        return 301 https://$host$request_uri;
    }}
}}

server {{
    {listen_tls}
    server_name _;

    ssl_certificate /etc/letsencrypt/live/{CERT_NAME}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/{CERT_NAME}/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;

    location = /comments.json {{
        return 404;
    }}

    location /api/ {{
        proxy_pass http://127.0.0.1:3000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }}

    location / {{
        root /opt/coffee-shop/public;
        try_files $uri $uri/ /index.html;
    }}
}}
"""


def make_app_unit(node: str) -> str:
    return f"""{MANAGED_MARKER}
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
ExecStart={node} /opt/coffee-shop/dist/main.js
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/coffee-shop/comments.json

[Install]
WantedBy=multi-user.target
"""


def make_renew_unit() -> str:
    return f"""{MANAGED_MARKER}
[Unit]
Description=Renew Let's Encrypt IP certificate
Wants=network-online.target
After=network-online.target nginx.service

[Service]
Type=oneshot
ExecStart={CERTBOT_BIN} renew -q
"""


def make_renew_timer() -> str:
    return f"""{MANAGED_MARKER}
[Unit]
Description=Run Certbot renewal checks twice daily

[Timer]
OnCalendar=*-*-* 00,12:00:00
RandomizedDelaySec=30m
Persistent=true

[Install]
WantedBy=timers.target
"""


def make_deploy_hook() -> str:
    nginx = shutil.which("nginx") or "/usr/sbin/nginx"
    systemctl = shutil.which("systemctl") or "/bin/systemctl"
    return f"""#!/bin/sh
{MANAGED_MARKER}
set -eu
{nginx} -t
{systemctl} reload nginx
"""


def cert_path(name: str) -> Path:
    return Path("/etc/letsencrypt/live") / name / "fullchain.pem"


def certificate_matches_ip(cert: Path, address: ipaddress._BaseAddress) -> bool:
    if not cert.is_file():
        return False
    result = run(
        ["openssl", "x509", "-in", str(cert), "-noout", "-checkip", str(address)],
        check=False,
        timeout=20,
    )
    if result.returncode != 0:
        raise DeployError(
            f"Сертификат {cert} уже существует, но не подтверждает IP {address}; "
            "он не будет перезаписан."
        )
    valid = run(
        ["openssl", "x509", "-in", str(cert), "-noout", "-checkend", "0"],
        check=False,
        timeout=20,
    )
    if valid.returncode != 0:
        raise DeployError(
            f"Сертификат {cert} просрочен; автоматическая замена остановлена."
        )
    return True


def staging_certificate_state(
    cert: Path, address: ipaddress._BaseAddress
) -> tuple[bool, bool]:
    """Возвращает (существует, действителен) для staging-сертификата."""
    if not cert.is_file():
        return False, False
    ip_check = run(
        ["openssl", "x509", "-in", str(cert), "-noout", "-checkip", str(address)],
        check=False,
        timeout=20,
    )
    if ip_check.returncode != 0:
        raise DeployError(
            f"Staging-сертификат {cert} выпущен для другого IP; он не будет перезаписан."
        )
    valid = run(
        ["openssl", "x509", "-in", str(cert), "-noout", "-checkend", "0"],
        check=False,
        timeout=20,
    )
    return True, valid.returncode == 0


def certbot_args(email: str | None) -> list[str]:
    if email:
        return ["--email", email, "--no-eff-email"]
    return ["--register-unsafely-without-email"]


def certonly_command(
    address: ipaddress._BaseAddress,
    email: str | None,
    *,
    staging: bool,
    cert_name: str,
    force_renewal: bool = False,
) -> list[str]:
    command = [
        str(CERTBOT_BIN),
        "certonly",
    ]
    if staging:
        command.append("--staging")
    if force_renewal:
        command.append("--force-renewal")
    command.extend(
        [
            "--preferred-profile",
            "shortlived",
            "--webroot",
            "--webroot-path",
            str(ACME_ROOT),
            "--ip-address",
            str(address),
            "--cert-name",
            cert_name,
            "--non-interactive",
            "--agree-tos",
            *certbot_args(email),
        ]
    )
    return command


def confirm_production_issue(args: argparse.Namespace) -> None:
    if args.confirm_production:
        return
    if not sys.stdin.isatty():
        raise DeployError(
            "Для production-сертификата повторно запустите скрипт с "
            "--confirm-production после staging-проверки."
        )
    answer = input(
        "Staging-проверка пройдена. Будет запрошен доверенный IP-сертификат "
        "Let’s Encrypt. Введите ISSUE: "
    ).strip()
    if answer != "ISSUE":
        raise DeployError("Запрос production-сертификата отменён.")


def local_http_request(path: str, address: ipaddress._BaseAddress) -> tuple[int, bytes, str]:
    host = str(address)
    connection_host = "127.0.0.1" if address.version == 4 else "::1"
    connection = http.client.HTTPConnection(connection_host, 80, timeout=20)
    try:
        connection.request("GET", path, headers={"Host": host, "Connection": "close"})
        response = connection.getresponse()
        body = response.read(2_000_000)
        return response.status, body, response.getheader("Location", "")
    finally:
        connection.close()


def check_local_api() -> None:
    try:
        with urllib.request.urlopen("http://127.0.0.1:3000/api/comments", timeout=20) as response:
            body = json.loads(response.read(2_000_000).decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise DeployError(f"Локальная проверка API NestJS не пройдена: {exc}") from exc
    if not isinstance(body, list):
        raise DeployError("API /api/comments вернул не JSON-массив.")


def prepare_acme_webroot() -> Path:
    challenge_dir = ACME_ROOT / ".well-known" / "acme-challenge"
    for path in (Path("/var/www"), ACME_ROOT, ACME_ROOT / ".well-known", challenge_dir):
        if path.is_symlink():
            raise DeployError(f"Каталог ACME является символической ссылкой: {path}")
        if path.exists() and not path.is_dir():
            raise DeployError(f"Путь ACME занят не каталогом: {path}")
        path.mkdir(parents=True, exist_ok=True)
    os.chown(ACME_ROOT, 0, 0)
    os.chmod(ACME_ROOT, 0o755)
    return challenge_dir


def check_acme_route(address: ipaddress._BaseAddress) -> None:
    challenge_dir = ACME_ROOT / ".well-known" / "acme-challenge"
    for path in (ACME_ROOT, ACME_ROOT / ".well-known", challenge_dir):
        if path.is_symlink() or not path.is_dir():
            raise DeployError(f"Каталог ACME изменился или небезопасен: {path}")
    token = f"deploy-probe-{os.getpid()}"
    probe = challenge_dir / token
    if probe.exists():
        raise DeployError(f"Пробный ACME-файл уже существует и не будет перезаписан: {probe}")
    probe.write_text("coffee-shop-acme-probe\n", encoding="ascii")
    try:
        status, body, _ = local_http_request(f"/.well-known/acme-challenge/{token}", address)
        if status != 200 or body != b"coffee-shop-acme-probe\n":
            raise DeployError(
                "HTTP-01 путь Nginx не отдаёт пробный файл из "
                "/var/www/letsencrypt/.well-known/acme-challenge."
            )
    finally:
        try:
            probe.unlink()
        except FileNotFoundError:
            pass


def check_static_and_proxy(address: ipaddress._BaseAddress) -> None:
    status, body, _ = local_http_request("/", address)
    if status != 200 or b"<!DOCTYPE html>" not in body[:500].upper():
        if status != 200 or b"<html" not in body[:1000].lower():
            raise DeployError("Nginx не вернул страницу public/index.html по HTTP.")
    status, body, _ = local_http_request("/api/comments", address)
    if status != 200:
        raise DeployError(f"Nginx proxy /api/comments вернул HTTP {status}.")
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DeployError("Nginx proxy /api/comments вернул некорректный JSON.") from exc
    if not isinstance(data, list):
        raise DeployError("Nginx proxy /api/comments должен возвращать JSON-массив.")


def check_https_local(address: ipaddress._BaseAddress) -> None:
    connect_host = "127.0.0.1" if address.version == 4 else "::1"
    family = socket.AF_INET if address.version == 4 else socket.AF_INET6
    raw = socket.socket(family, socket.SOCK_STREAM)
    raw.settimeout(20)
    tls: ssl.SSLSocket | None = None
    try:
        raw.connect((connect_host, 443))
        context = ssl.create_default_context()
        tls = context.wrap_socket(raw, server_hostname=str(address))
        request_host = (
            f"[{address}]" if address.version == 6 else str(address)
        )
        tls.sendall(
            (
                "GET /api/comments HTTP/1.1\r\n"
                f"Host: {request_host}\r\n"
                "Connection: close\r\n\r\n"
            ).encode("ascii")
        )
        response = http.client.HTTPResponse(tls)
        response.begin()
        body = response.read(2_000_000)
        if response.status != 200:
            raise DeployError(f"HTTPS proxy /api/comments вернул HTTP {response.status}.")
        if not isinstance(json.loads(body.decode("utf-8")), list):
            raise DeployError("HTTPS API вернул не JSON-массив.")
    except (OSError, ssl.SSLError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DeployError(
            "Локальная проверка HTTPS/сертификата не пройдена: "
            f"{exc}. Не отключайте проверку TLS."
        ) from exc
    finally:
        if tls is not None:
            tls.close()
        else:
            raw.close()

    status, body, _ = local_http_request("/comments.json", address)
    if status != 404:
        raise DeployError(f"Ожидался HTTP 404 для /comments.json, получен {status}.")
    status, _, location = local_http_request("/", address)
    if status != 301 or not location.startswith("https://"):
        raise DeployError("HTTP не перенаправляет сайт на HTTPS.")


def ensure_site_link() -> None:
    NGINX_ENABLED.parent.mkdir(parents=True, exist_ok=True)
    expected = NGINX_AVAILABLE
    if NGINX_ENABLED.is_symlink():
        if NGINX_ENABLED.resolve() != expected.resolve():
            raise DeployError(
                f"{NGINX_ENABLED} уже указывает на другой сайт; ссылка не изменена."
            )
        return
    if NGINX_ENABLED.exists():
        raise DeployError(
            f"{NGINX_ENABLED} уже занят обычным файлом; он не будет заменён."
        )
    NGINX_ENABLED.symlink_to(expected)


def validate_node_modules(path: Path) -> None:
    """Проверяет каталог npm-зависимостей и допускает только внутренние ссылки."""
    if path.is_symlink() or not path.is_dir():
        raise DeployError(f"Каталог зависимостей должен быть обычным каталогом: {path}")
    root = path.resolve()
    for entry in path.rglob("*"):
        if not entry.is_symlink():
            continue
        try:
            target = entry.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise DeployError(f"Символическая ссылка npm-зависимости повреждена: {entry}") from exc
        if target != root and root not in target.parents:
            raise DeployError(
                f"Символическая ссылка npm-зависимости выходит за каталог node_modules: {entry}"
            )


def copy_project_to_stage(source: Path, stage: Path) -> None:
    for name in SOURCE_FILES:
        shutil.copy2(source / name, stage / name)
    for name in SOURCE_DIRS:
        shutil.copytree(source / name, stage / name, symlinks=False)
    shutil.chown(stage, user=APP_USER, group=APP_GROUP)
    for path in stage.rglob("*"):
        if path.is_symlink():
            raise DeployError(f"Символическая ссылка в каталоге сборки: {path}")
        if path.is_dir():
            shutil.chown(path, user=APP_USER, group=APP_GROUP)
            os.chmod(path, 0o755)
        else:
            shutil.chown(path, user=APP_USER, group=APP_GROUP)
            os.chmod(path, 0o644)


def build_project(source: Path, npm: str) -> Path:
    if not BUILD_DIR.is_dir():
        raise DeployError(f"Не найден временный каталог для сборки: {BUILD_DIR}")
    stage = Path(tempfile.mkdtemp(prefix="coffee-shop-build-", dir=str(BUILD_DIR)))
    try:
        copy_project_to_stage(source, stage)
        run(
            [
                "runuser",
                "-u",
                APP_USER,
                "--",
                "env",
                "HOME=/var/lib/coffeeshop",
                "NODE_ENV=development",
                npm,
                "ci",
                "--include=dev",
            ],
            cwd=stage,
            timeout=900,
        )
        run(
            [
                "runuser",
                "-u",
                APP_USER,
                "--",
                "env",
                "HOME=/var/lib/coffeeshop",
                "NODE_ENV=development",
                npm,
                "run",
                "build",
            ],
            cwd=stage,
            timeout=600,
        )
        entry = stage / "dist" / "main.js"
        if not entry.is_file():
            raise DeployError("После npm run build не создан dist/main.js.")
        run(
            [
                "runuser",
                "-u",
                APP_USER,
                "--",
                "env",
                "HOME=/var/lib/coffeeshop",
                "NODE_ENV=production",
                npm,
                "prune",
                "--omit=dev",
            ],
            cwd=stage,
            timeout=600,
        )
        if not (stage / "node_modules").is_dir():
            raise DeployError("После npm prune не найден каталог node_modules.")
        validate_node_modules(stage / "node_modules")
        return stage
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def copy_managed_app(source: Path, stage: Path, backups: BackupManager) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    if APP_DIR.is_symlink() or not APP_DIR.is_dir():
        raise DeployError(f"Каталог приложения должен быть обычным каталогом: {APP_DIR}")
    for item in APP_MANAGED_ITEMS:
        path = APP_DIR / item
        if path.is_symlink():
            raise DeployError(f"Управляемый путь в приложении является ссылкой: {path}")
        if item == "node_modules" and path.exists():
            if not path.is_dir():
                raise DeployError(f"Ожидался каталог зависимостей: {path}")
            validate_node_modules(path)
        if path.is_dir() and item in (*SOURCE_DIRS, "dist"):
            for nested in path.rglob("*"):
                if nested.is_symlink():
                    raise DeployError(
                        f"Найден вложенный символический путь в каталоге приложения: {nested}"
                    )

    current_comments = APP_DIR / DATA_FILE
    if current_comments.exists():
        try:
            existing = json.loads(current_comments.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DeployError(
                "Текущий comments.json не является корректным JSON; файл сохранён, "
                "обновление остановлено."
            ) from exc
        if not isinstance(existing, list):
            raise DeployError(
                "Текущий comments.json не содержит массив; файл сохранён, обновление остановлено."
            )
    backups.backup_app(APP_DIR)

    for name in SOURCE_FILES:
        shutil.copy2(stage / name, APP_DIR / name)
    for name in SOURCE_DIRS:
        shutil.copytree(stage / name, APP_DIR / name, dirs_exist_ok=True, symlinks=False)
    shutil.copytree(stage / "dist", APP_DIR / "dist", dirs_exist_ok=True, symlinks=False)
    installed_modules = APP_DIR / "node_modules"
    if installed_modules.exists():
        shutil.rmtree(installed_modules)
    shutil.copytree(stage / "node_modules", installed_modules, symlinks=True)

    if current_comments.exists():
        pass
    else:
        shutil.copy2(source / DATA_FILE, current_comments)

    gid = get_group_gid()
    os.chown(APP_DIR, 0, gid)
    os.chmod(APP_DIR, 0o755)
    for item in (*SOURCE_FILES, *SOURCE_DIRS, "dist", "node_modules"):
        root = APP_DIR / item
        if root.is_dir():
            os.chown(root, 0, gid)
            os.chmod(root, 0o755)
            for path in root.rglob("*"):
                if path.is_symlink():
                    if item == "node_modules":
                        continue
                    raise DeployError(f"Символическая ссылка в каталоге приложения: {path}")
                if path.is_dir():
                    os.chown(path, 0, gid)
                    os.chmod(path, 0o755)
                else:
                    os.chown(path, 0, gid)
                    os.chmod(path, 0o644)
        elif root.is_file():
            os.chown(root, 0, gid)
            os.chmod(root, 0o644)
    os.chown(current_comments, get_app_uid(), gid)
    os.chmod(current_comments, 0o640)


def get_app_uid() -> int:
    import pwd

    try:
        return pwd.getpwnam(APP_USER).pw_uid
    except (ImportError, KeyError) as exc:
        raise DeployError(f"Не удалось получить UID пользователя {APP_USER}.") from exc


def write_app_unit(node: str, backups: BackupManager) -> None:
    install_managed_file(
        SYSTEMD_APP,
        make_app_unit(node),
        backups,
        compatible=("Description=Coffee Shop NestJS application", "WorkingDirectory=/opt/coffee-shop"),
    )
    was_active = unit_active("coffee-shop.service")
    run(["systemctl", "daemon-reload"], timeout=60)
    run(["systemctl", "enable", "coffee-shop.service"], timeout=60)
    run(
        ["systemctl", "restart" if was_active else "start", "coffee-shop.service"],
        timeout=90,
    )
    run(["systemctl", "is-active", "--quiet", "coffee-shop.service"], timeout=20)
    check_local_api()
    say("NestJS отвечает на 127.0.0.1:3000/api/comments.")


def write_http_site(
    address: ipaddress._BaseAddress,
    backups: BackupManager,
    ipv6: bool,
) -> None:
    prepare_acme_webroot()
    install_managed_file(
        NGINX_AVAILABLE,
        make_http_nginx_config(address, ipv6),
        backups,
        compatible=("root /opt/coffee-shop/public;", "proxy_pass http://127.0.0.1:3000;"),
    )
    ensure_site_link()
    run(["nginx", "-t"], timeout=45)
    run(["systemctl", "enable", "--now", "nginx.service"], timeout=90)
    run(["systemctl", "reload", "nginx.service"], timeout=60)
    check_acme_route(address)
    check_static_and_proxy(address)


def install_https_and_renewal(
    address: ipaddress._BaseAddress,
    email: str | None,
    args: argparse.Namespace,
    backups: BackupManager,
    ipv6: bool,
) -> None:
    production_cert_valid = certificate_matches_ip(cert_path(CERT_NAME), address)
    if not production_cert_valid:
        staging_cert = cert_path(STAGING_CERT_NAME)
        staging_exists, staging_valid = staging_certificate_state(staging_cert, address)
        if not staging_valid:
            run(
                certonly_command(
                    address,
                    email,
                    staging=True,
                    cert_name=STAGING_CERT_NAME,
                    force_renewal=staging_exists,
                ),
                timeout=900,
            )
    if not production_cert_valid:
        confirm_production_issue(args)
        run(
            certonly_command(
                address,
                email,
                staging=False,
                cert_name=CERT_NAME,
            ),
            timeout=900,
        )
    if not certificate_matches_ip(cert_path(CERT_NAME), address):
        raise DeployError("Production-сертификат отсутствует или не соответствует IP.")

    nginx_config = current_nginx_config()
    if has_foreign_tls_listener(nginx_config):
        raise DeployError(
            "В Nginx уже есть отдельный TLS-vhost на 443. Для IP-сертификата нужен "
            "однозначный default TLS-vhost; существующую конфигурацию не изменяю. "
            "Разрешите конфликт вручную и повторите запуск."
        )
    install_managed_file(
        NGINX_AVAILABLE,
        make_https_nginx_config(address, ipv6),
        backups,
        compatible=("root /opt/coffee-shop/public;", "proxy_pass http://127.0.0.1:3000;"),
    )
    run(["nginx", "-t"], timeout=45)
    run(["systemctl", "reload", "nginx.service"], timeout=60)

    hook_content = make_deploy_hook()
    install_managed_file(
        DEPLOY_HOOK,
        hook_content,
        backups,
        compatible=("/usr/sbin/nginx -t", "reload nginx"),
        mode=0o755,
    )
    os.chmod(DEPLOY_HOOK, 0o755)
    install_managed_file(
        SYSTEMD_RENEW,
        make_renew_unit(),
        backups,
        compatible=("Description=Renew Let's Encrypt IP certificate", str(CERTBOT_BIN)),
    )
    install_managed_file(
        SYSTEMD_TIMER,
        make_renew_timer(),
        backups,
        compatible=("Description=Run Certbot renewal checks twice daily", "OnCalendar=*-*-* 00,12:00:00"),
    )
    if unit_enabled("certbot.timer") or unit_active("certbot.timer"):
        raise DeployError(
            "Обнаружен системный certbot.timer. Чтобы не запустить два механизма "
            "продления, согласуйте их вручную перед включением certbot-renew.timer."
        )
    run(["systemctl", "daemon-reload"], timeout=60)
    run(["systemctl", "enable", "--now", "certbot-renew.timer"], timeout=60)
    run(
        [str(CERTBOT_BIN), "renew", "--cert-name", CERT_NAME, "--dry-run"],
        timeout=900,
    )
    check_https_local(address)
    run(["systemctl", "is-active", "--quiet", "coffee-shop.service"], timeout=20)
    run(["systemctl", "is-active", "--quiet", "nginx.service"], timeout=20)
    run(["systemctl", "is-enabled", "--quiet", "certbot-renew.timer"], timeout=20)
    run(["ss", "-ltnp"], timeout=20)


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Автоматическое развёртывание Coffee Shop на Debian 12."
    )
    parser.add_argument("--dry-run", action="store_true", help="Показать план без команд и изменений.")
    parser.add_argument("--apply", action="store_true", help="Разрешить системные изменения.")
    parser.add_argument("--public-ip", help="Фактический публичный IPv4- или IPv6-адрес VPS.")
    parser.add_argument("--email", help="Email для уведомлений Let’s Encrypt; секреты не используются.")
    parser.add_argument(
        "--confirm-host-access",
        action="store_true",
        help="Подтвердить открытые в сетевом firewall порты TCP 80/443 и сохранность SSH-доступа.",
    )
    parser.add_argument(
        "--confirm-production",
        action="store_true",
        help="Подтвердить выпуск доверенного production IP-сертификата Let’s Encrypt.",
    )
    parser.add_argument(
        "--full-upgrade",
        action="store_true",
        help="Дополнительно выполнить apt-get full-upgrade -y после интерактивного подтверждения.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = make_parser()
    args = parser.parse_args(argv)
    source = Path(__file__).resolve().parent
    try:
        validate_source(source)
        address = parse_public_ip(args.public_ip, prompt=not args.dry_run)
        email = parse_email(args.email, prompt=not args.dry_run)
        if args.dry_run:
            if args.apply:
                raise DeployError("--dry-run нельзя совмещать с --apply.")
            return print_dry_run(source, address, email)
        if not args.apply:
            raise DeployError(
                "Скрипт ничего не меняет без --apply. Сначала используйте --dry-run."
            )
        if not args.confirm_host_access:
            raise DeployError(
                "Перед применением подтвердите порты 80/443 и сохранность SSH через "
                "--confirm-host-access. Скрипт не меняет firewall."
            )
        if not sys.stdin.isatty() and not args.confirm_production:
            production_cert = cert_path(CERT_NAME)
            if not production_cert.exists():
                raise DeployError(
                    "Нужен терминал для подтверждения production-сертификата либо "
                    "передайте --confirm-production."
                )

        validate_host(address)
        backups = BackupManager()
        say("Этап 1/8. Проверка сервисов и существующих конфигураций.")
        for name in ("apt-get", "systemctl", "useradd", "groupadd", "runuser"):
            require_command(name)

        say("Этап 2/8. Установка системных зависимостей.")
        install_base_packages(args.full_upgrade)
        for name in ("nginx", "ss", "openssl", "systemctl", "runuser"):
            require_command(name)
        check_port_3000()
        ipv6 = nginx_ipv6_enabled()
        if address.version == 6 and not ipv6:
            raise DeployError("На сервере отключён IPv6.")
        if shutil.which("nginx"):
            existing_nginx = current_nginx_config()
            if has_foreign_tls_listener(existing_nginx) or (
                has_default_tls_server(existing_nginx)
                and not NGINX_AVAILABLE.is_file()
            ):
                raise DeployError(
                    "На сервере уже настроен другой TLS-vhost для 443. "
                    "Существующая конфигурация сохранена; разрешите конфликт вручную."
                )
        if unit_enabled("certbot.timer") or unit_active("certbot.timer"):
            raise DeployError(
                "Обнаружен системный certbot.timer. Чтобы не запустить два механизма "
                "продления, согласуйте их вручную до установки приложения."
            )

        say("Этап 3/8. Установка Node.js 24 и отдельного Certbot.")
        npm = ensure_node24()
        ensure_app_account(backups)
        ensure_certbot()

        say("Этап 4/8. Сборка приложения во временном каталоге.")
        stage = build_project(source, npm)
        active_before = unit_active("coffee-shop.service")
        try:
            if active_before:
                run(["systemctl", "stop", "coffee-shop.service"], timeout=60)
            say("Этап 5/8. Резервное копирование и установка собранного приложения.")
            copy_managed_app(source, stage, backups)
            node = shutil.which("node")
            if not node:
                raise DeployError("После установки не найдена команда node.")
            write_app_unit(node, backups)
        except Exception:
            if active_before and not unit_active("coffee-shop.service"):
                try:
                    say("Попытка вернуть ранее активную службу после ошибки обновления.")
                    run(["systemctl", "start", "coffee-shop.service"], timeout=90)
                except Exception as restart_error:
                    say(f"Не удалось повторно запустить службу: {restart_error}")
            raise
        finally:
            shutil.rmtree(stage, ignore_errors=True)

        say("Этап 6/8. HTTP, статика, API и проверка ACME webroot.")
        write_http_site(address, backups, ipv6)

        say("Этап 7/8. Staging и production-сертификаты, HTTPS.")
        install_https_and_renewal(address, email, args, backups, ipv6)

        say("Этап 8/8. Результат.")
        say("Развёртывание завершено; API доступно за Nginx, Node слушает loopback.")
        say(f"Адрес: https://{f'[{address}]' if address.version == 6 else address}/")
        say(f"Резервные копии: {backups.root if backups.created else 'не потребовались'}")
        say("Логи: journalctl -u coffee-shop -n 100 --no-pager")
        say("Nginx: journalctl -u nginx -n 100 --no-pager")
        say("Certbot: journalctl -u certbot-renew.service -n 100 --no-pager")
        return 0
    except DeployError as exc:
        say(f"ОШИБКА: {exc}")
        say("Критическая операция остановлена; проверьте сообщение и журналы перед повтором.")
        return 1
    except KeyboardInterrupt:
        say("Остановлено пользователем. Уже созданные резервные копии сохранены.")
        return 130
    except Exception as exc:  # noqa: BLE001 - последняя граница для понятной диагностики CLI
        say(f"НЕОЖИДАННАЯ ОШИБКА: {type(exc).__name__}: {exc}")
        say("Проверьте состояние служб и резервные копии перед повторным запуском.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
