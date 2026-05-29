// worker.js — Cloudflare Worker relay: HubSpot webhook -> GitHub repository_dispatch
//
// Why this exists: GitHub's dispatch endpoint requires BOTH an
// "Authorization: Bearer <token>" and an "Accept: application/vnd.github+json"
// header. HubSpot's "Send a webhook" action can't set those, so we relay.
//
// Set these as Worker secrets (Settings > Variables > Add secret), NOT in code:
//   GH_TOKEN   - GitHub PAT (fine-grained, repo scoped, "Contents" + "Actions" write)
//   GH_REPO    - "owner/repo"
//   SHARED_KEY - any random string; also send it from HubSpot so randoms can't trigger you
//
// In HubSpot's webhook action, append the shared key as a query param:
//   https://<your-worker>.workers.dev/?key=YOUR_SHARED_KEY

export default {
  async fetch(request, env) {
    if (request.method !== "POST") {
      return new Response("POST only", { status: 405 });
    }
    // simple shared-secret check (HubSpot can't sign workflow webhooks reliably)
    const url = new URL(request.url);
    if (env.SHARED_KEY && url.searchParams.get("key") !== env.SHARED_KEY) {
      return new Response("forbidden", { status: 403 });
    }

    let body;
    try {
      body = await request.json();
    } catch {
      return new Response("bad json", { status: 400 });
    }

    // HubSpot workflow webhooks usually send the deal id under one of these.
    // Adjust if your payload differs (check the worker logs on first run).
    const dealId =
      body.objectId || body.dealId || body.hs_object_id ||
      (body.properties && body.properties.hs_object_id) || null;

    if (!dealId) {
      return new Response("no deal id in payload", { status: 422 });
    }

    const ghResp = await fetch(
      `https://api.github.com/repos/${env.GH_REPO}/dispatches`,
      {
        method: "POST",
        headers: {
          "Authorization": `Bearer ${env.GH_TOKEN}`,
          "Accept": "application/vnd.github+json",
          "Content-Type": "application/json",
          "User-Agent": "hubspot-quote-relay",
        },
        body: JSON.stringify({
          event_type: "hubspot_deal",
          // one top-level key keeps us well under the client_payload size cap
          client_payload: { dealId: String(dealId), raw: body },
        }),
      }
    );

    if (!ghResp.ok) {
      const txt = await ghResp.text();
      return new Response(`github dispatch failed: ${ghResp.status} ${txt}`, { status: 502 });
    }
    return new Response("queued", { status: 202 });
  },
};
