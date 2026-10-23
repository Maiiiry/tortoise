// IndexNow on the ADMIN EDITOR publish path (#9803, follow-up to #9680) —
// admin-gated, server-side only.
//
// WHY THIS EXISTS. The agent API is the only caller that could reach the
// publish transition, because it is the only writer that goes through
// `api/posts/[[path]].ts` — where #9680 put the submission. The editor writes
// status DIRECTLY to Supabase via supabase-js (`apps/blog-admin/src/lib/
// blog-api.ts`), so the owner publishing from the UI submitted nothing, and the
// one automatic search-engine notification we have was unreachable by the human
// who publishes. `purge.ts` exists for exactly this shape (#1865); this is the
// same seam for the same reason.
//
// ⛔ THIS ENDPOINT DOES NOT WRITE THE POST. It answers one question — "is this
// slug published, and if so may it be submitted?" — and delegates the
// submission to `_shared/indexnow.ts`, so the 24h window, the unreadable-state
// skip and the fail-soft contract stay in ONE place. A second implementation of
// the rule here would be a second definition of "already submitted".
//
// ⛔ An unpublished slug is NEVER submitted (409). IndexNow has no retraction:
// handing a draft URL to six search engines is not undoable, and the endpoint
// takes a slug from the browser.
//
// Fail-open for the human, log-visible for the operator: the post is already
// published and saved by the time the editor calls this, so a vendor outage or
// a forgotten migration must never look like a failed publish.

import { type Env, HSTS, SITE_URL } from "../_lib.ts";
import { requireAdmin } from "../_shared/admin-auth.ts";
import { notifyPublished } from "../_shared/indexnow.ts";
import { isValidSlug } from "../_shared/slug.ts";

function json(body: unknown, status: number): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...HSTS },
  });
}

type Row = { id: string; slug: string; status: string };

export const onRequestPost: PagesFunction<Env> = async ({ request, env }) => {
  if (!env.SUPABASE_URL || !env.SUPABASE_ANON_KEY || !env.SUPABASE_SERVICE_ROLE_KEY) {
    return json({ error: "not_configured" }, 503);
  }
  const admin = await requireAdmin(env, request);
  if (!admin.ok) {
    // A store/database fault is NOT "you are signed out" — the #3485 class.
    if (admin.reason === "unavailable") {
      return json({ error: "unavailable", message: "Session store or admin lookup unavailable — try again shortly" }, 503);
    }
    return json({ error: "unauthorized", message: "Session expired or not an admin — refresh and log in again" }, 401);
  }

  let input: { slug?: unknown };
  try {
    input = (await request.json()) as { slug?: unknown };
  } catch {
    return json({ error: "invalid_json" }, 400);
  }
  const slug = typeof input.slug === "string" ? input.slug : "";
  if (!slug || !isValidSlug(slug)) {
    return json({ error: "invalid_slug" }, 400);
  }

  // Read the row with the service role: the browser only proves WHO it is, and
  // the status must come from the store, never from the caller.
  let row: Row | undefined;
  try {
    const res = await fetch(
      `${env.SUPABASE_URL}/rest/v1/blog_posts?select=id,slug,status&slug=eq.${encodeURIComponent(slug)}&limit=1`,
      {
        headers: {
          apikey: env.SUPABASE_SERVICE_ROLE_KEY,
          Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}`,
          Accept: "application/json",
        },
        signal: AbortSignal.timeout(3_000),
      },
    );
    if (!res.ok) {
      // Visible, not silent: the editor's publish already succeeded, so a read
      // fault here is a missed notification, never a failed publish.
      console.warn(`[indexnow] admin path read failed (supabase ${res.status}): ${slug}`);
      return json({ error: "unavailable", result: "skipped-unreadable" }, 503);
    }
    row = ((await res.json()) as Row[])[0];
  } catch {
    console.warn(`[indexnow] admin path read failed (upstream unreachable): ${slug}`);
    return json({ error: "unavailable", result: "skipped-unreadable" }, 503);
  }

  if (!row) return json({ error: "not_found" }, 404);
  if (row.status !== "published") {
    // Deliberate, and not softened: no retraction exists in this protocol.
    return json({ error: "not_published", status: row.status }, 409);
  }

  const url = `${SITE_URL}/blog/${slug}`;
  const result = await notifyPublished({ env, id: row.id, url, nowMs: Date.now() });
  return json({ slug, url, result }, 200);
};

export const onRequestOptions: PagesFunction<Env> = async () => {
  return new Response(null, {
    status: 204,
    headers: { Allow: "POST, OPTIONS", ...HSTS },
  });
};
