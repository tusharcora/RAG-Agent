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

/** Right shape for a pickup but with a character the format forbids. */
function pickupLike(): string {
  return randomId(32).slice(0, 42) + "*";
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

function redeem(state: string, verifier: string, pickup?: string): Promise<Response> {
  const body: Record<string, string> = { state, verifier };
  if (pickup !== undefined) body.pickup = pickup;
  return SELF.fetch(`${BROKER}/redeem`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

/** The loopback finish URL a callback redirected to, split into its parts. */
function finishOf(res: Response): { origin: string; path: string; state: string | null; pickup: string } {
  const loc = new URL(res.headers.get("location")!);
  const keys = [...loc.searchParams.keys()].sort();
  expect(keys).toEqual(["pickup", "state"]);
  const pickup = loc.searchParams.get("pickup")!;
  expect(pickup).toMatch(/^[A-Za-z0-9_-]{43}$/);
  return { origin: loc.origin, path: loc.pathname, state: loc.searchParams.get("state"), pickup };
}

/** start → (provider approves) → callback; returns the state/verifier/pickup to redeem with. */
async function signIn(provider: string, cb: Record<string, string> = { code: "the-code" }) {
  const { verifier, challenge } = await pkce();
  const state = randomId(32);
  const s = await start(provider, { state, challenge, port: PORT });
  expect(s.status).toBe(302);
  const c = await callback(provider, { ...cb, state });
  const pickup = c.status === 302 ? finishOf(c).pickup : "";
  return { state, verifier, pickup, callbackResponse: c };
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
    const { state, verifier, pickup, callbackResponse } = await signIn("notion");
    expect(callbackResponse.status).toBe(302);
    expect(finishOf(callbackResponse)).toEqual({
      origin: `http://127.0.0.1:${PORT}`,
      path: "/api/connect/finish",
      state,
      pickup,
    });
    const res = await redeem(state, verifier, pickup);
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
    const { state, verifier, pickup } = await signIn("jira");
    const res = await redeem(state, verifier, pickup);
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
    const gr = await redeem(g.state, g.verifier, g.pickup);
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
    const hr = await redeem(h.state, h.verifier, h.pickup);
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
    const { state, verifier, pickup, callbackResponse } = await signIn("notion", { error: "access_denied" });
    expect(callbackResponse.status).toBe(302);
    expect(finishOf(callbackResponse).state).toBe(state);
    const res = await redeem(state, verifier, pickup);
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ provider: "notion", error: "Notion access wasn't granted." });
  });

  it("callback_failed_exchange_is_an_error_result", async () => {
    fetchMock
      .get("https://api.notion.com")
      .intercept({ path: "/v1/oauth/token", method: "POST" })
      .reply(400, { error: "invalid_grant" });
    const { state, verifier, pickup, callbackResponse } = await signIn("notion");
    expect(callbackResponse.status).toBe(302);
    const res = await redeem(state, verifier, pickup);
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
    const { state, verifier, pickup } = await signIn("notion");
    const wrong = await redeem(state, (await pkce()).verifier, pickup);
    expect(wrong.status).toBe(403);
    const right = await redeem(state, verifier, pickup);
    expect(right.status).toBe(404);
  });

  it("redeem_wrong_pickup_403_then_gone", async () => {
    mockNotionToken();
    const { state, verifier, pickup } = await signIn("notion");
    const wrong = await redeem(state, verifier, randomId(32));
    expect(wrong.status).toBe(403);
    const right = await redeem(state, verifier, pickup);
    expect(right.status).toBe(404);
  });

  it("redeem_missing_pickup_403_then_gone", async () => {
    for (const bad of [undefined, "", "short", pickupLike()]) {
      mockNotionToken();
      const { state, verifier, pickup } = await signIn("notion");
      const res = await redeem(state, verifier, bad);
      expect(res.status, String(bad)).toBe(403);
      expect((await redeem(state, verifier, pickup)).status).toBe(404);
    }
  });

  it("pickup_is_not_stored_in_kv", async () => {
    mockNotionToken();
    const { state, pickup } = await signIn("notion");
    const raw = await env.PENDING.get(`result:${state}`);
    expect(raw).not.toBeNull();
    expect(raw).not.toContain(pickup);
  });

  it("redeem_twice_404", async () => {
    mockNotionToken();
    const { state, verifier, pickup } = await signIn("notion");
    expect((await redeem(state, verifier, pickup)).status).toBe(200);
    expect((await redeem(state, verifier, pickup)).status).toBe(404);
  });

  it("redeem_unknown_state_404", async () => {
    expect((await redeem(randomId(32), (await pkce()).verifier, randomId(32))).status).toBe(404);
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

  it("refresh_jira_refusals_are_400", async () => {
    for (const status of [401, 403]) {
      fetchMock
        .get("https://auth.atlassian.com")
        .intercept({ path: "/oauth/token", method: "POST" })
        .reply(status, { error: "unauthorized_client" });
      const res = await SELF.fetch(`${BROKER}/refresh/jira`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ refresh_token: "revoked-rt" }),
      });
      expect(res.status, String(status)).toBe(400);
    }
  });

  it("refresh_jira_transient_failures_are_502", async () => {
    const cases: Array<[number, unknown]> = [
      [429, { error: "rate_limited" }],
      [500, "oops"],
      [503, { error: "unavailable" }],
      [404, { error: "not_found" }],
      [200, { no_token: true }],
    ];
    for (const [status, body] of cases) {
      fetchMock.get("https://auth.atlassian.com").intercept({ path: "/oauth/token", method: "POST" }).reply(status, body as any);
      const res = await SELF.fetch(`${BROKER}/refresh/jira`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ refresh_token: "some-rt" }),
      });
      expect(res.status, String(status)).toBe(502);
    }
    fetchMock.get("https://auth.atlassian.com").intercept({ path: "/oauth/token", method: "POST" }).replyWithError(new Error("down"));
    const down = await SELF.fetch(`${BROKER}/refresh/jira`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ refresh_token: "some-rt" }),
    });
    expect(down.status).toBe(502);
  });
});

// ---------- github_repos (GitHub App user-to-server) ----------

function ghToken(reply: Record<string, unknown>, status = 200) {
  fetchMock
    .get("https://github.com")
    .intercept({
      path: "/login/oauth/access_token",
      method: "POST",
      headers: { accept: "application/json" },
      body: (b) => {
        const j = JSON.parse(b);
        return (
          j.client_id === "ghapp-id" &&
          j.client_secret === "ghapp-secret" &&
          j.code === "the-code" &&
          j.redirect_uri === `${BROKER}/callback/github_repos`
        );
      },
    })
    .reply(status, reply);
}

const GH_TOKEN_OK = {
  access_token: "ghu_access",
  expires_in: 28800,
  refresh_token: "ghr_refresh",
  refresh_token_expires_in: 15811200,
  token_type: "bearer",
  scope: "",
};

function ghUser() {
  fetchMock
    .get("https://api.github.com")
    .intercept({ path: "/user", headers: { authorization: "Bearer ghu_access", "user-agent": /.+/ } })
    .reply(200, { id: 4242, login: "octo", name: "Octo", email: "private@example.com" });
}

function ghInstall(n: number, offset = 0) {
  return Array.from({ length: n }, (_, i) => ({
    id: offset + i + 1,
    account: { login: `acct-${offset + i + 1}`, type: i % 2 ? "Organization" : "User" },
    app_slug: "x",
  }));
}

describe("github_repos", () => {
  it("start github_repos redirects to OAuth authorize with state", async () => {
    const { challenge } = await pkce();
    const state = randomId(32);
    const res = await start("github_repos", { state, challenge, port: PORT });
    expect(res.status).toBe(302);
    const loc = new URL(res.headers.get("location")!);
    expect(loc.host).toBe("github.com");
    expect(loc.pathname).toBe("/login/oauth/authorize");
    expect(loc.searchParams.get("client_id")).toBe("ghapp-id");
    expect(loc.searchParams.get("redirect_uri")).toBe(`${BROKER}/callback/github_repos`);
    expect(loc.searchParams.get("redirect_uri")!.endsWith("/callback/github_repos")).toBe(true);
    expect(loc.searchParams.get("state")).toBe(state);
    expect(loc.toString()).not.toContain("secret");
  });

  it("callback github_repos returns token, login and all installation pages", async () => {
    ghToken(GH_TOKEN_OK);
    ghUser();
    fetchMock
      .get("https://api.github.com")
      .intercept({ path: "/user/installations?per_page=100&page=1", headers: { authorization: "Bearer ghu_access" } })
      .reply(200, { total_count: 101, installations: ghInstall(100) }, {
        headers: { link: '<https://api.github.com/user/installations?per_page=100&page=2>; rel="next", <https://api.github.com/user/installations?per_page=100&page=2>; rel="last"' },
      });
    fetchMock
      .get("https://api.github.com")
      .intercept({ path: "/user/installations?per_page=100&page=2", headers: { authorization: "Bearer ghu_access" } })
      .reply(200, { total_count: 101, installations: ghInstall(1, 100) });
    const { state, verifier, pickup, callbackResponse } = await signIn("github_repos");
    expect(callbackResponse.status).toBe(302);
    const res = await redeem(state, verifier, pickup);
    expect(res.status).toBe(200);
    const j = (await res.json()) as any;
    expect(j.installations.length).toBe(101);
    expect(j.installations[0]).toEqual({ id: 1, account_login: "acct-1", account_type: "User" });
    expect(j.installations[1].account_type).toBe("Organization");
    expect(j.installations[100]).toEqual({ id: 101, account_login: "acct-101", account_type: "User" });
    delete j.installations;
    expect(j).toEqual({
      provider: "github_repos",
      access_token: "ghu_access",
      refresh_token: "ghr_refresh",
      expires_in: 28800,
      refresh_token_expires_in: 15811200,
      user_id: 4242,
      login: "octo",
      app_slug: "knowledge-agent-test",
    });
  });

  it("callback github_repos treats 200 with error as denied", async () => {
    ghToken({ error: "bad_verification_code", error_description: "The code passed is incorrect or expired." });
    const { state, verifier, pickup } = await signIn("github_repos");
    const res = await redeem(state, verifier, pickup);
    expect(await res.json()).toEqual({ provider: "github_repos", error: "GitHub access wasn't granted." });
  });

  it("callback github_repos with ?error=access_denied is denied", async () => {
    const { state, verifier, pickup } = await signIn("github_repos", { error: "access_denied" });
    const res = await redeem(state, verifier, pickup);
    expect(await res.json()).toEqual({ provider: "github_repos", error: "GitHub access wasn't granted." });
  });

  it("callback github_repos with zero installations", async () => {
    ghToken(GH_TOKEN_OK);
    ghUser();
    fetchMock
      .get("https://api.github.com")
      .intercept({ path: "/user/installations?per_page=100&page=1" })
      .reply(200, { total_count: 0, installations: [] });
    const { state, verifier, pickup } = await signIn("github_repos");
    const j = (await (await redeem(state, verifier, pickup)).json()) as any;
    expect(j.error).toBeUndefined();
    expect(j.installations).toEqual([]);
    expect(j.login).toBe("octo");
  });

  it("callback github_repos does not follow a next link to another host", async () => {
    ghToken(GH_TOKEN_OK);
    ghUser();
    fetchMock
      .get("https://api.github.com")
      .intercept({ path: "/user/installations?per_page=100&page=1" })
      .reply(200, { installations: ghInstall(1) }, { headers: { link: '<https://evil.example/steal>; rel="next"' } });
    const { state, verifier, pickup } = await signIn("github_repos");
    const j = (await (await redeem(state, verifier, pickup)).json()) as any;
    expect(j.error).toMatch(/GitHub/);
  });

  it("callback github_repos result is encrypted in KV", async () => {
    ghToken(GH_TOKEN_OK);
    ghUser();
    fetchMock
      .get("https://api.github.com")
      .intercept({ path: "/user/installations?per_page=100&page=1" })
      .reply(200, { installations: [] });
    const { state } = await signIn("github_repos");
    const raw = await env.PENDING.get(`result:${state}`);
    expect(raw).not.toContain("ghu_access");
    expect(raw).not.toContain("ghr_refresh");
  });
});

describe("refresh github_repos", () => {
  function refresh(rt = "old-rt") {
    return SELF.fetch(`${BROKER}/refresh/github_repos`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ refresh_token: rt }),
    });
  }
  function ghRefreshReply(status: number, body: unknown) {
    fetchMock
      .get("https://github.com")
      .intercept({
        path: "/login/oauth/access_token",
        method: "POST",
        headers: { accept: "application/json" },
        body: (b) => {
          const j = JSON.parse(b);
          return (
            j.grant_type === "refresh_token" &&
            j.client_id === "ghapp-id" &&
            j.client_secret === "ghapp-secret" &&
            j.refresh_token === "old-rt"
          );
        },
      })
      .reply(status, body as any);
  }

  it("success passes the four fields through", async () => {
    ghRefreshReply(200, {
      access_token: "ghu_new",
      refresh_token: "ghr_new",
      expires_in: 28800,
      refresh_token_expires_in: 15811200,
      token_type: "bearer",
    });
    const res = await refresh();
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({
      access_token: "ghu_new",
      refresh_token: "ghr_new",
      expires_in: 28800,
      refresh_token_expires_in: 15811200,
    });
  });

  it("maps bad_refresh_token (200 body) to 400", async () => {
    ghRefreshReply(200, { error: "bad_refresh_token", error_description: "The refresh token passed is incorrect or expired." });
    const res = await refresh();
    expect(res.status).toBe(400);
    expect(typeof ((await res.json()) as any).error).toBe("string");
  });

  it("maps bad_refresh_token with 4xx and 401/403 to 400", async () => {
    ghRefreshReply(400, { error: "bad_refresh_token" });
    expect((await refresh()).status).toBe(400);
    ghRefreshReply(401, { message: "Bad credentials" });
    expect((await refresh()).status).toBe(400);
    ghRefreshReply(403, "forbidden");
    expect((await refresh()).status).toBe(400);
  });

  it("maps other errors and statuses to 502", async () => {
    ghRefreshReply(500, "oops");
    expect((await refresh()).status).toBe(502);
    ghRefreshReply(200, { error: "incorrect_client_credentials" });
    expect((await refresh()).status).toBe(502);
    ghRefreshReply(200, { no_token: true });
    expect((await refresh()).status).toBe(502);
    ghRefreshReply(429, { error: "rate_limited" });
    expect((await refresh()).status).toBe(502);
  });

  it("maps a network error to 502", async () => {
    fetchMock.get("https://github.com").intercept({ path: "/login/oauth/access_token", method: "POST" }).replyWithError(new Error("down"));
    expect((await refresh()).status).toBe(502);
  });

  it("rejects a missing refresh_token with 400", async () => {
    const res = await SELF.fetch(`${BROKER}/refresh/github_repos`, { method: "POST", body: "{}" });
    expect(res.status).toBe(400);
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
