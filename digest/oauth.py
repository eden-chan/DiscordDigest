import asyncio
import json
import os
from typing import Any, Dict, Optional

import aiohttp
from aiohttp import web
import urllib.parse as _up
import webbrowser
import asyncio
from datetime import datetime, timezone, timedelta


TOKEN_URL = "https://discord.com/api/oauth2/token"


async def exchange_code(
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
) -> Dict[str, Any]:
    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(
            TOKEN_URL,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"OAuth exchange failed ({resp.status}): {text}")
            return await resp.json()


async def refresh_access_token(
    client_id: str,
    client_secret: str,
    refresh_token: str,
) -> Dict[str, Any]:
    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(
            TOKEN_URL,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"OAuth refresh failed ({resp.status}): {text}")
            return await resp.json()


async def exchange_from_env() -> Dict[str, Any]:
    cid = os.getenv("OAUTH_CLIENT_ID")
    secret = os.getenv("OAUTH_CLIENT_SECRET")
    code = os.getenv("OAUTH_CODE")
    redirect = os.getenv("OAUTH_REDIRECT_URI")
    if not all([cid, secret, code, redirect]):
        raise RuntimeError("Missing OAUTH_* env vars for code exchange")
    return await exchange_code(cid, secret, code, redirect)


async def refresh_from_env() -> Dict[str, Any]:
    cid = os.getenv("OAUTH_CLIENT_ID")
    secret = os.getenv("OAUTH_CLIENT_SECRET")
    rtok = os.getenv("OAUTH_REFRESH_TOKEN")

    if not rtok:
        # Fallback to SQLite token store
        try:
            from .db import get_oauth_token_sync
            rec = get_oauth_token_sync(provider="discord", token_type="Bearer")
            if rec is not None:
                rtok = getattr(rec, "refreshToken", None)
        except Exception:
            rtok = None

    if not all([cid, secret, rtok]):
        raise RuntimeError("Missing refresh parameters: need OAUTH_CLIENT_ID, OAUTH_CLIENT_SECRET, and OAUTH_REFRESH_TOKEN (env or SQLite)")
    return await refresh_access_token(cid, secret, rtok)  # type: ignore[arg-type]


def build_authorize_url(client_id: str, redirect_uri: str, scope: str = "messages.read", prompt: str = "consent") -> str:
    return (
        "https://discord.com/oauth2/authorize?" +
        _up.urlencode(
            {
                "client_id": client_id,
                "response_type": "code",
                "redirect_uri": redirect_uri,
                "scope": scope,
                "prompt": prompt,
            }
        )
    )


async def authorize_and_exchange(
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    scope: str = "messages.read",
    open_browser: bool = True,
    timeout: int = 300,
) -> Dict[str, Any]:
    """Runs a tiny local server to capture the OAuth code from the redirect and exchanges it.

    Returns the token response JSON.
    """
    parsed = _up.urlparse(redirect_uri)
    host = parsed.hostname or "localhost"
    port = parsed.port or (3000 if host in {"localhost", "127.0.0.1"} else 80)
    path = parsed.path or "/"

    code_future: asyncio.Future[str] = asyncio.get_event_loop().create_future()

    async def handle(request: web.Request) -> web.Response:
        qs = request.rel_url.query
        err = qs.get("error")
        if err:
            if not code_future.done():
                code_future.set_exception(RuntimeError(f"OAuth error: {err}"))
            return web.Response(text="OAuth failed. You can close this tab.")
        code = qs.get("code")
        if code and not code_future.done():
            code_future.set_result(code)
        return web.Response(text="Authorization received. You can return to the terminal.")

    app = web.Application()
    app.router.add_get(path, handle)
    # also accept any GET path in case of trailing slashes/mismatches
    app.router.add_get("/{tail:.*}", handle)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host=host, port=port)
    await site.start()

    try:
        url = build_authorize_url(client_id, redirect_uri, scope=scope)
        print("Open this URL to authorize:")
        print(url)
        if open_browser:
            try:
                webbrowser.open(url)
            except Exception:
                pass
        # Wait for the code
        code = await asyncio.wait_for(code_future, timeout=timeout)
        # Exchange
        result = await exchange_code(client_id, client_secret, code, redirect_uri)
        return result
    finally:
        await runner.cleanup()


async def probe_token(token: str, token_type: str) -> Dict[str, Any]:
    """Probe the current token and return metadata.

    - For Bearer: calls /oauth2/@me to get scopes and application info.
    - For Bot: calls /users/@me to confirm identity.
    """
    headers: Dict[str, str]
    url: str
    if token_type.lower() == "bot":
        headers = {"Authorization": f"Bot {token}"}
        url = "https://discord.com/api/users/@me"
    else:
        headers = {"Authorization": f"Bearer {token}"}
        url = "https://discord.com/api/oauth2/@me"

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            data: Dict[str, Any] = {
                "http_status": resp.status,
                "url": url,
                "token_type": token_type,
            }
            try:
                payload = await resp.json()
            except Exception:
                payload = {"raw": await resp.text()}
            data["payload"] = payload
            return data


def _print(result: Dict[str, Any]) -> None:
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Discord OAuth helper")
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--exchange", action="store_true", help="Exchange OAUTH_CODE for tokens using env vars")
    g.add_argument("--refresh", action="store_true", help="Refresh access token using env vars")
    parser.add_argument("--out", help="Write resulting JSON to a file")
    args = parser.parse_args()

    if args.exchange:
        result = asyncio.run(exchange_from_env())
    else:
        result = asyncio.run(refresh_from_env())

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"Wrote {args.out}")
    else:
        _print(result)

    # Also store in SQLite for centralized management
    try:
        from .db import upsert_oauth_token_sync

        token_type = str(result.get("token_type", "Bearer"))
        access = str(result.get("access_token")) if result.get("access_token") else None
        refresh = result.get("refresh_token")
        scope = result.get("scope")
        expires_in = result.get("expires_in")
        expires_at = None
        if isinstance(expires_in, (int, float)):
            expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
        if access:
            upsert_oauth_token_sync(
                provider="discord",
                token_type=token_type,
                access_token=access,
                refresh_token=refresh,
                scope=scope,
                expires_at=expires_at,
            )
            print("Stored OAuth token in SQLite (provider=discord).")
    except Exception as e:
        print(f"Warning: failed to store OAuth token in SQLite: {e}")


if __name__ == "__main__":
    main()
