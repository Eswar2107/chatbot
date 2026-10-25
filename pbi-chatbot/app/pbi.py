import os, json, re, requests

WS = os.environ["PBI_WORKSPACE_ID"]
DS = os.environ["PBI_DATASET_ID"]
URL = f"https://api.powerbi.com/v1.0/myorg/groups/{WS}/datasets/{DS}/executeQueries"
SCOPE = "https://analysis.windows.net/powerbi/api/.default"
MAX_ROWS = 200

_cred = None
def dev_token() -> str:
    """Dev only: your own identity. Replaced by per-user tokens in Block 4."""
    global _cred
    if _cred is None:
        from azure.identity import AzureCliCredential
        _cred = AzureCliCredential()
    return _cred.get_token(SCOPE).token


def _clean(rows):
    """'[Name]' -> 'Name' for the INFO.* results."""
    return [{re.sub(r"^.*\[|\]$", "", k): v for k, v in r.items()} for r in rows]


def exec_dax(dax: str, token: str, max_rows: int = MAX_ROWS):
    d = dax.strip().upper()
    if not (d.startswith("EVALUATE") or d.startswith("DEFINE")):
        return {"error": "Only EVALUATE or DEFINE queries are allowed."}
    r = requests.post(
        URL,
        headers={"Authorization": f"Bearer {token}"},
        json={"queries": [{"query": dax}], "serializerSettings": {"includeNulls": True}},
        timeout=60,
    )
    if r.status_code != 200:
        return {"error": f"HTTP {r.status_code}: {r.text[:500]}"}
    res = r.json()["results"][0]
    if res.get("error"):
        return {"error": str(res["error"])[:500]}
    rows = res["tables"][0]["rows"] if res.get("tables") else []
    return {"row_count": len(rows), "rows": rows[:max_rows], "truncated": len(rows) > max_rows}


# ---- tool implementations (token is bound per request in Block 3) ----

def get_schema(token: str):
    out = {}
    for key, queries in {
        "tables":   ["INFO.VIEW.TABLES()", "INFO.TABLES()"],
        "columns":  ["INFO.VIEW.COLUMNS()", "INFO.COLUMNS()"],
        "measures": ["INFO.VIEW.MEASURES()", "INFO.MEASURES()"],
    }.items():
        for q in queries:                       # fallback if VIEW variant unsupported
            res = exec_dax(f"EVALUATE {q}", token, max_rows=2000)
            if "error" not in res:
                out[key] = _clean(res["rows"])
                break
        else:
            out[key] = {"error": res["error"]}
    return out

_schema_cache = None

def schema_text(token: str) -> str:
    """Compact, visible-only schema as text. Cached per process."""
    global _schema_cache
    if _schema_cache:
        return _schema_cache
    s = get_schema(token)
    for k in ("tables", "columns", "measures"):
        if not isinstance(s.get(k), list):
            raise RuntimeError(f"schema {k} failed: {s.get(k)}")

    def skip(name):
        return str(name).startswith(("DateTableTemplate_", "LocalDateTable_"))

    tables = [t["Name"] for t in s["tables"] if not t.get("IsHidden") and not skip(t["Name"])]
    cols, meas = {}, {}
    for c in s["columns"]:
        t, n = c.get("Table"), c.get("Name")
        if t in tables and not c.get("IsHidden") and not str(n).startswith("RowNumber"):
            cols.setdefault(t, []).append(f"{n} ({c.get('DataType')})")
    for m in s["measures"]:
        t = m.get("Table")
        if not m.get("IsHidden"):
            meas.setdefault(t, []).append(m["Name"])

    lines = []
    for t in tables:
        lines.append(f"TABLE '{t}'")
        if cols.get(t):
            lines.append("  columns: " + ", ".join(cols[t]))
    lines.append("MEASURES (use as [Measure Name]):")
    for t, names in meas.items():
        lines.append(f"  in '{t}': " + ", ".join(names))
    _schema_cache = "\n".join(lines)
    return _schema_cache


def run_dax(dax: str, token: str):
    return exec_dax(dax, token)


# ---- tool definitions for the LLM (neutral format from llm.py) ----
TOOLS = [
    {
        "name": "get_schema",
        "description": "List tables, columns and measures of the semantic model. Call this first.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "run_dax",
        "description": ("Run a read-only DAX query (must start with EVALUATE or DEFINE) on the semantic model. "
                        "Aggregate in DAX; never return large raw tables."),
        "parameters": {"type": "object", "properties": {"dax": {"type": "string"}}, "required": ["dax"]},
    },
]