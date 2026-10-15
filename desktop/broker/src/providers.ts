// Provider-specific authorize URLs and code exchanges. Client secrets are only ever sent server-to-server here.

export const PROVIDERS = ["notion", "jira", "google", "github"] as const;
export type Provider = (typeof PROVIDERS)[number];

export function isProvider(p: string): p is Provider {
  return (PROVIDERS as readonly string[]).includes(p);
}

export const DISPLAY_NAME: Record<Provider, string> = {
  notion: "Notion",
  jira: "Jira",
  google: "Google",
  github: "GitHub",
};

export interface Env {
  PENDING: KVNamespace;
  BROKER_URL: string;
  NOTION_CLIENT_ID: string;
  NOTION_CLIENT_SECRET: string;
  JIRA_CLIENT_ID: string;
  JIRA_CLIENT_SECRET: string;
  GOOGLE_CLIENT_ID: string;
  GOOGLE_CLIENT_SECRET: string;
  GITHUB_CLIENT_ID: string;
  GITHUB_CLIENT_SECRET: string;
  RESULT_KEY: string;
}

export type NotionResult = {
  provider: "notion";
  access_token: string;
  workspace_id: string;
  workspace_name: string | null;
  bot_id: string;
};
export type JiraResult = {
  provider: "jira";
  access_token: string;
  refresh_token: string;
  expires_in: number;
  cloud_id: string;
  site_url: string;
  site_name: string;
};
export type IdentityResult = {
  provider: "google" | "github";
  provider_user_id: string;
  email: string | null;
  name: string | null;
};
export type ErrorResult = { provider: Provider; error: string };
export type Result = NotionResult | JiraResult | IdentityResult | ErrorResult;

const URLS = {
  notion: { authorize: "https://api.notion.com/v1/oauth/authorize", token: "https://api.notion.com/v1/oauth/token" },
  jira: {
    authorize: "https://auth.atlassian.com/authorize",
    token: "https://auth.atlassian.com/oauth/token",
    sites: "https://api.atlassian.com/oauth/token/accessible-resources",
  },
  google: {
    authorize: "https://accounts.google.com/o/oauth2/v2/auth",
    token: "https://oauth2.googleapis.com/token",
    identity: "https://openidconnect.googleapis.com/v1/userinfo",
  },
  github: {
    authorize: "https://github.com/login/oauth/authorize",
    token: "https://github.com/login/oauth/access_token",
    user: "https://api.github.com/user",
    emails: "https://api.github.com/user/emails",
  },
} as const;

const USER_AGENT = "knowledge-agent-auth";

/** Error whose message is safe to show/log: never contains provider response bodies, codes or tokens. */
export class ProviderError extends Error {}

export function brokerBase(env: Env): string {
  return (env.BROKER_URL || "").replace(/\/+$/, "");
}

export function redirectUri(provider: Provider, env: Env): string {
  return `${brokerBase(env)}/callback/${provider}`;
}

export function authorizeUrl(provider: Provider, env: Env, state: string): string {
  const redirect_uri = redirectUri(provider, env);
  let base: string;
  let params: Record<string, string>;
  switch (provider) {
    case "notion":
      base = URLS.notion.authorize;
      params = { client_id: env.NOTION_CLIENT_ID, response_type: "code", owner: "user", redirect_uri, state };
      break;
    case "jira":
      base = URLS.jira.authorize;
      params = {
        audience: "api.atlassian.com",
        client_id: env.JIRA_CLIENT_ID,
        scope: "read:jira-work read:jira-user offline_access",
        redirect_uri,
        state,
        response_type: "code",
        prompt: "consent",
      };
      break;
    case "google":
      base = URLS.google.authorize;
      params = { client_id: env.GOOGLE_CLIENT_ID, response_type: "code", scope: "openid email profile", redirect_uri, state };
      break;
    case "github":
      base = URLS.github.authorize;
      params = { client_id: env.GITHUB_CLIENT_ID, scope: "read:user user:email", redirect_uri, state };
      break;
  }
  return `${base}?${new URLSearchParams(params)}`;
}

async function jsonOrThrow(res: Response, what: string): Promise<any> {
  if (!res.ok) throw new ProviderError(`${what} returned HTTP ${res.status}`);
  try {
    return await res.json();
  } catch {
    throw new ProviderError(`${what} returned a non-JSON body`);
  }
}

function requireString(v: unknown, what: string): string {
  if (typeof v !== "string" || v.length === 0) throw new ProviderError(`${what} missing`);
  return v;
}

function optString(v: unknown): string | null {
  return typeof v === "string" && v.length > 0 ? v : null;
}

export async function exchange(provider: Provider, env: Env, code: string): Promise<Result> {
  const redirect_uri = redirectUri(provider, env);
  switch (provider) {
    case "notion": {
      const res = await fetch(URLS.notion.token, {
        method: "POST",
        headers: {
          authorization: `Basic ${btoa(`${env.NOTION_CLIENT_ID}:${env.NOTION_CLIENT_SECRET}`)}`,
          "content-type": "application/json",
          accept: "application/json",
        },
        body: JSON.stringify({ grant_type: "authorization_code", code, redirect_uri }),
      });
      const j = await jsonOrThrow(res, "Notion token endpoint");
      return {
        provider: "notion",
        access_token: requireString(j.access_token, "Notion access_token"),
        workspace_id: requireString(j.workspace_id, "Notion workspace_id"),
        workspace_name: optString(j.workspace_name),
        bot_id: requireString(j.bot_id, "Notion bot_id"),
      };
    }
    case "jira": {
      const res = await fetch(URLS.jira.token, {
        method: "POST",
        headers: { "content-type": "application/json", accept: "application/json" },
        body: JSON.stringify({
          grant_type: "authorization_code",
          client_id: env.JIRA_CLIENT_ID,
          client_secret: env.JIRA_CLIENT_SECRET,
          code,
          redirect_uri,
        }),
      });
      const t = await jsonOrThrow(res, "Atlassian token endpoint");
      const access_token = requireString(t.access_token, "Atlassian access_token");
      const sitesRes = await fetch(URLS.jira.sites, {
        headers: { authorization: `Bearer ${access_token}`, accept: "application/json" },
      });
      const sites = await jsonOrThrow(sitesRes, "Atlassian accessible-resources");
      if (!Array.isArray(sites) || sites.length === 0) {
        return { provider: "jira", error: "Your Atlassian account has no Jira site this app can read." };
      }
      const site = sites[0];
      return {
        provider: "jira",
        access_token,
        refresh_token: requireString(t.refresh_token, "Atlassian refresh_token"),
        expires_in: typeof t.expires_in === "number" ? t.expires_in : 3600,
        cloud_id: requireString(site.id, "Atlassian site id"),
        site_url: requireString(site.url, "Atlassian site url"),
        site_name: optString(site.name) ?? "",
      };
    }
    case "google": {
      const res = await fetch(URLS.google.token, {
        method: "POST",
        headers: { "content-type": "application/x-www-form-urlencoded", accept: "application/json" },
        body: new URLSearchParams({
          grant_type: "authorization_code",
          client_id: env.GOOGLE_CLIENT_ID,
          client_secret: env.GOOGLE_CLIENT_SECRET,
          code,
          redirect_uri,
        }).toString(),
      });
      const t = await jsonOrThrow(res, "Google token endpoint");
      const token = requireString(t.access_token, "Google access_token");
      const u = await jsonOrThrow(
        await fetch(URLS.google.identity, { headers: { authorization: `Bearer ${token}`, accept: "application/json" } }),
        "Google userinfo",
      );
      // The provider token is discarded here: only the identity leaves this function.
      return {
        provider: "google",
        provider_user_id: requireString(u.sub, "Google sub"),
        email: u.email_verified === true ? optString(u.email) : null,
        name: optString(u.name),
      };
    }
    case "github": {
      const res = await fetch(URLS.github.token, {
        method: "POST",
        headers: { "content-type": "application/json", accept: "application/json", "user-agent": USER_AGENT },
        body: JSON.stringify({
          client_id: env.GITHUB_CLIENT_ID,
          client_secret: env.GITHUB_CLIENT_SECRET,
          code,
          redirect_uri,
        }),
      });
      const t = await jsonOrThrow(res, "GitHub token endpoint");
      // GitHub reports a bad code as HTTP 200 with an `error` field.
      const token = requireString(t.access_token, "GitHub access_token");
      const headers = {
        authorization: `Bearer ${token}`,
        accept: "application/vnd.github+json",
        "user-agent": USER_AGENT,
      };
      const u = await jsonOrThrow(await fetch(URLS.github.user, { headers }), "GitHub /user");
      const emails = await jsonOrThrow(await fetch(URLS.github.emails, { headers }), "GitHub /user/emails");
      const primary = Array.isArray(emails)
        ? emails.find((e: any) => e && e.primary === true && e.verified === true && typeof e.email === "string")
        : undefined;
      if (u.id === undefined || u.id === null) throw new ProviderError("GitHub user id missing");
      return {
        provider: "github",
        provider_user_id: String(u.id),
        email: primary ? primary.email : null,
        name: optString(u.name) ?? optString(u.login),
      };
    }
  }
}

export type RefreshOutcome =
  | { ok: true; access_token: string; refresh_token: string; expires_in: number }
  | { ok: false; status: number; error: string };

export async function refreshJira(env: Env, refresh_token: string): Promise<RefreshOutcome> {
  let res: Response;
  try {
    res = await fetch(URLS.jira.token, {
      method: "POST",
      headers: { "content-type": "application/json", accept: "application/json" },
      body: JSON.stringify({
        grant_type: "refresh_token",
        client_id: env.JIRA_CLIENT_ID,
        client_secret: env.JIRA_CLIENT_SECRET,
        refresh_token,
      }),
    });
  } catch {
    return { ok: false, status: 502, error: "Couldn't reach Atlassian." };
  }
  let j: any = null;
  try {
    j = await res.json();
  } catch {
    /* fall through */
  }
  if (res.ok && j && typeof j.access_token === "string") {
    return {
      ok: true,
      access_token: j.access_token,
      // Atlassian rotates refresh tokens; keep the old one if a response ever omits it.
      refresh_token: typeof j.refresh_token === "string" ? j.refresh_token : refresh_token,
      expires_in: typeof j.expires_in === "number" ? j.expires_in : 3600,
    };
  }
  // Only a definite refusal of the refresh token (400/401/403) is a 400 here — that's what tells the app to ask the
  // person to reconnect Jira. Anything else (rate limit, Atlassian outage, a 200 without a token) is transient: 502.
  if (res.status === 400 || res.status === 401 || res.status === 403) {
    const msg =
      (j && (optString(j.error_description) ?? optString(j.error))) || `Atlassian returned HTTP ${res.status}`;
    return { ok: false, status: 400, error: msg };
  }
  return { ok: false, status: 502, error: `Atlassian returned HTTP ${res.status}` };
}
