"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { Avatar } from "@/components/base/avatar/avatar";
import { Button } from "@/components/base/buttons/button";
import { ThemeToggle } from "@/components/ThemeToggle";

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
    <nav className="cyber-glow-sm flex items-center gap-1 border-b border-ink-800 bg-ink-950/90 px-4 py-3 backdrop-blur">
      <Link href="/" className="mr-6 flex items-center gap-2">
        <span className="cyber-chamfer-sm flex h-6 w-6 items-center justify-center rounded-md bg-gradient-to-br from-coral-500 to-gold-400 text-[11px] font-bold text-ink-950">
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
        <ThemeToggle />
        {user ? (
          <>
            <span className="hidden text-ink-400 sm:inline">
              {user.display_name || user.email} <span className="text-ink-700">·</span>{" "}
              <span className="capitalize text-gold-400">{user.role}</span>
            </span>
            <Avatar size="xs" initials={(user.display_name || user.email).slice(0, 1).toUpperCase()} />
            <Button color="tertiary" size="sm" onPress={() => logout()}>
              Log out
            </Button>
          </>
        ) : (
          <>
            <Button color="tertiary" size="sm" href="/login">
              Log in
            </Button>
            <Button color="primary" size="sm" href="/signup">
              Sign up
            </Button>
          </>
        )}
      </div>
    </nav>
  );
}
