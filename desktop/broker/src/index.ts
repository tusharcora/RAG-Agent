// Sign-in Worker for the Knowledge Agent desktop app.
//
// Flow: the app calls GET /start/{provider} (via the user's browser) with a random `state`, a PKCE-style
// `challenge` and its loopback `port`. The provider redirects back to /callback/{provider}; we exchange the code
// with the client secret, store the result encrypted under `state` for 120 s, and send the browser to
// http://127.0.0.1:{port}/api/connect/finish?state=...&pickup=... — carrying the state id and a one-time random
// `pickup` that only that loopback redirect ever sees. The app then POSTs /redeem {state, verifier, pickup}; the
// result is released once, only if sha256(verifier) matches the challenge AND sha256(pickup) matches the stored
// hash. The pickup binds redemption to the loopback redirect: whoever started the sign-in (and so holds the
// verifier) can't redeem a result that a provider callback delivered to somebody else's browser.
//
// Never log codes, tokens, verifiers or identities from this file.

import { b64urlEncode, challengeOf, constantTimeEqual, open, seal } from "./crypto";
import {
  DISPLAY_NAME,
  type Env,
  type Provider,
  type Result,
  ProviderError,
  authorizeUrl,
  brokerBase,
  exchange,
  isProvider,
  refreshJira,
} from "./providers";

export type { Env } from "./providers";

const PENDING_TTL = 600; // seconds
const RESULT_TTL = 120; // seconds
const MAX_BODY = 8192; // bytes

const STATE_RE = /^[A-Za-z0-9_-]{32,128}$/;
const CHALLENGE_RE = /^[A-Za-z0-9_-]{43}$/;
const VERIFIER_RE = /^[A-Za-z0-9_-]{43,128}$/;
const PORT_RE = /^[0-9]{4,5}$/;
const PICKUP_RE = /^[A-Za-z0-9_-]{43}$/;

const EXPIRED = "This sign-in link has expired. Start again from the app.";
const INVALID = "This sign-in link isn't valid. Start again from the app.";

interface Pending {
  provider: Provider;
  challenge: string;
  port: number;
}

interface StoredResult {
  challenge: string;
  /** base64url(sha256(pickup)) — the pickup itself only travels in the loopback redirect. */
  pickup_hash: string;
  sealed: string;
}

const kPending = (s: string) => `pending:${s}`;
const kResult = (s: string) => `result:${s}`;
const kSeen = (s: string) => `seen:${s}`;

// ---------- responses ----------

const NO_STORE = { "cache-control": "no-store", "referrer-policy": "no-referrer" };

export function escapeHtml(s: string): string {
  return s.replace(/[&<>"'`]/g, (c) => `&#${c.charCodeAt(0)};`);
}

function page(message: string, status: number): Response {
  const body = `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Knowledge Agent sign-in</title>
<style>body{font:16px/1.5 system-ui,sans-serif;max-width:32rem;margin:4rem auto;padding:0 1rem;color:#1f2328}
@media (prefers-color-scheme:dark){body{background:#0d1117;color:#e6edf3}}</style></head>
<body><h1>Knowledge Agent</h1><p>${escapeHtml(message)}</p></body></html>`;
  return new Response(body, {
    status,
    headers: {
      ...NO_STORE,
      "content-type": "text/html; charset=utf-8",
      "content-security-policy": "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'",
      "x-content-type-options": "nosniff",
      "x-frame-options": "DENY",
    },
  });
}

function json(obj: unknown, status = 200): Response {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { ...NO_STORE, "content-type": "application/json; charset=utf-8" },
  });
}

function redirect(location: string): Response {
  return new Response(null, { status: 302, headers: { ...NO_STORE, location } });
}

function text(body: string, status: number): Response {
  return new Response(body, { status, headers: { ...NO_STORE, "content-type": "text/plain; charset=utf-8" } });
}

async function readJson(request: Request): Promise<Record<string, unknown> | null> {
  const raw = await request.text();
  if (raw.length > MAX_BODY) return null;
  try {
    const v = JSON.parse(raw);
    return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

// ---------- handlers ----------

async function handleStart(provider: string, url: URL, env: Env): Promise<Response> {
  if (!isProvider(provider)) return page(INVALID, 400);
  const state = url.searchParams.get("state") ?? "";
  const challenge = url.searchParams.get("challenge") ?? "";
  const portRaw = url.searchParams.get("port") ?? "";
  if (!STATE_RE.test(state) || !CHALLENGE_RE.test(challenge) || !PORT_RE.test(portRaw)) return page(INVALID, 400);
  const port = Number(portRaw);
  if (!Number.isInteger(port) || port < 1024 || port > 65535) return page(INVALID, 400);
  if (!brokerBase(env)) return page("The sign-in service isn't configured yet.", 500);

  // A state is good for exactly one sign-in: the `seen:` marker is written alongside the pending entry and
  // outlives its consumption. (No read of `pending:` here — a miss would be cached by KV for up to 60 s and could
  // hide the entry from /callback.)
  if ((await env.PENDING.get(kSeen(state))) !== null) return page(EXPIRED, 400);
  const pending: Pending = { provider, challenge, port };
  await env.PENDING.put(kSeen(state), "1", { expirationTtl: PENDING_TTL });
  await env.PENDING.put(kPending(state), JSON.stringify(pending), { expirationTtl: PENDING_TTL });
  return redirect(authorizeUrl(provider, env, state));
}

async function handleCallback(provider: string, url: URL, env: Env): Promise<Response> {
  const state = url.searchParams.get("state") ?? "";
  if (!isProvider(provider) || !STATE_RE.test(state)) return page(EXPIRED, 400);

  const raw = await env.PENDING.get(kPending(state));
  if (raw === null) return page(EXPIRED, 400);
  await env.PENDING.delete(kPending(state));

  let pending: Pending;
  try {
    pending = JSON.parse(raw) as Pending;
  } catch {
    return page(EXPIRED, 400);
  }
  if (pending.provider !== provider) return page(EXPIRED, 400);
  const port = pending.port;
  if (!Number.isInteger(port) || port < 1024 || port > 65535) return page(EXPIRED, 400);

  const name = DISPLAY_NAME[provider];
  const code = url.searchParams.get("code");
  let result: Result;
  if (url.searchParams.has("error") || !code) {
    result = { provider, error: `${name} access wasn't granted.` };
  } else {
    try {
      result = await exchange(provider, env, code);
    } catch (err) {
      // ProviderError messages carry only an HTTP status / field name, never response bodies or secrets.
      console.warn("code exchange failed", provider, err instanceof ProviderError ? err.message : "network error");
      result = { provider, error: `Couldn't finish signing in to ${name}. Start again from the app.` };
    }
  }

  const pickup = b64urlEncode(crypto.getRandomValues(new Uint8Array(32)));
  const stored: StoredResult = {
    challenge: pending.challenge,
    pickup_hash: await challengeOf(pickup),
    sealed: await seal(result, env.RESULT_KEY, state),
  };
  await env.PENDING.put(kResult(state), JSON.stringify(stored), { expirationTtl: RESULT_TTL });
  return redirect(
    `http://127.0.0.1:${port}/api/connect/finish?state=${encodeURIComponent(state)}&pickup=${encodeURIComponent(pickup)}`,
  );
}

async function handleRedeem(request: Request, env: Env): Promise<Response> {
  const body = await readJson(request);
  const state = body?.state;
  const verifier = body?.verifier;
  const pickup = typeof body?.pickup === "string" ? body.pickup : "";
  if (typeof state !== "string" || typeof verifier !== "string" || !STATE_RE.test(state) || !VERIFIER_RE.test(verifier)) {
    return json({ error: "Expected JSON {state, verifier, pickup}." }, 400);
  }
  const raw = await env.PENDING.get(kResult(state));
  if (raw === null) return json({ error: EXPIRED }, 404);
  // Single use either way: a wrong verifier or a wrong/missing pickup burns the entry too.
  await env.PENDING.delete(kResult(state));

  let stored: StoredResult;
  try {
    stored = JSON.parse(raw) as StoredResult;
  } catch {
    return json({ error: EXPIRED }, 404);
  }
  // Evaluate both checks before branching so timing doesn't reveal which one failed.
  const verifierOk = constantTimeEqual(await challengeOf(verifier), stored.challenge ?? "");
  const pickupOk =
    PICKUP_RE.test(pickup) &&
    typeof stored.pickup_hash === "string" &&
    constantTimeEqual(await challengeOf(pickup), stored.pickup_hash);
  if (!verifierOk || !pickupOk) {
    return json({ error: "This sign-in couldn't be confirmed." }, 403);
  }
  let result: unknown;
  try {
    result = await open(stored.sealed, env.RESULT_KEY, state);
  } catch {
    return json({ error: EXPIRED }, 404);
  }
  return json(result, 200);
}

async function handleRefreshJira(request: Request, env: Env): Promise<Response> {
  const body = await readJson(request);
  const rt = body?.refresh_token;
  if (typeof rt !== "string" || rt.length === 0 || rt.length > 4096) {
    return json({ error: "Expected JSON {refresh_token}." }, 400);
  }
  const out = await refreshJira(env, rt);
  if (!out.ok) return json({ error: out.error }, out.status);
  return json({ access_token: out.access_token, refresh_token: out.refresh_token, expires_in: out.expires_in });
}

// ---------- router ----------

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    const parts = url.pathname.split("/").filter(Boolean);
    const method = request.method;

    if (parts.length === 1 && parts[0] === "health") {
      return method === "GET" || method === "HEAD" ? text("ok", 200) : text("method not allowed", 405);
    }
    if (parts.length === 2 && parts[0] === "start") {
      return method === "GET" ? handleStart(parts[1], url, env) : text("method not allowed", 405);
    }
    if (parts.length === 2 && parts[0] === "callback") {
      return method === "GET" ? handleCallback(parts[1], url, env) : text("method not allowed", 405);
    }
    if (parts.length === 1 && parts[0] === "redeem") {
      return method === "POST" ? handleRedeem(request, env) : text("method not allowed", 405);
    }
    if (parts.length === 2 && parts[0] === "refresh" && parts[1] === "jira") {
      return method === "POST" ? handleRefreshJira(request, env) : text("method not allowed", 405);
    }
    return text("not found", 404);
  },
} satisfies ExportedHandler<Env>;
