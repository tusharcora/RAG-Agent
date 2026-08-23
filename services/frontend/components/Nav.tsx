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
    <nav className="flex items-center gap-1 border-b border-slate-800 bg-slate-900/60 px-4 py-3">
      <span className="mr-6 text-sm font-semibold tracking-wide text-slate-200">RAG Knowledge Agent</span>
      {user &&
        LINKS.map((link) => {
          const active = pathname === link.href;
          return (
            <Link
              key={link.href}
              href={link.href}
              className={`rounded-md px-3 py-1.5 text-sm transition ${
                active ? "bg-slate-800 text-white" : "text-slate-400 hover:bg-slate-800/60 hover:text-slate-200"
              }`}
            >
              {link.label}
            </Link>
          );
        })}
      {user && (
        <div className="ml-auto flex items-center gap-3 text-sm text-slate-400">
          <span>
            {user.display_name || user.email} <span className="text-slate-600">·</span>{" "}
            <span className="capitalize">{user.role}</span>
          </span>
          <button
            onClick={() => logout()}
            className="rounded-md px-2 py-1 text-slate-400 transition hover:bg-slate-800/60 hover:text-slate-200"
          >
            Log out
          </button>
        </div>
      )}
    </nav>
  );
}
