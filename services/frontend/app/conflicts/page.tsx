"use client";

import Link from "next/link";
import { useAuth } from "@/lib/auth-context";
import { PageHeader } from "@/components/PageHeader";

/**
 * Conflicts inbox. The design calls for a nightly scan that flags places where
 * two sources disagree (dates, owners, numbers). That backend job does not
 * exist yet, so this page shows the shell and says so plainly instead of
 * inventing conflicts.
 */
export default function ConflictsPage() {
  const { user } = useAuth();
  if (!user) return null;

  return (
    <div className="mx-auto flex max-w-[1240px] flex-col gap-8 px-6 py-10 md:px-14">
      <PageHeader
        eyebrow="CONFLICT DETECTION"
        title={
          <>
            Where your docs <span className="hl italic">disagree</span>
          </>
        }
        trailing={<span className="font-mono text-xs text-ink-500">0 open</span>}
      />

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-[380px_minmax(0,1fr)]">
        <section className="flex flex-col gap-2 rounded-[20px] border border-dashed border-ink-700 p-5">
          <span className="font-mono text-[11px] tracking-[0.08em] text-ink-500">INBOX</span>
          <p className="text-sm text-ink-400">No conflicts have been found.</p>
        </section>

        <section className="flex flex-col gap-5 rounded-[20px] border border-ink-800 bg-ink-900 p-7">
          <h2 className="font-serif text-[30px] leading-tight text-ink-100">Conflict scanning isn&apos;t switched on yet</h2>
          <p className="max-w-2xl text-[15px] leading-relaxed text-ink-300">
            When it is, a scan compares what your Notion pages and Jira issues say about the same thing — a date, an
            owner, a number — and lists each disagreement here side by side, with both sources quoted. You&apos;ll be
            able to mark one as resolved, dismiss it, or ask about it in chat.
          </p>
          <div className="flex flex-wrap gap-2.5">
            <Link
              href="/"
              className="flex h-10 items-center rounded-full bg-ink-100 px-4 text-sm font-medium text-ink-950 transition hover:bg-ink-200"
            >
              Ask a question instead
            </Link>
            <Link
              href="/connections"
              className="flex h-10 items-center rounded-full border border-ink-700 px-4 text-sm text-ink-100 transition hover:border-ink-100"
            >
              Check your connections
            </Link>
          </div>
        </section>
      </div>
    </div>
  );
}
