import { SELF, env, fetchMock } from "cloudflare:test";
import { afterEach, beforeAll, describe, expect, it } from "vitest";

const BROKER = "https://broker.test";
const PORT = 47821;

// ---------- helpers (independent of src/, so the tests check the contract, not the implementation) ----------

function b64url(bytes: Uint8Array): string {
  let s = "";
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function randomId(n = 32): string {
  return b64url(crypto.getRandomValues(new Uint8Array(n)));
}

async function pkce(): Promise<{ verifier: string; challenge: string }> {
  const verifier = randomId(32);
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  return { verifier, challenge: b64url(new Uint8Array(digest)) };
}

function start(provider: string, params: Record<string, string | number>): Promise<Response> {
  const qs = new URLSearchParams(Object.entries(params).map(([k, v]) => [k, String(v)]));
  return SELF.fetch(`${BROKER}/start/${provider}?${qs}`, { redirect: "manual" });
}

function callback(provider: string, params: Record<string, string>): Promise<Response> {
  const qs = new URLSearchParams(params);
  return SELF.fetch(`${BROKER}/callback/${provider}?${qs}`, { redirect: "manual" });
}

function redeem(state: string, verifier: string): Promise<Response> {
  return SELF.fetch(`${BROKER}/redeem`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ state, verifier }),
  });
}

/** start → (provider approves) → callback; returns the state/verifier to redeem with. */
async function signIn(provider: string, cb: Record<string, string> = { code: "the-code" }) {
  const { verifier, challenge } = await pkce();
  const state = randomId(32);
  const s = await start(provider, { state, challenge, port: PORT });
  expect(s.status).toBe(302);
  const c = await callback(provider, { ...cb, state });
  return { state, verifier, callbackResponse: c };
}

function mockNotionToken() {
  fetchMock
    .get("https://api.notion.com")
    .intercept({
      path: "/v1/oauth/token",
      method: "POST",
      headers: { authorization: `Basic ${btoa("notion-id:notion-secret")}` },
      body: (b) => {
        const j = JSON.parse(b);
        return (
          j.grant_type === "authorization_code" &&
          j.code === "the-code" &&
          j.redirect_uri === `${BROKER}/callback/notion`
        );
      },
    })
    .reply(200, {
      access_token: "secret_notion_token",
      workspace_id: "ws-1",
      workspace_name: "Acme Wiki",
      bot_id: "bot-1",
      owner: { type: "user" },
    });
}

function mockGoogle() {
  fetchMock
    .get("https://oauth2.googleapis.com")
    .intercept({
      path: "/token",
      method: "POST",
      body: (b) => {
        const p = new URLSearchParams(b);
        return p.get("client_secret") === "google-secret" && p.get("code") === "the-code";
      },
    })
    .reply(200, { access_token: "ya29.google-token", id_token: "x.y.z", expires_in: 3599 });
  fetchMock
    .get("https://openidconnect.googleapis.com")
    .intercept({ path: "/v1/userinfo", headers: { authorization: "Bearer ya29.google-token" } })
    .reply(200, {
      sub: "1234567890",
      email: "ada@example.com",
      email_verified: true,
      name: "Ada Lovelace",
    });
}

function mockGithub() {
  fetchMock
    .get("https://github.com")
    .intercept({
      path: "/login/oauth/access_token",
      method: "POST",
      headers: { accept: "application/json" },
      body: (b) => {
        const j = JSON.parse(b);
        return j.client_secret === "github-secret" && j.code === "the-code";
      },
    })
    .reply(200, { access_token: "gho_github-token", token_type: "bearer", scope: "read:user,user:email" });
  fetchMock
    .get("https://api.github.com")
    .intercept({ path: "/user", headers: { authorization: "Bearer gho_github-token", "user-agent": /.+/ } })
    .reply(200, { id: 42, login: "octo", name: null, email: null });
  fetchMock
    .get("https://api.github.com")
    .intercept({ path: "/user/emails", headers: { authorization: "Bearer gho_github-token", "user-agent": /.+/ } })
    .reply(200, [
      { email: "old@example.com", primary: false, verified: true },
      { email: "unverified@example.com", primary: true, verified: false },
      { email: "octo@example.com", primary: true, verified: true },
    ]);
}

beforeAll(() => {
  fetchMock.activate();
  fetchMock.disableNetConnect();
});

afterEach(() => {
  fetchMock.assertNoPendingInterceptors();
});

// ---------- /start ----------

describe("start", () => {
  it("start_redirects_to_provider", async () => {
    const cases: Array<[string, string, string, Record<string, string>]> = [
      ["notion", "api.notion.com", "/v1/oauth/authorize", { owner: "user", client_id: "notion-id", response_type: "code" }],
      [
        "jira",
        "auth.atlassian.com",
        "/authorize",
        {
          scope: "read:jira-work read:jira-user offline_access",
          prompt: "consent",
          audience: "api.atlassian.com",
          client_id: "jira-id",
          response_type: "code",
        },
      ],
      ["google", "accounts.google.com", "/o/oauth2/v2/auth", { scope: "openid email profile", client_id: "google-id", response_type: "code" }],
      ["github", "github.com", "/login/oauth/authorize", { scope: "read:user user:email", client_id: "github-id" }],
    ];
    for (const [provider, host, path, expected] of cases) {
      const { challenge } = await pkce();
      const state = randomId(32);
      const res = await start(provider, { state, challenge, port: PORT });
      expect(res.status, provider).toBe(302);
      const loc = new URL(res.headers.get("location")!);
      expect(loc.protocol).toBe("https:");
      expect(loc.host, provider).toBe(host);
      expect(loc.pathname, provider).toBe(path);
      expect(loc.searchParams.get("redirect_uri")).toBe(`${BROKER}/callback/${provider}`);
      expect(loc.searchParams.get("state")).toBe(state);
      for (const [k, v] of Object.entries(expected)) expect(loc.searchParams.get(k), `${provider} ${k}`).toBe(v);
      // The secret never appears in the browser-visible URL, nor does the challenge.
      expect(loc.toString()).not.toContain("secret");
      expect(loc.toString()).not.toContain(challenge);
    }
  });

  it("start_rejects_bad_input", async () => {
    const { challenge } = await pkce();
    const good = { state: randomId(32), challenge, port: PORT };
    const bad: Array<[string, Record<string, string | number>]> = [
      ["dropbox", good],
      ["notion", { ...good, state: randomId(32), port: 80 }],
      ["notion", { ...good, state: randomId(32), port: 70000 }],
      ["notion", { ...good, state: randomId(32), port: "47821abc" }],
      ["notion", { ...good, state: randomId(32), port: "1e4" }],
      ["notion", { ...good, state: "short" }],
      ["notion", { ...good, state: randomId(32).slice(0, 30) + "<>" }],
      ["notion", { ...good, state: randomId(32), challenge: "not-a-challenge" }],
      ["notion", { ...good, state: randomId(32), challenge: challenge + "=" }],
      ["notion", { state: randomId(32), port: PORT }],
    ];
    for (const [provider, params] of bad) {
      const res = await start(provider, params);
      expect(res.status, `${provider} ${JSON.stringify(params)}`).toBe(400);
      expect(res.headers.get("location")).toBeNull();
      expect(res.headers.get("content-type")).toMatch(/text\/html/);
    }
  });

  it("start_rejects_reused_state", async () => {
    const { challenge } = await pkce();
    const state = randomId(32);
    expect((await start("notion", { state, challenge, port: PORT })).status).toBe(302);
    // Single use rests on the `seen:` marker alone (no read of `pending:` at /start, which KV would negative-cache).
    expect(await env.PENDING.get(`seen:${state}`)).not.toBeNull();
    const again = await start("notion", { state, challenge, port: PORT });
    expect(again.status).toBe(400);
    expect(again.headers.get("location")).toBeNull();
  });

  it("start_rejects_state_reused_after_callback", async () => {
    const { state, callbackResponse } = await signIn("notion", { error: "access_denied" });
    expect(callbackResponse.status).toBe(302);
    const { challenge } = await pkce();
    const again = await start("notion", { state, challenge, port: PORT });
    expect(again.status).toBe(400);
  });
});

// ---------- full flows ----------

describe("flows", () => {
  it("notion_flow_round_trip", async () => {
    mockNotionToken();
    const { state, verifier, callbackResponse } = await signIn("notion");
    expect(callbackResponse.status).toBe(302);
    expect(callbackResponse.headers.get("location")).toBe(
      `http://127.0.0.1:${PORT}/api/connect/finish?state=${state}`,
    );
    const res = await redeem(state, verifier);
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({
      provider: "notion",
      access_token: "secret_notion_token",
      workspace_id: "ws-1",
      workspace_name: "Acme Wiki",
      bot_id: "bot-1",
    });
  });

  it("jira_flow_includes_site", async () => {
    fetchMock
      .get("https://auth.atlassian.com")
      .intercept({
        path: "/oauth/token",
        method: "POST",
        body: (b) => {
          const j = JSON.parse(b);
          return (
            j.grant_type === "authorization_code" &&
            j.client_id === "jira-id" &&
            j.client_secret === "jira-secret" &&
            j.code === "the-code" &&
            j.redirect_uri === `${BROKER}/callback/jira`
          );
        },
      })
      .reply(200, { access_token: "jira-at", refresh_token: "jira-rt", expires_in: 3600, scope: "x" });
    fetchMock
      .get("https://api.atlassian.com")
      .intercept({ path: "/oauth/token/accessible-resources", headers: { authorization: "Bearer jira-at" } })
      .reply(200, [
        { id: "cloud-1", url: "https://acme.atlassian.net", name: "acme", scopes: [] },
        { id: "cloud-2", url: "https://other.atlassian.net", name: "other", scopes: [] },
      ]);
    const { state, verifier } = await signIn("jira");
    const res = await redeem(state, verifier);
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({
      provider: "jira",
      access_token: "jira-at",
      refresh_token: "jira-rt",
      expires_in: 3600,
      cloud_id: "cloud-1",
      site_url: "https://acme.atlassian.net",
      site_name: "acme",
    });
  });

  it("google_and_github_return_identity_only", async () => {
    mockGoogle();
    const g = await signIn("google");
    const gr = await redeem(g.state, g.verifier);
    expect(gr.status).toBe(200);
    const gj = (await gr.json()) as Record<string, unknown>;
    expect(gj).toEqual({
      provider: "google",
      provider_user_id: "1234567890",
      email: "ada@example.com",
      name: "Ada Lovelace",
    });
    expect(gj).not.toHaveProperty("access_token");

    mockGithub();
    const h = await signIn("github");
    const hr = await redeem(h.state, h.verifier);
    expect(hr.status).toBe(200);
    const hj = (await hr.json()) as Record<string, unknown>;
    expect(hj).toEqual({
      provider: "github",
      provider_user_id: "42",
      email: "octo@example.com",
      name: "octo",
    });
    expect(hj).not.toHaveProperty("access_token");
  });

  it("callback_error_result", async () => {
    const { state, verifier, callbackResponse } = await signIn("notion", { error: "access_denied" });
    expect(callbackResponse.status).toBe(302);
    expect(callbackResponse.headers.get("location")).toBe(
      `http://127.0.0.1:${PORT}/api/connect/finish?state=${state}`,
    );
    const res = await redeem(state, verifier);
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ provider: "notion", error: "Notion access wasn't granted." });
  });

  it("callback_failed_exchange_is_an_error_result", async () => {
    fetchMock
      .get("https://api.notion.com")
      .intercept({ path: "/v1/oauth/token", method: "POST" })
      .reply(400, { error: "invalid_grant" });
    const { state, verifier, callbackResponse } = await signIn("notion");
    expect(callbackResponse.status).toBe(302);
    const res = await redeem(state, verifier);
    expect(res.status).toBe(200);
    const body = (await res.json()) as { provider: string; error: string };
    expect(body.provider).toBe("notion");
    expect(body.error).toMatch(/Notion/);
  });

  it("callback_unknown_state_400", async () => {
    const res = await callback("notion", { code: "the-code", state: randomId(32) });
    expect(res.status).toBe(400);
    expect(res.headers.get("location")).toBeNull();
    expect(res.headers.get("content-type")).toMatch(/text\/html/);
    expect(await res.text()).toContain("This sign-in link has expired. Start again from the app.");
  });

  it("callback_state_cannot_be_used_twice", async () => {
    const { state, callbackResponse } = await signIn("notion", { error: "access_denied" });
    expect(callbackResponse.status).toBe(302);
    const again = await callback("notion", { error: "access_denied", state });
    expect(again.status).toBe(400);
  });

  it("callback_provider_must_match_start", async () => {
    const { challenge } = await pkce();
    const state = randomId(32);
    await start("notion", { state, challenge, port: PORT });
    const res = await callback("google", { error: "access_denied", state });
    expect(res.status).toBe(400);
    expect(res.headers.get("location")).toBeNull();
  });
});

// ---------- /redeem ----------

describe("redeem", () => {
  it("redeem_wrong_verifier_403_then_gone", async () => {
    mockNotionToken();
    const { state, verifier } = await signIn("notion");
    const wrong = await redeem(state, (await pkce()).verifier);
    expect(wrong.status).toBe(403);
    const right = await redeem(state, verifier);
    expect(right.status).toBe(404);
  });

  it("redeem_twice_404", async () => {
    mockNotionToken();
    const { state, verifier } = await signIn("notion");
    expect((await redeem(state, verifier)).status).toBe(200);
    expect((await redeem(state, verifier)).status).toBe(404);
  });

  it("redeem_unknown_state_404", async () => {
    expect((await redeem(randomId(32), (await pkce()).verifier)).status).toBe(404);
  });

  it("redeem_bad_body_400", async () => {
    const res = await SELF.fetch(`${BROKER}/redeem`, { method: "POST", body: "not json" });
    expect(res.status).toBe(400);
    const res2 = await SELF.fetch(`${BROKER}/redeem`, { method: "POST", body: JSON.stringify({ state: 1 }) });
    expect(res2.status).toBe(400);
  });

  it("result_is_encrypted_in_kv", async () => {
    mockGoogle();
    const { state } = await signIn("google");
    const raw = await env.PENDING.get(`result:${state}`);
    expect(raw).not.toBeNull();
    expect(raw).not.toContain("ada@example.com");
    expect(raw).not.toContain("Ada Lovelace");
    expect(raw).not.toContain("1234567890");

    mockNotionToken();
    const n = await signIn("notion");
    const rawN = await env.PENDING.get(`result:${n.state}`);
    expect(rawN).not.toBeNull();
    expect(rawN).not.toContain("secret_notion_token");
    expect(rawN).not.toContain("Acme Wiki");
  });
});

// ---------- /refresh/jira ----------

describe("refresh", () => {
  it("refresh_jira", async () => {
    fetchMock
      .get("https://auth.atlassian.com")
      .intercept({
        path: "/oauth/token",
        method: "POST",
        body: (b) => {
          const j = JSON.parse(b);
          return (
            j.grant_type === "refresh_token" &&
            j.client_id === "jira-id" &&
            j.client_secret === "jira-secret" &&
            j.refresh_token === "old-rt"
          );
        },
      })
      .reply(200, { access_token: "new-at", refresh_token: "new-rt", expires_in: 3600, scope: "x", token_type: "Bearer" });
    const ok = await SELF.fetch(`${BROKER}/refresh/jira`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ refresh_token: "old-rt" }),
    });
    expect(ok.status).toBe(200);
    expect(await ok.json()).toEqual({ access_token: "new-at", refresh_token: "new-rt", expires_in: 3600 });

    fetchMock
      .get("https://auth.atlassian.com")
      .intercept({ path: "/oauth/token", method: "POST" })
      .reply(400, { error: "invalid_grant", error_description: "Unknown or invalid refresh token." });
    const bad = await SELF.fetch(`${BROKER}/refresh/jira`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ refresh_token: "revoked-rt" }),
    });
    expect(bad.status).toBe(400);
    const err = (await bad.json()) as { error: string };
    expect(typeof err.error).toBe("string");
    expect(err.error.length).toBeGreaterThan(0);

    const missing = await SELF.fetch(`${BROKER}/refresh/jira`, { method: "POST", body: "{}" });
    expect(missing.status).toBe(400);
  });
});

// ---------- misc ----------

describe("misc", () => {
  it("health_ok", async () => {
    const res = await SELF.fetch(`${BROKER}/health`);
    expect(res.status).toBe(200);
    expect(await res.text()).toBe("ok");
  });

  it("unknown_route_404", async () => {
    expect((await SELF.fetch(`${BROKER}/nope`)).status).toBe(404);
  });
});
