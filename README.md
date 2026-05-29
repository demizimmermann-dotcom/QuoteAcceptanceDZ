# PriceEasy Quote Pipeline — Setup Guide

Turns a HubSpot deal into a hosted, branded order form (web version of contract
pages 5–8). When a deal hits a stage you choose, HubSpot fires a webhook → a tiny
Cloudflare Worker relays it to GitHub → a GitHub Action renders the order form and
publishes it to GitHub Pages at an unguessable URL → the link is written back onto
the deal.

```
HubSpot workflow (Send a webhook)
        │  POST deal id
        ▼
Cloudflare Worker (adds GitHub's auth headers)
        │  repository_dispatch
        ▼
GitHub Actions  ──► transform.py renders template.html ──► GitHub Pages /q/<slug>/
        │
        └─► writes the quote URL back onto the deal
```

## Files

| File | What it is |
|------|------------|
| `template.html` | The branded order form. Renders from one `QUOTE_DATA` object the pipeline fills. |
| `scripts/transform.py` | Pulls the deal + line items + company/contact from HubSpot, fills the template. |
| `.github/workflows/quote.yml` | Runs the transform and deploys to Pages on `repository_dispatch`. |
| `worker.js` | Cloudflare Worker relay (HubSpot can't set the headers GitHub needs). |

---

## Prerequisites
- A GitHub account and a new (private is fine) repository.
- A free Cloudflare account.
- HubSpot **Operations Hub Professional or Enterprise** (required for the "Send a webhook" workflow action). If you're on a lower tier, see "Alternative trigger" at the bottom.

---

## Step 1 — Put the files in a GitHub repo
1. Create a new repo, e.g. `quote-pipeline`.
2. Add these files at the same paths shown above (`template.html` and `worker.js` at the root, `transform.py` under `scripts/`, the workflow under `.github/workflows/`).
3. Commit and push.

## Step 2 — Turn on GitHub Pages
1. Repo → **Settings → Pages**.
2. Under **Build and deployment → Source**, choose **GitHub Actions**.
3. Note your Pages root URL — it looks like `https://<you>.github.io/quote-pipeline`. You'll need it as `PAGES_BASE`.

## Step 3 — Create a HubSpot private-app token
1. HubSpot → **Settings → Integrations → Private Apps → Create a private app**.
2. On **Scopes**, enable: `crm.objects.deals.read`, `crm.objects.deals.write`, `crm.objects.line_items.read`, `crm.objects.companies.read`, `crm.objects.contacts.read`.
3. Create it and copy the **access token** (starts with `pat-`).

## Step 4 — Create a GitHub token for the Worker
1. GitHub → **Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate**.
2. **Repository access:** only your `quote-pipeline` repo.
3. **Permissions:** Contents → Read and write; Actions → Read and write.
4. Generate and copy the token (starts with `github_pat_`).

## Step 5 — Add repo secrets
Repo → **Settings → Secrets and variables → Actions → New repository secret**. Add:
- `HUBSPOT_TOKEN` = the `pat-…` token from Step 3.
- `PAGES_BASE` = your Pages root from Step 2 (e.g. `https://you.github.io/quote-pipeline`). Add this as a **Variable** (same screen, "Variables" tab) rather than a secret if you prefer — the workflow reads it either way.

## Step 6 — Deploy the Cloudflare Worker (the relay)
1. Cloudflare dashboard → **Workers & Pages → Create → Worker**. Name it e.g. `quote-relay`, deploy the default, then **Edit code**.
2. Paste the contents of `worker.js`, click **Deploy**.
3. **Settings → Variables and Secrets**, add three **Secrets**:
   - `GH_TOKEN` = the `github_pat_…` from Step 4.
   - `GH_REPO`  = `your-username/quote-pipeline`.
   - `SHARED_KEY` = any long random string you make up (e.g. a password-generator value).
4. Copy your Worker URL: `https://quote-relay.<subdomain>.workers.dev`.

## Step 7 — Test the relay by hand (before involving HubSpot)
From a terminal, replace the URL/key/dealId and run:
```bash
curl -X POST "https://quote-relay.<subdomain>.workers.dev/?key=YOUR_SHARED_KEY" \
  -H "Content-Type: application/json" \
  -d '{"objectId": 123456789}'
```
- `202 queued` → success. Check repo → **Actions**; a run should appear and, after it finishes, your quote is live at `PAGES_BASE/q/<slug>/` (the run log prints the exact URL).
- `403 forbidden` → wrong/missing `key`.
- `502 github dispatch failed` → check `GH_TOKEN`/`GH_REPO`.

Use a real deal id from a HubSpot deal URL so transform.py has something to fetch.

## Step 8 — Build the HubSpot workflow
1. HubSpot → **Automation → Workflows → Create → From scratch → Deal-based**.
2. **Enrollment trigger:** e.g. *Deal stage is "Quote ready"* (or any signal you want to generate a quote).
3. **+ → Send a webhook.**
   - Method: **POST**
   - Webhook URL: `https://quote-relay.<subdomain>.workers.dev/?key=YOUR_SHARED_KEY`
   - Request body: include the deal's record id. HubSpot sends the enrolled object's properties; if you can add a custom property field, send `{"objectId": <Record ID>}`. The Worker also reads `hs_object_id`, so the default payload usually works.
4. **Turn the workflow on.** Re-enroll a test deal (or move one into the trigger stage) and watch repo → **Actions**.

## Step 9 — Map your HubSpot fields
Open `scripts/transform.py` → the `CONFIG` block near the top:
- `BRAND` — already set to PriceEasy; change the `--accent` color in `template.html` if you have an exact brand hex.
- `DEAL_PROPS` — set each value to the **internal name** of your matching HubSpot deal property (find them under Settings → Properties). Leave any you don't have; that field just renders blank.
- Line-item categorization:
  - **Recurring** is detected automatically from HubSpot's native `recurringbillingfrequency` (set it on the line item).
  - **Equipment** is matched by SKU prefix — default `EQ-`/`EQUIP` (`EQUIPMENT_SKU_PREFIX`).
  - **Pilot** items by SKU prefix `PILOT`/`POC`.
  - Everything else falls into **One-Time**.

## Step 10 — (Optional) Wire the "Confirm Quote" button
Set `ACCEPT_ENDPOINT` in `transform.py` to a URL that records acceptance:
- Easiest: a **HubSpot form** submit endpoint — the form submission can re-enroll the deal into a workflow that marks it accepted and kicks off DocuSign.
- Or your own API. The button POSTs `{quote, company, acceptedBy, title, acceptedAt, total}`.

---

## Important: keep signing & payment in DocuSign
This web form mirrors pages 5–8 (review of the order). **Do not** move pages 9–11
(signatures, and the ACH/credit-card Payment Authorization) onto the Pages site —
collecting bank/card numbers on a static page puts you in PCI scope with no audit
trail. Let the **Confirm Quote** action trigger your existing DocuSign envelope for
the binding signature and payment authorization, using the same merge tags it
already uses.

## Privacy note
Quotes are published to GitHub Pages, which is public. That's why URLs use a random
`/q/<slug>/` token instead of the deal id, so they can't be guessed or enumerated.
Treat the link as the secret. If you need true access control, host the output behind
auth (e.g. Cloudflare Pages with Access) instead of GitHub Pages — the transform
output is the same either way.

## Alternative trigger (no Operations Hub)
If you don't have Ops Hub Pro/Enterprise, you can't use "Send a webhook." Options:
- A **custom-coded workflow action** (also Ops Hub), or
- Poll the HubSpot CRM API on a schedule from the same GitHub Action (`on: schedule`)
  and render quotes for deals in your target stage — no webhook or Worker needed.
  Ask and I'll provide the scheduled version.
