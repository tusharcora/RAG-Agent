"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useAuth } from "@/lib/auth-context";

const LINKS = [
  { href: "/", label: "Chat" },
  { href: "/connections", label: "Connections" },
  { href: "/knowledge-base", label: "Knowledge Base" },
  { href: "/activity", label: "Activity" },
];

export default function Nav() {
  const pathname = usePathname();
  const { user, logout } = useAuth();

  return (
    <nav className="flex items-center gap-1 border-b border-ink-800 bg-ink-950/90 px-4 py-3 backdrop-blur">
      <Link href="/" className="mr-6 flex items-center gap-2">
        <span className="flex h-6 w-6 items-center justify-center rounded-md bg-gradient-to-br from-coral-500 to-gold-400 text-[11px] font-bold text-ink-950">
          R
        </span>
        <span className="text-sm font-semibold tracking-wide text-ink-100">RAG Knowledge Agent</span>
      </Link>

      {user &&
        LINKS.map((link) => {
          const active = pathname === link.href;
          return (
            <Link
              key={link.href}
              href={link.href}
              className={`rounded-full px-3 py-1.5 text-sm transition ${
                active ? "bg-coral-500/15 text-coral-300" : "text-ink-400 hover:bg-ink-800/60 hover:text-ink-200"
              }`}
            >
              {link.label}
            </Link>
          );
        })}

      <div className="ml-auto flex items-center gap-3 text-sm">
        {user ? (
          <>
            <span className="hidden text-ink-400 sm:inline">
              {user.display_name || user.email} <span className="text-ink-700">·</span>{" "}
              <span className="capitalize text-gold-400">{user.role}</span>
            </span>
            <span className="flex h-7 w-7 items-center justify-center rounded-full bg-ink-800 text-xs font-semibold text-ink-200 ring-1 ring-inset ring-ink-700">
              {(user.display_name || user.email).slice(0, 1).toUpperCase()}
            </span>
            <button
              onClick={() => logout()}
              className="rounded-full px-3 py-1.5 text-ink-400 transition hover:bg-ink-800/60 hover:text-ink-200"
            >
              Log out
            </button>
          </>
        ) : (
          <>
            <Link href="/login" className="rounded-full px-3 py-1.5 text-ink-400 transition hover:bg-ink-800/60 hover:text-ink-200">
              Log in
            </Link>
            <Link
              href="/signup"
              className="rounded-full bg-coral-500 px-3.5 py-1.5 font-medium text-white transition hover:bg-coral-400"
            >
              Sign up
            </Link>
          </>
        )}
      </div>
    </nav>
  );
}
