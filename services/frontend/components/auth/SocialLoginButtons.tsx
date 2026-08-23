import { API_BASE } from "@/lib/api";

/**
 * "Continue with Google/GitHub" — real <a href> browser navigations (not
 * fetch), same reasoning as the Notion/Jira "Connect" links elsewhere in
 * this app: the actual redirect chain through the provider's consent screen
 * needs a real navigation, a fetch can't do it. This is app LOGIN, distinct
 * from the Notion/Jira OAuth that connects data sources to an org.
 *
 * Self-contained on purpose — drop this into any auth page without touching
 * its surrounding form markup.
 */
export function SocialLoginButtons({ inviteToken }: { inviteToken?: string }) {
  const qs = inviteToken ? `?invite_token=${encodeURIComponent(inviteToken)}` : "";

  return (
    <div className="space-y-2">
      <a
        href={`${API_BASE}/auth/google/authorize${qs}`}
        className="flex w-full items-center justify-center gap-2 rounded-md border border-slate-700 bg-slate-900 px-3 py-2 text-sm font-medium text-slate-100 transition hover:bg-slate-800"
      >
        Continue with Google
      </a>
      <a
        href={`${API_BASE}/auth/github/authorize${qs}`}
        className="flex w-full items-center justify-center gap-2 rounded-md border border-slate-700 bg-slate-900 px-3 py-2 text-sm font-medium text-slate-100 transition hover:bg-slate-800"
      >
        Continue with GitHub
      </a>
    </div>
  );
}
