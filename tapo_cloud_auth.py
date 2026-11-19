"""
Robust sign-in to the TP-Link/Tapo cloud account for tapo-webcam.

ontapo 0.1.0 is hard-wired to the Asia region (aps1) and sends a very minimal
login request. For European accounts the server then answers e.g. with
-20104 "Parameter doesn't exist". Here the login is built the way the app does it
(and the way the tplink-cloud-api project does):

  1. getAccountStatusAndUrl  -> regional server of the account
  2. login (with all query parameters of the app)
  3. e-mail code (MFA) if required

Several variants (key pairs, with/without region lookup) are tried in turn;
every step is logged without password/token.
The result is stored as an ontapo-compatible session, extended by the detected
region so that device listing and the video relay use the right server as well.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import uuid
from typing import Any, Awaitable, Callable

LOGIN_BASE = "https://n-wap.i.tplinkcloud.com"
APP_TYPE = "TP-Link_Tapo_Android"

# key pairs accepted by the login API (constants of the app, not user secrets)
KEYSETS = {
    "tapo": ("4d11b6b9d5ea4d19a829adbb9714b057", "6ed7d97f3e73467f8a5bab90b577ba4c"),
    "alt":  ("e37525375f8845999bcc56d5e6faa76d", "314bc6700b3140ca80bc655e527cb062"),
}
ERR_MFA_REQUIRED = -20677
ERR_WRONG_CREDENTIALS = -20601
ERR_ACCOUNT_LOCKED = -20675
ERR_WRONG_REGION = -20212


def _redact(obj: Any) -> Any:
    secret = {"token", "refreshtoken", "cloudpassword", "password", "mfaprocessid", "code"}
    if isinstance(obj, dict):
        return {k: ("***" if k.lower() in secret else _redact(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact(v) for v in obj]
    return obj


def region_of(url: str) -> str | None:
    m = re.search(r"(?:^|[/.-])([a-z]{2,3}[a-z]*\d)-(?:wap|app-server|cipc)", url or "")
    return m.group(1) if m else None


class CloudLogin:
    def __init__(self, email: str, password: str, app_version: str, log: Callable[[str], None] = print):
        self.email, self.password = email, password
        self.app_version = app_version
        self.term = uuid.uuid4().hex.upper()
        self.log = log
        from ontapo._http import new_client      # trusts TP-Link's private CA
        self.client = new_client()

    async def close(self):
        await self.client.aclose()

    def _qp(self) -> dict[str, str]:
        return {"appName": APP_TYPE, "appVer": self.app_version, "netType": "wifi",
                "termID": self.term, "ospf": "Android 15", "brand": "TPLINK",
                "locale": "de_DE", "model": "Pixel 8", "termName": "Pixel 8", "termMeta": "Pixel 8"}

    async def _post(self, base: str, path: str, body: dict, keys: str) -> dict:
        ak, sk = KEYSETS[keys]
        raw = json.dumps(body).encode()
        md5 = base64.b64encode(hashlib.md5(raw).digest()).decode()
        nonce = str(uuid.uuid4())
        sig = hmac.new(sk.encode(), f"{md5}\n9999999999\n{nonce}\n{path}".encode(), hashlib.sha1).hexdigest()
        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "Content-MD5": md5,
            "X-Authorization": f"Timestamp=9999999999, Nonce={nonce}, AccessKey={ak}, Signature={sig}",
            "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 15; Pixel 8 Build/AP3A)",
        }
        r = await self.client.post(base + path, content=raw, params=self._qp(), headers=headers)
        try:
            data = r.json()
        except ValueError:
            data = {"http_status": r.status_code, "text": r.text[:200]}
        self.log(f"  POST {base}{path} [{keys}] -> HTTP {r.status_code} "
                 f"error_code={data.get('error_code')} msg={data.get('msg')!r} "
                 f"result_keys={sorted((data.get('result') or {}).keys())}")
        return data

    async def _regional(self, keys: str) -> str:
        data = await self._post(LOGIN_BASE, "/api/v2/account/getAccountStatusAndUrl",
                                {"appType": APP_TYPE, "cloudUserName": self.email}, keys)
        url = (data.get("result") or {}).get("appServerUrl")
        return url.rstrip("/") if url else LOGIN_BASE

    def _login_body(self) -> dict:
        return {"appType": APP_TYPE, "appVersion": self.app_version,
                "cloudPassword": self.password, "cloudUserName": self.email,
                "platform": "Android 15", "refreshTokenNeeded": True, "supportBindAccount": False,
                "terminalUUID": self.term, "terminalName": "Pixel 8", "terminalMeta": "Pixel 8"}

    async def login(self, mfa: Callable[[], Awaitable[str]]) -> dict:
        """Returns a dict with token, refreshToken, terminalUUID, accountBase, region, appServer."""
        attempts = []
        for keys in ("tapo", "alt"):
            for discover in (True, False):
                attempts.append((keys, discover))
        last = None
        for keys, discover in attempts:
            self.log(f"Login variant: keys={keys}, region lookup={'yes' if discover else 'no'}")
            base = await self._regional(keys) if discover else LOGIN_BASE
            data = await self._post(base, "/api/v2/account/login", self._login_body(), keys)
            res = data.get("result") or {}
            ec = data.get("error_code", 0)
            # wrong region -> the server names the right one
            if not res.get("token") and res.get("appServerUrl") and res["appServerUrl"].rstrip("/") != base:
                base = res["appServerUrl"].rstrip("/")
                data = await self._post(base, "/api/v2/account/login", self._login_body(), keys)
                res, ec = data.get("result") or {}, data.get("error_code", 0)
            if ec in (ERR_WRONG_CREDENTIALS, ERR_ACCOUNT_LOCKED):
                raise RuntimeError(f"Sign-in rejected ({ec}: {data.get('msg')}) – check e-mail/password.")
            if res.get("token"):
                return self._result(res, base, keys)
            if ec == ERR_MFA_REQUIRED or res.get("MFAProcessId"):
                return self._result(await self._mfa(base, keys, res, mfa), base, keys)
            last = data
        raise RuntimeError(f"No login variant succeeded, last response: {json.dumps(_redact(last))[:300]}")

    async def _mfa(self, base: str, keys: str, res: dict, mfa) -> dict:
        proc = res.get("MFAProcessId")
        mfa_type = 2  # e-mail
        if proc:
            await self._post(base, "/api/v2/account/getPushVC4TerminalMFA",
                             {"appType": APP_TYPE, "cloudUserName": self.email, "MFAProcessId": proc,
                              "MFAType": mfa_type, "terminalUUID": self.term}, keys)
        code = await mfa()
        body = {"appType": APP_TYPE, "cloudUserName": self.email, "cloudPassword": self.password,
                "code": code, "terminalUUID": self.term, "terminalName": "Pixel 8",
                "refreshTokenNeeded": True}
        if proc:
            body.update({"MFAProcessId": proc, "MFAType": mfa_type})
        data = await self._post(base, "/api/v2/account/checkMFACodeAndLogin", body, keys)
        r = data.get("result") or {}
        if not r.get("token"):
            raise RuntimeError(f"E-mail code rejected: {json.dumps(_redact(data))[:200]}")
        return r

    def _result(self, res: dict, base: str, keys: str) -> dict:
        region = region_of(base) or region_of(res.get("appServerUrl", "")) or "aps1"
        self.log(f"Login successful (variant {keys}), region: {region}")
        return {"email": self.email, "token": res["token"], "refreshToken": res.get("refreshToken", ""),
                "terminalUUID": self.term, "accountBase": base,
                "appServer": f"{region}-app-server.iot.i.tplinkcloud.com", "region": region}


async def refresh_session(d: dict, app_version: str, log=lambda m: None) -> dict:
    """Renews the token via /api/v2/account/refreshToken; returns the updated dict."""
    cl = CloudLogin(d.get("email", ""), "", app_version, log=log)
    cl.term = d.get("terminalUUID") or cl.term
    try:
        base = d.get("accountBase") or LOGIN_BASE
        for keys in ("tapo", "alt"):
            data = await cl._post(base, "/api/v2/account/refreshToken",
                                  {"appType": APP_TYPE, "refreshToken": d.get("refreshToken", ""),
                                   "terminalUUID": cl.term}, keys)
            r = data.get("result") or {}
            if r.get("token"):
                d = dict(d, token=r["token"], refreshToken=r.get("refreshToken") or d.get("refreshToken", ""))
                return d
        raise RuntimeError(f"Token refresh failed ({data.get('error_code')}) – run `cloud-login` again.")
    finally:
        await cl.close()
