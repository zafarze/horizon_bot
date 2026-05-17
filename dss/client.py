"""DSS Pro V8.4.0 HTTP клиент.

ВНИМАНИЕ: точная схема MD5-подписи login зависит от DSS Open API Reference (PDF от дилера).
Здесь — типичный двухэтапный auth-скелет. Заполнить TODO после получения PDF.
"""
from __future__ import annotations

import asyncio
import hashlib
import socket
import ssl
from typing import Any

import aiohttp
from loguru import logger

# Эмпирически на этом DSS-инстансе токен инвалидируется через ~70с простоя
# (см. логи: login → первый 401 на /obms через 71с). Дёргаем keepalive чаще,
# с запасом, чтобы не ловить 401 на штатных запросах.
KEEPALIVE_INTERVAL = 45


class DSSAuthError(RuntimeError):
    pass


class DSSAuthFatal(DSSAuthError):
    """Терминальная ошибка авторизации (неверный пароль, заблокированная учётка).
    Ретрай только усугубит — нужно вмешательство человека."""
    pass


class DSSSessionConflict(DSSAuthError):
    """code=2004 'The user has logged in' — старая сессия (того же user/ip/
    clientType) ещё активна на DSS. Логаут без токена невозможен; нужно
    подождать, пока она протухнет по таймауту на сервере (обычно ~70с)."""
    pass


# Коды ответа DSS, при которых ретраить login() бессмысленно и опасно
# (каждая попытка приближает блокировку учётки).
_FATAL_LOGIN_CODES = {
    2001,  # Incorrect username or password
    2002,  # Account locked
    2003,  # Account disabled / does not exist
}


class DSSClient:
    def __init__(
        self,
        base_url: str,
        user: str,
        password: str,
        client_type: str = "WINPC_V2",
    ):
        self.base_url = base_url.rstrip("/")
        self.user = user
        self.password = password
        self.client_type = client_type

        self._session: aiohttp.ClientSession | None = None
        self._token: str | None = None
        self._auth_disabled: DSSAuthFatal | None = None
        self._user_id: str | None = None
        self._lock = asyncio.Lock()
        self._keepalive_task: asyncio.Task | None = None

    # --- lifecycle -------------------------------------------------------
    async def start(self) -> None:
        # DSS использует самоподписанный сертификат и слабый DH-параметр (<1024 бит),
        # поэтому отключаем верификацию и понижаем SECLEVEL OpenSSL до 0,
        # иначе handshake падает с DH_KEY_TOO_SMALL.
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE
        ssl_ctx.set_ciphers("DEFAULT@SECLEVEL=0")
        connector = aiohttp.TCPConnector(ssl=ssl_ctx)
        timeout = aiohttp.ClientTimeout(total=30, connect=10)
        self._session = aiohttp.ClientSession(connector=connector, timeout=timeout)

    async def stop(self) -> None:
        if self._keepalive_task and not self._keepalive_task.done():
            self._keepalive_task.cancel()
        if self._session:
            await self._session.close()
            self._session = None

    @property
    def session(self) -> aiohttp.ClientSession:
        assert self._session is not None, "DSSClient.start() не вызван"
        return self._session

    @property
    def token(self) -> str | None:
        return self._token

    # --- helpers ---------------------------------------------------------
    @staticmethod
    def _local_ip() -> str:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))
                return s.getsockname()[0]
        except Exception:
            return "127.0.0.1"

    @staticmethod
    def _md5(s: str) -> str:
        return hashlib.md5(s.encode("utf-8")).hexdigest()

    # --- auth ------------------------------------------------------------
    async def login(self) -> None:
        """Двухэтапный логин DSS Pro.

        TODO(API_GUIDE): уточнить по DSS Open API Reference V8.4.0 следующее:
            1) Точный путь второго запроса (часто `/brms/api/v1.0/accounts/authorize`
               повторяется со signature, либо `/brms/api/v1.0/accounts/login`).
            2) Точная формула signature. Типичная схема DSS:
                 first  = MD5(password)
                 second = MD5(user + first)
                 third  = MD5(second + randomKey)
                 sig    = MD5(user + realm + third)
               но порядок и набор полей зависит от encryptType.
            3) Какие поля возвращаются в успешном ответе (token, userId, expires).
            4) Какие заголовки требуются для последующих запросов
               (обычно `X-Subject-Token` или Cookie с tokenId).
        """
        async with self._lock:
            # Если предыдущая попытка получила терминальный отказ —
            # не стучимся в DSS повторно (иначе сжигаем unlockRemainTimes).
            if self._auth_disabled is not None:
                raise self._auth_disabled

            ip = self._local_ip()

            # --- этап 1: получить realm/randomKey/publickey ---
            step1_url = f"{self.base_url}/brms/api/v1.0/accounts/authorize"
            step1_payload = {
                "userName": self.user,
                "ipAddress": ip,
                "clientType": self.client_type,
            }
            logger.debug("DSS login step1 → {}", step1_url)
            async with self.session.post(step1_url, json=step1_payload) as resp:
                # DSS на step1 обычно возвращает 401 с challenge в теле — это нормально.
                data = await resp.json(content_type=None)
                logger.debug("DSS login step1 status={} body={}", resp.status, data)

            realm = data.get("realm") or data.get("Realm")
            random_key = data.get("randomKey") or data.get("RandomKey")
            encrypt_type = data.get("encryptType") or data.get("EncryptType") or "MD5"

            if not realm or not random_key:
                raise DSSAuthError(
                    f"DSS step1: отсутствуют realm/randomKey в ответе: {data}"
                )

            # --- этап 2: signed login ---
            # Формула DSS Pro V8 (HTTP Digest-стиль), подтверждена в реальных
            # интеграциях Dahua DSS (см. mwandotheboss/Dahua-DSS-Integration):
            #     temp1 = MD5(password)
            #     temp2 = MD5(user + temp1)
            #     temp3 = MD5(temp2)
            #     temp4 = MD5(user + ":" + realm + ":" + temp3)
            #     signature = MD5(temp4 + ":" + randomKey)
            temp1 = self._md5(self.password)
            temp2 = self._md5(self.user + temp1)
            temp3 = self._md5(temp2)
            temp4 = self._md5(f"{self.user}:{realm}:{temp3}")
            signature = self._md5(f"{temp4}:{random_key}")

            step2_payload = {
                "userName": self.user,
                "ipAddress": ip,
                "clientType": self.client_type,
                "userType": "0",
                "signature": signature,
                "randomKey": random_key,
                "encryptType": encrypt_type,
            }
            logger.debug("DSS login step2 → {}", step1_url)
            async with self.session.post(step1_url, json=step2_payload) as resp:
                if resp.status >= 400:
                    body = await resp.text()
                    raise DSSAuthError(f"DSS login step2 HTTP {resp.status}: {body}")
                result = await resp.json(content_type=None)

            # DSS возвращает прикладной код ошибки в теле даже при HTTP 200.
            code = result.get("code")
            if code in _FATAL_LOGIN_CODES:
                fatal = DSSAuthFatal(
                    f"DSS login отклонён (code={code}, desc={result.get('desc')!r}, "
                    f"data={result.get('data')}). "
                    f"Проверьте логин/пароль в .env. "
                    f"Бот не будет ретраить, чтобы не заблокировать учётку."
                )
                self._auth_disabled = fatal
                raise fatal

            if code == 2004:
                raise DSSSessionConflict(
                    "DSS говорит 'The user has logged in' (code=2004). "
                    "Старая сессия предыдущего процесса ещё активна; "
                    "ждём её таймаута на сервере."
                )

            # TODO(API_GUIDE): уточнить имя поля. Часто `token`, `tokenId`, `subjectToken`.
            self._token = (
                result.get("token")
                or result.get("tokenId")
                or result.get("subjectToken")
            )
            self._user_id = result.get("userId") or result.get("user_id")

            if not self._token:
                raise DSSAuthError(f"DSS login: не получен token: {result}")

            logger.info("DSS login OK, userId={}", self._user_id)

            if self._keepalive_task is None or self._keepalive_task.done():
                self._keepalive_task = asyncio.create_task(self._keepalive_loop())

    async def _keepalive_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(KEEPALIVE_INTERVAL)
                await self.keepalive()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("DSS keepalive error: {}", e)
                await asyncio.sleep(5)

    async def keepalive(self) -> None:
        """Продление сессии. TODO(API_GUIDE): уточнить эндпоинт.
        Часто: PUT /brms/api/v1.0/accounts/keepalive с заголовком token."""
        if not self._token:
            await self.login()
            return
        url = f"{self.base_url}/brms/api/v1.0/accounts/keepalive"
        try:
            async with self.session.put(url, headers=self._auth_headers()) as resp:
                if resp.status == 401:
                    logger.info("DSS keepalive → 401, re-login")
                    self._token = None
                    await self.login()
                else:
                    logger.debug("DSS keepalive status={}", resp.status)
        except aiohttp.ClientError as e:
            logger.warning("DSS keepalive network error: {}", e)

    def _auth_headers(self) -> dict[str, str]:
        # TODO(API_GUIDE): точное имя заголовка из PDF.
        return {
            "X-Subject-Token": self._token or "",
            "Content-Type": "application/json",
        }

    # --- generic request -------------------------------------------------
    async def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Any:
        if not self._token:
            await self.login()

        url = f"{self.base_url}{path}"
        kwargs: dict[str, Any] = {"headers": self._auth_headers()}
        if json is not None:
            kwargs["json"] = json
        if params:
            kwargs["params"] = params
        if timeout:
            kwargs["timeout"] = aiohttp.ClientTimeout(total=timeout)

        async with self.session.request(method, url, **kwargs) as resp:
            if resp.status == 401:
                logger.info("DSS 401 on {} -> re-login", path)
                self._token = None
                await self.login()
                kwargs["headers"] = self._auth_headers()
                async with self.session.request(method, url, **kwargs) as resp2:
                    resp2.raise_for_status()
                    return await resp2.json(content_type=None)
            resp.raise_for_status()
            return await resp.json(content_type=None)

    async def download_bytes(self, url: str, timeout: float = 30.0) -> bytes:
        """Скачивает произвольный URL через ту же aiohttp-сессию (с auth-заголовком
        и SECLEVEL=0 SSL-контекстом). Используется для картинок DSS, которые
        наружу с публичных Telegram-серверов недоступны."""
        if not self._token:
            await self.login()
        headers = {"X-Subject-Token": self._token or ""}
        async with self.session.get(
            url,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            if resp.status == 401:
                # токен протух — релогинимся и пробуем ещё раз
                self._token = None
                await self.login()
                headers["X-Subject-Token"] = self._token or ""
                async with self.session.get(
                    url, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as resp2:
                    resp2.raise_for_status()
                    return await resp2.read()
            resp.raise_for_status()
            return await resp.read()

    async def ping(self) -> bool:
        """Лёгкая проверка сессии для /dss_ping."""
        try:
            await self.keepalive()
            return self._token is not None
        except Exception as e:
            logger.warning("DSS ping fail: {}", e)
            return False
