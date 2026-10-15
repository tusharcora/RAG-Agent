"use client";

import { useCallback, useEffect, useState } from "react";
import { Copy01 } from "@untitledui/icons";
import { createServiceToken, listServiceTokens, revokeServiceToken } from "@/lib/api";
import { useCopyToClipboard } from "@/hooks/useCopyToClipboard";
import { timeAgo } from "@/lib/format";
import { toast } from "@/lib/toast";
import type { ServiceTokenSummary } from "@/lib/types";

// The MCP server lives on the API itself (not behind the frontend's /api proxy).
const MCP_URL = process.env.NEXT_PUBLIC_MCP_URL || "http://localhost:8000/mcp";
const TOKEN_PLACEHOLDER = "<your-token>";

type Client = "claude-code" | "claude-desktop" | "web";

export function McpSettings({ canEdit }: { canEdit: boolean }) {
  const copy = useCopyToClipboard();
  const [tokens, setTokens] = useState<ServiceTokenSummary[]>([]);
  const [label, setLabel] = useState("Claude");
  const [fresh, setFresh] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [client, setClient] = useState<Client>("claude-code");

  const refresh = useCallback(() => {
    if (!canEdit) return;
    listServiceTokens()
      .then((all) => setTokens(all.filter((t) => !t.revoked_at)))
      .catch(() => {});
  }, [canEdit]);

  useEffect(refresh, [refresh]);

  const create = async () => {
    setCreating(true);
    try {
      const created = await createServiceToken(label.trim() || "MCP client");
      setFresh(created.token);
      refresh();
    } catch {
      toast.error("Couldn't create a token", "Please try again.");
    } finally {
      setCreating(false);
    }
  };

  const revoke = async (id: string) => {
    try {
      await revokeServiceToken(id);
      setTokens((prev) => prev.filter((t) => t.id !== id));
      toast.success("Token revoked");
    } catch {
      toast.error("Couldn't revoke the token");
    }
  };

  const token = fresh ?? TOKEN_PLACEHOLDER;
  const snippets: Record<Client, { title: string; steps: string[]; code?: string }> = {
    "claude-code": {
      title: "Claude Code",
      steps: ["Run this in a terminal. It works with your Claude subscription or Console login."],
      code: `claude mcp add --transport http knowledge-agent ${MCP_URL} \\\n  --header "Authorization: Bearer ${token}"`,
    },
    "claude-desktop": {
      title: "Claude Desktop",
      steps: [
        "Open Claude Desktop → Settings → Developer → Edit Config.",
        "Add this under \"mcpServers\", save, and restart Claude Desktop. Needs Node.js for npx.",
      ],
      code: JSON.stringify(
        {
          mcpServers: {
            "knowledge-agent": {
              command: "npx",
              args: ["-y", "mcp-remote", MCP_URL, "--header", "Authorization:${AUTH_HEADER}"],
              env: { AUTH_HEADER: `Bearer ${token}` },
            },
          },
        },
        null,
        2,
      ),
    },
    web: {
      title: "Claude.ai or ChatGPT",
      steps: [
        "These run in the cloud, so they can't see localhost. Expose the API over HTTPS first, for example: cloudflared tunnel --url http://localhost:8000 — then use https://<your-tunnel>/mcp as the URL.",
        "Claude (Free, Pro, Max): Customize → Connectors → Add custom connector. Choose No sign-in and add the request header Authorization: Bearer <token> (header auth is in beta and not on every account yet).",
        "ChatGPT (Plus, Pro): Settings → Apps & Connectors → Advanced → Developer mode, then create a connector with the URL and the same bearer token.",
        "Sign-in with OAuth isn't supported by this server yet, so accounts without header auth can't connect from the web apps.",
      ],
    },
  };
  const active = snippets[client];

  return (
    <section aria-labelledby="mcp-heading" className="flex flex-col gap-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="mcp-heading" className="text-[15px] font-semibold text-ink-100">
          Use with Claude or ChatGPT
        </h2>
        <span className="font-mono text-xs text-ink-500">MCP server</span>
      </div>
      <p className="-mt-2 text-[13px] leading-relaxed text-ink-400">
        Bring your own Claude or ChatGPT subscription: add this knowledge base as a connector there, and their model
        answers using your synced Notion and Jira content. It can search and list documents, nothing else, and only sees
        org-wide connections.
      </p>

      <div className="flex flex-col gap-5 rounded-2xl border border-ink-800 bg-ink-900 p-5">
        <div className="flex flex-col gap-1.5">
          <span className="text-[13px] font-medium text-ink-200">Server URL</span>
          <div className="flex items-center gap-2 rounded-xl border border-ink-700 bg-ink-950 py-1.5 pr-1.5 pl-3.5">
            <code className="min-w-0 flex-1 truncate font-mono text-[13px] text-ink-100">{MCP_URL}</code>
            <button
              type="button"
              aria-label="Copy server URL"
              onClick={() => copy(MCP_URL, "URL copied")}
              className="flex h-8 w-8 items-center justify-center rounded-lg text-ink-400 transition hover:bg-ink-800 hover:text-ink-100"
            >
              <Copy01 className="h-4 w-4" aria-hidden="true" />
            </button>
          </div>
        </div>

        {canEdit ? (
          <div className="flex flex-col gap-2.5">
            <span className="text-[13px] font-medium text-ink-200">Access tokens</span>
            {fresh && (
              <div className="flex flex-col gap-2 rounded-xl bg-accent/30 p-3.5">
                <span className="text-[13px] text-ink-100">Copy this token now — it won&apos;t be shown again.</span>
                <div className="flex items-center gap-2 rounded-lg bg-ink-950 py-1.5 pr-1.5 pl-3">
                  <code className="min-w-0 flex-1 truncate font-mono text-[13px] text-ink-100">{fresh}</code>
                  <button
                    type="button"
                    aria-label="Copy token"
                    onClick={() => copy(fresh, "Token copied")}
                    className="flex h-8 w-8 items-center justify-center rounded-lg text-ink-400 transition hover:bg-ink-800 hover:text-ink-100"
                  >
                    <Copy01 className="h-4 w-4" aria-hidden="true" />
                  </button>
                </div>
              </div>
            )}
            {tokens.length > 0 && (
              <ul className="flex flex-col divide-y divide-ink-800 rounded-xl border border-ink-800">
                {tokens.map((t) => (
                  <li key={t.id} className="flex items-center justify-between gap-3 px-3.5 py-2.5 text-sm">
                    <span className="truncate text-ink-100">{t.label}</span>
                    <span className="flex shrink-0 items-center gap-3">
                      <span className="font-mono text-xs text-ink-500">created {timeAgo(t.created_at)}</span>
                      <button
                        type="button"
                        onClick={() => revoke(t.id)}
                        className="text-xs text-error-primary underline-offset-2 hover:underline"
                      >
                        Revoke
                      </button>
                    </span>
                  </li>
                ))}
              </ul>
            )}
            <div className="flex flex-wrap gap-2">
              <label className="sr-only" htmlFor="token-label">
                Token label
              </label>
              <input
                id="token-label"
                value={label}
                onChange={(e) => setLabel(e.target.value)}
                placeholder="Label, e.g. Claude Desktop"
                className="h-10 min-w-0 flex-1 rounded-full border border-ink-700 bg-ink-950 px-4 text-sm text-ink-100 outline-none placeholder:text-ink-500 focus:border-ink-100"
              />
              <button
                type="button"
                onClick={create}
                disabled={creating}
                className="h-10 rounded-full bg-ink-100 px-4 text-sm font-medium text-ink-950 transition hover:bg-ink-200 disabled:opacity-60"
              >
                {creating ? "Creating…" : "Create token"}
              </button>
            </div>
          </div>
        ) : (
          <p className="text-[13px] text-ink-400">Ask an owner or admin for an access token.</p>
        )}

        <div className="flex flex-col gap-3">
          <div role="tablist" aria-label="Client" className="flex flex-wrap gap-1.5">
            {(Object.keys(snippets) as Client[]).map((c) => (
              <button
                key={c}
                type="button"
                role="tab"
                aria-selected={client === c}
                onClick={() => setClient(c)}
                className={`h-9 rounded-full px-3.5 text-[13px] transition ${
                  client === c ? "bg-ink-100 text-ink-950" : "border border-ink-700 text-ink-300 hover:text-ink-100"
                }`}
              >
                {snippets[c].title}
              </button>
            ))}
          </div>
          <ol className="flex list-decimal flex-col gap-1.5 pl-5 text-[13px] leading-relaxed text-ink-300">
            {active.steps.map((step) => (
              <li key={step}>{step}</li>
            ))}
          </ol>
          {active.code && (
            <div className="relative">
              <pre className="overflow-x-auto rounded-xl bg-ink-950 p-4 pr-12 font-mono text-[12.5px] leading-relaxed text-ink-200 ring-1 ring-ink-800">
                {active.code}
              </pre>
              <button
                type="button"
                aria-label="Copy snippet"
                onClick={() => copy(active.code!, "Copied")}
                className="absolute top-2.5 right-2.5 flex h-8 w-8 items-center justify-center rounded-lg text-ink-400 transition hover:bg-ink-800 hover:text-ink-100"
              >
                <Copy01 className="h-4 w-4" aria-hidden="true" />
              </button>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
