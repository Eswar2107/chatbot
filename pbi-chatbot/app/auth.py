import os, time, secrets, hmac
import msal

AUTHORITY = f"https://login.microsoftonline.com/{os.environ['TENANT_ID']}"
CLIENT_ID = os.environ["CLIENT_ID"]
CLIENT_SECRET = os.environ["CLIENT_SECRET"]
REDIRECT_URI = os.environ["REDIRECT_URI"]
SCOPES = ["https://analysis.windows.net/powerbi/api/Dataset.Read.All"]

PENDING_TTL = 600          # 10 min to finish sign-in
SESSION_IDLE_TTL = 8 * 3600

PENDING = {}    # state -> {flow, cache, code, attempts, created}
SESSIONS = {}   # session_token -> {cache, last, user}


def _app(cache):
    return msal.ConfidentialClientApplication(
        CLIENT_ID, client_credential=CLIENT_SECRET, authority=AUTHORITY, token_cache=cache)


def start():
    cache = msal.SerializableTokenCache()
    flow = _app(cache).initiate_auth_code_flow(SCOPES, redirect_uri=REDIRECT_URI)
    sid = flow["state"]
    PENDING[sid] = {"flow": flow, "cache": cache, "code": None, "attempts": 0, "created": time.time()}
    return {"sid": sid, "login_url": flow["auth_uri"]}


def callback(params: dict):
    sid = params.get("state")
    p = PENDING.get(sid)
    if not p or time.time() - p["created"] > PENDING_TTL:
        raise ValueError("Sign-in expired. Start again.")
    result = _app(p["cache"]).acquire_token_by_auth_code_flow(p["flow"], params)
    if "access_token" not in result:
        raise ValueError(result.get("error_description", "Sign-in failed")[:200])
    p["code"] = f"{secrets.randbelow(10**6):06d}"
    p["user"] = result.get("id_token_claims", {}).get("preferred_username", "")
    return p["code"]


def claim(sid: str, code: str) -> str:
    """Visual swaps sid + the on-screen code for a session token.
    The code stops someone else's sign-in being attached to your session."""
    p = PENDING.get(sid)
    if not p or not p["code"] or time.time() - p["created"] > PENDING_TTL:
        raise PermissionError("Not signed in yet or expired.")
    p["attempts"] += 1
    if p["attempts"] > 5:
        PENDING.pop(sid, None)
        raise PermissionError("Too many attempts.")
    if not hmac.compare_digest(p["code"], code.strip()):
        raise PermissionError("Wrong code.")
    token = secrets.token_urlsafe(32)
    SESSIONS[token] = {"cache": p["cache"], "last": time.time(), "user": p["user"]}
    PENDING.pop(sid, None)
    return token


def get_user_token(session_token: str) -> str:
    """The ONE place that decides whose identity a query runs as."""
    s = SESSIONS.get(session_token)
    if not s or time.time() - s["last"] > SESSION_IDLE_TTL:
        SESSIONS.pop(session_token, None)
        raise PermissionError("Session expired. Sign in again.")
    app = _app(s["cache"])
    accounts = app.get_accounts()
    result = app.acquire_token_silent(SCOPES, account=accounts[0]) if accounts else None
    if not result or "access_token" not in result:
        SESSIONS.pop(session_token, None)
        raise PermissionError("Session expired. Sign in again.")
    s["last"] = time.time()
    return result["access_token"]


def logout(session_token: str):
    SESSIONS.pop(session_token, None)