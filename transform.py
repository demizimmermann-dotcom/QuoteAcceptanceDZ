#!/usr/bin/env python3
"""
transform.py — turn a HubSpot deal into a hosted PriceEasy order form.

Reads the deal id (from the GitHub repository_dispatch payload), pulls the deal,
its line items, and the associated company + primary contact from the HubSpot
CRM API, sorts the line items into recurring / one-time / equipment, fills
template.html, and writes it to public/q/<random-slug>/index.html.

It then writes that public URL back onto the deal so the rep sees the link in
HubSpot.

Required env (set as GitHub Actions secrets / step env):
  HUBSPOT_TOKEN  - private-app token. Scopes: crm.objects.deals.read+write,
                   crm.objects.line_items.read, crm.objects.companies.read,
                   crm.objects.contacts.read
  DEAL_ID        - from github.event.client_payload.dealId
  PAGES_BASE     - your Pages root, e.g. https://acme.github.io/hubspot-quote
"""
import json, os, sys, secrets, datetime, urllib.request, urllib.error

API   = "https://api.hubapi.com"
TOKEN = os.environ["HUBSPOT_TOKEN"]
DEAL  = os.environ["DEAL_ID"]
BASE  = os.environ.get("PAGES_BASE", "").rstrip("/")

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG — edit to match YOUR HubSpot setup
# ─────────────────────────────────────────────────────────────────────────────
BRAND = {
    "logoUrl": "https://priceeasy.com/wp-content/uploads/2026/04/Logo-2-e1777375707525.png",
    "tagline": "AI-powered pricing & location intelligence for fuel, c-store & retail",
    "legal":   "MaYH Inc., d/b/a PriceEasy AI",
    "website": "www.priceeasy.com",
    "address": "1080 Eldridge Pkwy, Suite 340, Houston TX 77077",
    "phone":   "Sales +1 (713) 364-8412",
}
CURRENCY = "USD"
QUOTE_VALID_DAYS = 30
ACCEPT_ENDPOINT  = ""          # HubSpot form submit URL or your API; "" = demo

# Internal names of your custom DEAL properties. Leave a value as None if you
# don't have that property — the field just renders blank.
DEAL_PROPS = {
    "quote_number":   "quote_number",
    "quote_version":  "quote_version",
    "supersedes":     "supersedes_quote",
    "scope":          "scope_of_agreement",
    "client_type":    "client_type",
    "territory":      "territory",
    "site_locations": "site_locations",
    "billing_freq":   "billing_frequency",
    "payment_method": "payment_method",
    "auto_renewal":   "auto_renewal_term",
    "pilot_flag":     "pilot_agreement",
    "effective_date": "contract_effective_date",
    "contract_term":  "contract_term",
    "pilot_days":     "pilot_days",
    "pilot_impl_fee": "pilot_implementation_fee",
    "url_writeback":  "quote_url",     # where we store the generated link
}
# Line items are recurring if HubSpot's native recurringbillingfrequency is set.
# Equipment is detected by SKU prefix (everything else = one-time).
EQUIPMENT_SKU_PREFIX = ("EQ-", "EQUIP")
PILOT_SKU_PREFIX     = ("PILOT", "POC")
# ─────────────────────────────────────────────────────────────────────────────


def req(method, path, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    r = urllib.request.Request(API + path, method=method, data=data,
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
    with urllib.request.urlopen(r) as resp:
        return json.load(resp) if resp.length != 0 else {}


def prop(props, key):
    name = DEAL_PROPS.get(key)
    return props.get(name, "") if name else ""


def fetch_deal():
    p = ",".join(["dealname", "amount"] + [v for v in DEAL_PROPS.values() if v])
    return req("GET", f"/crm/v3/objects/deals/{DEAL}"
                      f"?properties={p}&associations=line_items,companies,contacts")


def fetch_line_items(deal):
    ids = [a["id"] for a in deal.get("associations", {})
           .get("line items", deal.get("associations", {}).get("line_items", {})).get("results", [])] \
          if deal.get("associations") else []
    if not ids:
        return []
    body = {"inputs": [{"id": i} for i in ids],
            "properties": ["name", "description", "quantity", "price",
                           "hs_sku", "recurringbillingfrequency"]}
    res = req("POST", "/crm/v3/objects/line_items/batch/read", body)
    out = []
    for li in res.get("results", []):
        p = li.get("properties", {})
        out.append({
            "name": p.get("name") or "Item",
            "sku":  p.get("hs_sku") or "",
            "description": p.get("description") or "",
            "qty":  float(p.get("quantity") or 1),
            "unitPrice": float(p.get("price") or 0),
            "recurring": bool(p.get("recurringbillingfrequency")),
        })
    return out


def categorize(items):
    recurring, one_time, equipment, pilot = [], [], [], []
    for it in items:
        sku = (it["sku"] or "").upper()
        if any(sku.startswith(x) for x in PILOT_SKU_PREFIX):
            pilot.append({"name": it["name"], "qty": it["qty"], "rate": it["unitPrice"]})
        elif any(sku.startswith(x) for x in EQUIPMENT_SKU_PREFIX):
            equipment.append(it)
        elif it["recurring"]:
            recurring.append(it)
        else:
            one_time.append(it)
    return recurring, one_time, equipment, pilot


def named(deal, kind):
    res = deal.get("associations", {}).get(kind, {}).get("results", [])
    if not res:
        return ("", "")
    oid = res[0]["id"]
    if kind == "companies":
        o = req("GET", f"/crm/v3/objects/companies/{oid}?properties=name,address")
        pr = o["properties"]
        return (pr.get("name", ""), pr.get("address", ""))
    o = req("GET", f"/crm/v3/objects/contacts/{oid}"
                   "?properties=firstname,lastname,jobtitle,email,phone")
    pr = o["properties"]
    nm = " ".join(x for x in [pr.get("firstname"), pr.get("lastname")] if x).strip()
    disp = f"{nm}, {pr['jobtitle']}" if pr.get("jobtitle") and nm else nm
    return (disp, pr.get("email", ""))


def main():
    deal = fetch_deal()
    props = deal.get("properties", {})
    items = fetch_line_items(deal)
    recurring, one_time, equipment, pilot_items = categorize(items)

    company_name, company_addr = named(deal, "companies")
    contact_disp, contact_email = named(deal, "contacts")

    today = datetime.date.today()
    valid = today + datetime.timedelta(days=QUOTE_VALID_DAYS)
    pilot_on = str(prop(props, "pilot_flag")).lower() in ("yes", "true", "1") or bool(pilot_items)

    data = {
        "currency": CURRENCY,
        "brand": BRAND,
        "quote": {
            "number": prop(props, "quote_number") or f"QF-{DEAL}",
            "version": prop(props, "quote_version") or "1",
            "supersedes": prop(props, "supersedes"),
            "date": today.strftime("%b %d, %Y"),
            "scope": prop(props, "scope"),
        },
        "customer": {
            "company": company_name or "Customer",
            "address": company_addr,
            "clientType": prop(props, "client_type"),
            "territory": prop(props, "territory"),
            "siteLocations": prop(props, "site_locations"),
            "contactName": contact_disp,
            "contactEmail": contact_email,
        },
        "billing": {
            "name": contact_disp, "email": contact_email, "phone": "",
            "poNumber": "", "address": "",
        },
        "terms": {
            "billingFrequency": prop(props, "billing_freq"),
            "paymentMethod": prop(props, "payment_method"),
            "autoRenewalTerm": prop(props, "auto_renewal"),
            "pilotFlag": prop(props, "pilot_flag") or ("Yes" if pilot_on else "No"),
            "effectiveDate": prop(props, "effective_date"),
            "contractTerm": prop(props, "contract_term"),
        },
        "recurring": recurring,
        "oneTime": one_time,
        "equipment": equipment,
        "pilot": {
            "enabled": pilot_on,
            "days": int(float(prop(props, "pilot_days") or 30)),
            "implementationFee": float(prop(props, "pilot_impl_fee") or 0),
            "items": pilot_items,
        },
        "acceptEndpoint": ACCEPT_ENDPOINT,
    }

    # unguessable URL so quotes can't be enumerated by deal id
    slug = secrets.token_urlsafe(12)
    template = open("template.html", encoding="utf-8").read()
    out = (template.split("/*__QUOTE_DATA__*/")[0]
           + json.dumps(data, ensure_ascii=False)
           + template.split("/*__END__*/")[1])

    out_dir = os.path.join("public", "q", slug)
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(out)
    url = f"{BASE}/q/{slug}/" if BASE else f"q/{slug}/"
    print(f"Wrote {out_dir}/index.html  ->  {url}")

    # write the link back onto the deal (optional; needs deals write scope)
    wb = DEAL_PROPS.get("url_writeback")
    if wb and BASE:
        try:
            req("PATCH", f"/crm/v3/objects/deals/{DEAL}",
                {"properties": {wb: url}})
            print(f"Wrote {wb} back to deal {DEAL}")
        except urllib.error.HTTPError as e:
            print(f"(writeback skipped: {e.code})", file=sys.stderr)


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as e:
        print(f"HubSpot API error {e.code}: {e.read().decode()}", file=sys.stderr)
        sys.exit(1)
