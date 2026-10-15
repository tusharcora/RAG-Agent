"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import {
  Activity,
  AlertTriangle,
  BookOpen01,
  Link01,
  LogOut01,
  MessageChatCircle,
  Plus,
  Settings01,
  Trash01,
} from "@untitledui/icons";
import { IS_DESKTOP, useAuth } from "@/lib/auth-context";
import { deleteSession, getSessions } from "@/lib/api";
import { SESSIONS_CHANGED_EVENT } from "@/lib/format";
import { useInterval } from "@/hooks/useInterval";
import type { SessionSummary } from "@/lib/types";
import { Button } from "@/components/base/buttons/button";
import { ConfirmDialog } from "@/components/base/modal/confirm-dialog";
import { toast } from "@/lib/toast";

const LINKS = [
  { href: "/", label: "Chat", icon: MessageChatCircle },
  { href: "/connections", label: "Connections", icon: Link01 },
  { href: "/knowledge-base", label: "Knowledge Base", icon: BookOpen01 },
  { href: "/conflicts", label: "Conflicts", icon: AlertTriangle },
  { href: "/activity", label: "Activity", icon: Activity },
  { href: "/settings", label: "Settings", icon: Settings01 },
];

export default function Sidebar() {
  const pathname = usePathname();
  const { user, logout, account } = useAuth();
  const signedName = IS_DESKTOP && account?.signed_in ? account.name || account.email : null;

  // First-run setup is full-screen.
  if (pathname.startsWith("/setup")) return null;

  return (
    <aside className="flex shrink-0 flex-col gap-3 border-b border-ink-800 bg-ink-950 px-3 py-3 md:sticky md:top-0 md:h-screen md:w-[248px] md:gap-6 md:border-r md:border-b-0 md:px-[18px] md:pt-7 md:pb-5">
      <Link href="/" className="px-2.5">
        <span className="font-serif text-[30px] leading-none tracking-tight text-ink-100">Knowledge Agent</span>
      </Link>

      {user ? (
        <>
          <Link
            href="/"
            className="hidden h-11 items-center justify-center gap-2 rounded-full bg-ink-100 text-sm font-medium text-ink-950 transition hover:bg-ink-200 md:flex"
          >
            <Plus className="h-4 w-4" aria-hidden="true" />
            New chat
          </Link>

          <nav aria-label="Primary" className="flex gap-0.5 overflow-x-auto md:flex-col md:overflow-visible">
            {LINKS.map((link) => {
              const active = link.href === "/" ? pathname === "/" : pathname.startsWith(link.href);
              const Icon = link.icon;
              return (
                <Link
                  key={link.href}
                  href={link.href}
                  aria-current={active ? "page" : undefined}
                  className={`flex h-10 shrink-0 items-center gap-3 rounded-[10px] px-3 text-sm transition ${
                    active ? "bg-ink-800 font-medium text-ink-100" : "text-ink-400 hover:bg-ink-800/60 hover:text-ink-100"
                  }`}
                >
                  <Icon className="h-[18px] w-[18px]" aria-hidden="true" />
                  {link.label}
                </Link>
              );
            })}
          </nav>

          <Suspense fallback={<div className="hidden flex-1 md:block" />}>
            <RecentList />
          </Suspense>

          <div className="hidden items-center gap-2.5 rounded-xl border border-ink-800 bg-ink-900 px-3 py-2.5 md:flex">
            <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-ink-100 text-xs font-semibold text-ink-950">
              {signedName ? signedName.slice(0, 1).toUpperCase() : (user.org_name || user.email).slice(0, 2).toUpperCase()}
            </span>
            <div className="min-w-0 flex-1 leading-tight">
              <p className="truncate text-[13px] font-medium text-ink-100">{signedName ?? user.org_name}</p>
              <p className="truncate text-xs text-ink-500 first-letter:uppercase">{signedName ? user.org_name : user.role}</p>
            </div>
            {!IS_DESKTOP && (
              <button
                type="button"
                aria-label="Log out"
                onClick={() => logout()}
                className="rounded-md p-1.5 text-ink-500 transition hover:bg-ink-800 hover:text-ink-100"
              >
                <LogOut01 className="h-4 w-4" aria-hidden="true" />
              </button>
            )}
          </div>
        </>
      ) : (
        <div className="flex gap-2 md:mt-auto md:flex-col">
          <Button color="primary" size="sm" href="/signup">
            Sign up
          </Button>
          <Button color="tertiary" size="sm" href="/login">
            Log in
          </Button>
        </div>
      )}
    </aside>
  );
}

function RecentList() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const activeId = pathname === "/" ? params.get("session") : null;
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [pendingDelete, setPendingDelete] = useState<SessionSummary | null>(null);

  const refresh = useCallback(() => {
    getSessions()
      .then(setSessions)
      .catch(() => {});
  }, []);

  useEffect(() => {
    refresh();
    window.addEventListener(SESSIONS_CHANGED_EVENT, refresh);
    return () => window.removeEventListener(SESSIONS_CHANGED_EVENT, refresh);
  }, [refresh]);
  useInterval(refresh, 15000);

  const confirmDelete = () => {
    if (!pendingDelete) return;
    const id = pendingDelete.session_id;
    const previous = sessions;
    setSessions((prev) => prev.filter((s) => s.session_id !== id));
    setPendingDelete(null);
    deleteSession(id)
      .then(() => {
        if (id === activeId) router.push("/");
      })
      .catch(() => {
        setSessions(previous);
        toast.error("Couldn't delete conversation", "Please try again.");
      });
  };

  return (
    <div className="hidden min-h-0 flex-1 flex-col gap-1 md:flex">
      <p className="px-3 pb-2 font-mono text-[11px] tracking-[0.08em] text-ink-500">RECENT</p>
      <ul className="flex min-h-0 flex-col gap-0.5 overflow-y-auto">
        {sessions.length === 0 && <li className="px-3 py-1 text-[13px] text-ink-500">No conversations yet.</li>}
        {sessions.slice(0, 12).map((s) => {
          const active = s.session_id === activeId;
          return (
            <li key={s.session_id} className="group relative">
              <Link
                href={`/?session=${s.session_id}`}
                title={s.preview ?? undefined}
                className={`block truncate rounded-[10px] py-2 pr-8 pl-3 text-[13.5px] transition ${
                  active
                    ? "bg-ink-900 text-ink-100 ring-1 ring-ink-800"
                    : "text-ink-400 hover:bg-ink-800/60 hover:text-ink-100"
                }`}
              >
                {s.preview || "Untitled conversation"}
              </Link>
              <button
                type="button"
                aria-label="Delete conversation"
                onClick={() => setPendingDelete(s)}
                className="absolute top-1/2 right-1.5 -translate-y-1/2 rounded-md p-1 text-ink-500 opacity-0 transition group-hover:opacity-100 hover:text-error-primary focus-visible:opacity-100"
              >
                <Trash01 className="h-3.5 w-3.5" aria-hidden="true" />
              </button>
            </li>
          );
        })}
      </ul>

      <ConfirmDialog
        isOpen={pendingDelete !== null}
        onOpenChange={(open) => !open && setPendingDelete(null)}
        title="Delete this conversation?"
        description={`${pendingDelete?.preview || "Untitled conversation"} — this can't be undone.`}
        confirmLabel="Delete"
        isDestructive
        onConfirm={confirmDelete}
      />
    </div>
  );
}
