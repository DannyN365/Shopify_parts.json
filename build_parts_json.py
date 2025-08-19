import os, json, re, requests
from datetime import datetime, timezone

# --- Optional for local runs; ignored in Actions if no .env present ---
try:
    from dotenv import load_dotenv  # pip install python-dotenv (local only)
    load_dotenv()
except Exception:
    pass

def _env(name: str) -> str:
    val = os.environ.get(name, "")
    # strip spaces, tabs, CRLF that often sneak into secrets
    val = val.strip().replace("\r", "").replace("\n", "")
    if not val:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return val

# --- ENV (set as GitHub Action secrets) ---
APP_ID        = _env("LARK_APP_ID")
APP_SECRET    = _env("LARK_APP_SECRET")
LARK_BASE_ID  = _env("LARK_BASE_ID")
LARK_TABLE_ID = _env("LARK_TABLE_ID")  # parts table

AUTH_URL = "https://open.larksuite.com/open-apis/auth/v3/tenant_access_token/internal"

def get_lark_headers():
    r = requests.post(AUTH_URL, json={"app_id": APP_ID, "app_secret": APP_SECRET}, timeout=30)
    # Lark often returns 200 with {code,msg} on errors, so don't only rely on status
    try:
        data = r.json()
    except Exception:
        raise RuntimeError(f"Lark auth HTTP {r.status_code}: {r.text[:200]}")
    token = data.get("tenant_access_token")
    if not token:
        code = data.get("code")
        msg  = data.get("msg")
        raise RuntimeError(
            f"Lark auth failed (code={code}) msg={msg}. "
            f"Check: app is Internal (self-built), IDs correct, no trailing spaces."
        )
    return {"Authorization": f"Bearer {token}"}

def _to_float(x, default=0.0):
    if isinstance(x, list):
        x = x[0] if x else default
    try:
        return float(x)
    except Exception:
        return default

def _slug(s):
    s = (s or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return re.sub(r"-+", "-", s).strip("-")

def load_lark_spare_parts(headers):
    all_rows = []
    page_token = None
    base_url = f"https://open.larksuite.com/open-apis/bitable/v1/apps/{LARK_BASE_ID}/tables/{LARK_TABLE_ID}/records"

    while True:
        params = {"page_size": 500}
        if page_token:
            params["page_token"] = page_token
        res = requests.get(base_url, headers=headers, params=params, timeout=60)
        res.raise_for_status()
        data = res.json().get("data", {}) or {}
        items = data.get("items", []) or []
        if not items:
            break

        for rec in items:
            f = rec.get("fields", {}) or {}
            part_num = f.get("PN")
            if not part_num:
                continue

            mn_raw = f.get("Model number", {})
            model_number = mn_raw.get("text") if isinstance(mn_raw, dict) else str(mn_raw or "")
            model_name   = f.get("Model Name-English", "") or ""

            price_eur = _to_float(f.get("Price (EUR)"), 0.0)
            stock     = int(f.get("Current stock", 0) or 0)

            pic_url = ""
            pics = f.get("Pictures", [])
            if isinstance(pics, list) and pics and isinstance(pics[0], dict):
                pic_url = pics[0].get("url") or pics[0].get("value") or ""

            all_rows.append({
                "Part #": str(part_num).strip(),
                "Part Name": f.get("English Name", ""),
                "Model number": str(model_number),
                "Model Name": model_name,
                "In Stock": stock,
                "Picture": pic_url,
                "Price (EUR)": price_eur
            })

        page_token = (data.get("page_token") or "").strip()
        if not page_token:
            break

    return all_rows

def build_snapshot():
    headers = get_lark_headers()
    rows = load_lark_spare_parts(headers)

    models_by_key = {}
    def ensure_model_id(model_number, model_name):
        key = (model_number or model_name or "").strip()
        if not key:
            return None
        if key not in models_by_key:
            model_id = _slug(model_number) if model_number else _slug(model_name)
            orig = model_id
            i = 2
            while any(m["id"] == model_id for m in models_by_key.values()):
                model_id = f"{orig}-{i}"
                i += 1
            models_by_key[key] = {"id": model_id, "name": model_name or model_number}
        return models_by_key[key]["id"]

    parts_by_sku = {}
    for r in rows:
        sku = r["Part #"]
        if sku not in parts_by_sku:
            parts_by_sku[sku] = {
                "sku": sku,
                "name": r.get("Part Name") or "",
                "price_eur": r.get("Price (EUR)", 0.0),
                "stock": r.get("In Stock", 0),
                "image": r.get("Picture") or "",
                "compatible_models": set()
            }
        mid = ensure_model_id(r.get("Model number"), r.get("Model Name"))
        if mid:
            parts_by_sku[sku]["compatible_models"].add(mid)

        if r.get("Price (EUR)"):
            parts_by_sku[sku]["price_eur"] = float(r["Price (EUR)"])
        if isinstance(r.get("In Stock"), int):
            parts_by_sku[sku]["stock"] = int(r["In Stock"])
        if r.get("Picture"):
            parts_by_sku[sku]["image"] = r["Picture"]
        if r.get("Part Name"):
            parts_by_sku[sku]["name"] = r["Part Name"]

    models = sorted(models_by_key.values(), key=lambda x: x["name"].lower())
    parts = []
    for p in parts_by_sku.values():
        parts.append({
            "sku": p["sku"],
            "name": p["name"],
            "price_eur": p["price_eur"],
            "stock": p["stock"],
            "image": p["image"],
            "compatible_models": sorted(list(p["compatible_models"]))
        })
    parts.sort(key=lambda x: x["sku"])

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "models": models,
        "parts": parts
    }
    os.makedirs("public", exist_ok=True)
    with open("public/index.html", "w") as f:
    f.write('<meta http-equiv="refresh" content="0; url=parts.json">')
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(f"Wrote public/parts.json with {len(models)} models and {len(parts)} parts.")

if __name__ == "__main__":
    build_snapshot()
