import os
import traceback
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException, Header, Depends, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from .llm import get_provider, run_loop
from . import pbi, auth
from fastapi.middleware.cors import CORSMiddleware
app = FastAPI()


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],            # fine here: no cookies, every call needs a Bearer token
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)
provider = get_provider()

SYSTEM = """You are a data assistant inside a Power BI report. You answer questions using ONLY results from the run_dax tool on the semantic model described below.

Rules:
- Never invent numbers. If you have no query result for it, say so.
- Prefer existing measures: SUMMARIZECOLUMNS('Table'[Col], "Name", [Measure]).
- Always aggregate in DAX. Use TOPN for rankings. Never return large raw tables.
- Queries must start with EVALUATE. Read-only.
- If a query errors, fix the DAX and retry. If it fails twice, tell the user plainly.
- Keep answers short: lead with the number or answer, then one line of context.

SEMANTIC MODEL:
{schema}
"""

MAX_HISTORY = 10   # turns


# ---------- auth: the one place that decides whose identity queries run as ----------

def user_token(authorization: str = Header(None)) -> str:
    if os.environ.get("AUTH_MODE", "session") == "dev":
        return pbi.dev_token()                    # dev only: your az login identity
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Sign in required")
    try:
        return auth.get_user_token(authorization[7:])
    except PermissionError as e:
        raise HTTPException(status_code=401, detail=str(e))


class ClaimReq(BaseModel):
    sid: str
    code: str


@app.post("/auth/start")
def auth_start():
    return auth.start()


@app.get("/auth/callback", response_class=HTMLResponse)
def auth_callback(request: Request):
    try:
        code = auth.callback(dict(request.query_params))
    except Exception as e:
        return HTMLResponse(f"<h3>Sign-in failed</h3><p>{str(e)[:200]}</p>", status_code=400)
    return HTMLResponse(
        f"<h2>Signed in</h2><p>Enter this code in the report:</p><h1>{code}</h1>"
        "<p>You can close this tab.</p>"
    )


@app.post("/auth/claim")
def auth_claim(req: ClaimReq):
    try:
        return {"session_token": auth.claim(req.sid, req.code)}
    except PermissionError as e:
        raise HTTPException(status_code=401, detail=str(e))


@app.post("/auth/logout")
def auth_logout(authorization: str = Header(None)):
    if authorization and authorization.startswith("Bearer "):
        auth.logout(authorization[7:])
    return {"ok": True}


# ---------- chat ----------

class ChatReq(BaseModel):
    question: str
    history: list[dict] = []     # [{"role": "user" | "assistant", "content": str}]


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/chat")
def chat(req: ChatReq, token: str = Depends(user_token)):
    try:
        system = SYSTEM.format(schema=pbi.schema_text(token))

        def run_dax(dax: str):
            print("DAX >>", dax)                  # visible in the server log for debugging
            return pbi.run_dax(dax, token)        # runs as the signed-in user, so RLS applies

        # Convert client history into the neutral format used by llm.py
        msgs = []
        for h in req.history[-MAX_HISTORY * 2:]:
            if h.get("role") == "user":
                msgs.append({"role": "user", "content": h.get("content", "")})
            elif h.get("role") == "assistant":
                msgs.append({"role": "assistant", "text": h.get("content", ""), "tool_calls": []})
        msgs.append({"role": "user", "content": req.question})

        run_tools = [t for t in pbi.TOOLS if t["name"] == "run_dax"]
        answer = run_loop(provider, system, msgs, run_tools, {"run_dax": run_dax})
        return {"answer": answer}

    except Exception as e:
        traceback.print_exc()                     # full error in the uvicorn terminal
        raise HTTPException(status_code=500, detail=str(e)[:300])