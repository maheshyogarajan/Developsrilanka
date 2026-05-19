"""fiesta.integrations.sf_auth — minimal Salesforce JWT-bearer auth + REST.

Used by fiesta.delivery_ops.automation_runner (Wave 2b SL adapter) to insert
Processing_task__c rows into the Lanka.tax SF org. The Lanka.tax-side
IRD-System-hosting Lambda + Dimuth Docker poller pick them up automatically.

Stdlib only (urllib + json + subprocess fallback). PyJWT is preferred when
available (Fly container will install it via requirements.txt); subprocess
fallback to `sf` CLI is for local CEO-OS dev environments.

Auth precedence:
  1. SF_BEARER_TOKEN env (test/dev short-circuit — pass a live token directly)
  2. SF_JWT_PRIVATE_KEY + SF_CLIENT_ID + SF_USERNAME env (Fly production)
  3. Local sf_jwt_server.key file fallback (CEO-OS Windows dev)
  4. `sf org display --target-org smartertax --json` CLI fallback (CEO-OS dev)

Returns access_token + instance_url tuple. Caching is caller's job.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional, Tuple

SF_LOGIN_URL = "https://login.salesforce.com"
SF_INSTANCE_DEFAULT = "https://smartertax.my.salesforce.com"
SF_API_VERSION = "v62.0"

_LOCAL_JWT_KEY_PATH = "G:/My Drive/CEO OS/sf_jwt_server.key"


class SFAuthError(Exception):
    """Raised when no auth path can produce a usable token."""


def _try_env_bearer() -> Optional[Tuple[str, str]]:
    """Path 1: SF_BEARER_TOKEN + optional SF_INSTANCE_URL env."""
    token = os.environ.get("SF_BEARER_TOKEN")
    if not token:
        return None
    instance = os.environ.get("SF_INSTANCE_URL", SF_INSTANCE_DEFAULT)
    return token, instance


def _read_private_key() -> Optional[str]:
    """Read private key from SF_JWT_PRIVATE_KEY env or local file fallback."""
    key = os.environ.get("SF_JWT_PRIVATE_KEY")
    if key:
        return key
    path = pathlib.Path(_LOCAL_JWT_KEY_PATH)
    if path.exists():
        try:
            return path.read_text(encoding="utf-8")
        except Exception:
            return None
    return None


def _try_jwt_bearer() -> Optional[Tuple[str, str]]:
    """Path 2: JWT-bearer OAuth flow against login.salesforce.com."""
    client_id = os.environ.get("SF_CLIENT_ID")
    username = os.environ.get("SF_USERNAME")
    private_key = _read_private_key()
    if not (client_id and username and private_key):
        return None
    try:
        import jwt as _jwt  # PyJWT — optional dep
    except ImportError:
        return None

    now = int(time.time())
    payload = {
        "iss": client_id,
        "sub": username,
        "aud": SF_LOGIN_URL,
        "exp": now + 300,
    }
    assertion = _jwt.encode(payload, private_key, algorithm="RS256")
    if isinstance(assertion, bytes):
        assertion = assertion.decode()

    body = urllib.parse.urlencode({
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion": assertion,
    }).encode()
    req = urllib.request.Request(
        f"{SF_LOGIN_URL}/services/oauth2/token",
        data=body, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError:
        return None
    except Exception:
        return None
    token = data.get("access_token")
    instance = data.get("instance_url", SF_INSTANCE_DEFAULT)
    if not token:
        return None
    return token, instance


def _try_sf_cli() -> Optional[Tuple[str, str]]:
    """Path 3: `sf org display` CLI fallback (CEO-OS Windows dev only)."""
    for cmd in (["sf.cmd", "org", "display", "--target-org", "smartertax", "--json"],
                ["sf", "org", "display", "--target-org", "smartertax", "--json"]):
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=20, shell=False,
            )
            if result.returncode != 0:
                continue
            data = json.loads(result.stdout)
            token = data.get("result", {}).get("accessToken")
            instance = data.get("result", {}).get("instanceUrl", SF_INSTANCE_DEFAULT)
            if token:
                return token, instance
        except Exception:
            continue
    return None


def get_sf_token() -> Tuple[str, str]:
    """Resolve SF access_token + instance_url via the auth precedence chain.

    Raises SFAuthError if no path succeeds.
    """
    for getter in (_try_env_bearer, _try_jwt_bearer, _try_sf_cli):
        result = getter()
        if result:
            return result
    raise SFAuthError(
        "No SF auth path succeeded. Set SF_BEARER_TOKEN, "
        "or SF_JWT_PRIVATE_KEY+SF_CLIENT_ID+SF_USERNAME (PyJWT installed), "
        "or have `sf` CLI authed with target-org smartertax."
    )


class SFRestClient:
    """Thin urllib-based SF REST client. POST + GET only — adapter scope."""

    def __init__(self, token: Optional[str] = None, instance_url: Optional[str] = None):
        self._token = token
        self._instance = instance_url

    def _resolve(self) -> Tuple[str, str]:
        if not (self._token and self._instance):
            self._token, self._instance = get_sf_token()
        return self._token, self._instance

    def post(self, sobject: str, body: dict) -> dict:
        """POST /sobjects/{sobject}/. Returns parsed JSON or {error:...}."""
        token, instance = self._resolve()
        url = f"{instance}/services/data/{SF_API_VERSION}/sobjects/{sobject}/"
        req = urllib.request.Request(
            url, data=json.dumps(body).encode(), method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            body_bytes = e.read() if e.fp else b""
            return {
                "error": True,
                "status": e.code,
                "message": body_bytes.decode(errors="replace"),
            }
        except Exception as e:
            return {"error": True, "status": 0, "message": str(e)}

    def get(self, path: str) -> dict:
        """GET an absolute REST path (with /services/data/... prefix)."""
        token, instance = self._resolve()
        url = f"{instance}{path}"
        req = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {token}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            body_bytes = e.read() if e.fp else b""
            return {
                "error": True,
                "status": e.code,
                "message": body_bytes.decode(errors="replace"),
            }
        except Exception as e:
            return {"error": True, "status": 0, "message": str(e)}

    def query(self, soql: str) -> dict:
        """SOQL query convenience wrapper."""
        path = f"/services/data/{SF_API_VERSION}/query/?q={urllib.parse.quote(soql)}"
        return self.get(path)
