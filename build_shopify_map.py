# build_shopify_map.py
import os, json, requests, time

STORE = os.environ["SHOPIFY_STORE"]                 # reparero.myshopify.com
ADMIN_TOKEN = os.environ["SHOPIFY_ADMIN_TOKEN"]     # Admin API access token
API_VER = "2024-07"

SESSION = requests.Session()
SESSION.headers.update({
    "X-Shopify-Access-Token": ADMIN_TOKEN,
    "Content-Type": "application/json"
})

def run_gql(query, variables=None, retries=3):
    url = f"https://{STORE}/admin/api/{API_VER}/graphql.json"
    for i in range(retries):
        r = SESSION.post(url, json={"query": query, "variables": variables or {}}, timeout=60)
        if r.status_code == 200:
            j = r.json()
            if "errors" in j:
                raise RuntimeError(j["errors"])
            return j
        if r.status_code in (429, 520, 502, 503):
            time.sleep(2**i)
            continue
        raise RuntimeError(f"HTTP {r.status_code}: {r.text}")
    raise RuntimeError("Max retries")

QUERY = """
query Products($cursor: String){
  products(first: 100, after: $cursor) {
    pageInfo { hasNextPage endCursor }
    edges {
      node {
        handle
        variants(first: 100) {
          edges {
            node {
              id
              sku
              price
              availableForSale
              inventoryQuantity
              image { originalSrc url }
            }
          }
        }
      }
    }
  }
}
"""

def main():
    sku_map = {}
    cursor = None
    while True:
        data = run_gql(QUERY, {"cursor": cursor})["data"]["products"]
        for edge in data["edges"]:
            node = edge["node"]
            handle = node["handle"]
            for vedge in node["variants"]["edges"]:
                v = vedge["node"]
                sku = (v.get("sku") or "").strip().lower()
                if not sku:
                    continue
                # Extract numeric id from GID
                gid = v["id"]
                vid = gid.rsplit("/", 1)[-1]
                img = v.get("image", {}) or {}
                image = img.get("url") or img.get("originalSrc")
                sku_map[sku] = {
                    "variant_id": int(vid),
                    "gid": gid,
                    "price": v.get("price"),
                    "inventoryQuantity": v.get("inventoryQuantity"),
                    "availableForSale": v.get("availableForSale"),
                    "image": image,
                    "handle": handle
                }
        if not data["pageInfo"]["hasNextPage"]:
            break
        cursor = data["pageInfo"]["endCursor"]

    os.makedirs("public", exist_ok=True)
    out = {"generated_at": time.time(), "map": sku_map}
    with open("public/shopify_map.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"Wrote public/shopify_map.json with {len(sku_map)} SKUs.")

if __name__ == "__main__":
    main()
