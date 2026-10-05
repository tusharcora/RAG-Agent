"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { LogOut01, Settings01, Zap } from "@untitledui/icons";
import { useAuth } from "@/lib/auth-context";
import { useTheme, type Theme } from "@/lib/theme-context";
import { Avatar } from "@/components/base/avatar/avatar";
import { Button } from "@/components/base/buttons/button";
import { Dropdown } from "@/components/base/dropdown/dropdown";

const LINKS = [
  { href: "/", label: "Chat" },
  { href: "/connections", label: "Connections" },
  { href: "/knowledge-base", label: "Knowledge Base" },
  { href: "/activity", label: "Activity" },
];

// A single dropdown item can't offer 3 independent choices without a
// submenu, so it cycles: light -> dark -> cyberpunk -> light. The full
// 3-way picker lives on the Settings page (components/ThemeToggle.tsx).
const THEME_CYCLE: Theme[] = ["light", "dark", "cyberpunk"];
const THEME_LABEL: Record<Theme, string> = { light: "light", dark: "standard", cyberpunk: "cyberpunk" };

function nextTheme(current: Theme): Theme {
  return THEME_CYCLE[(THEME_CYCLE.indexOf(current) + 1) % THEME_CYCLE.length];
}

export default function Nav() {
  const pathname = usePathname();
  const { user, logout } = useAuth();
  const { theme, setTheme } = useTheme();

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
          // coral-300 is tuned for contrast against a dark pill background —
          // on light mode the same translucent bg-coral-500/15 is nearly
          // white, so that light text reads as almost invisible. Found by
          // eye during the light-theme first pass; the other coral-300/400
          // "highlighted text on a translucent coral chip" spots (e.g.
          // SessionSidebar's active-conversation row) have the same issue
          // and are follow-up, not fixed here.
          const activeClass = theme === "light" ? "bg-coral-500/15 text-coral-700" : "bg-coral-500/15 text-coral-300";
          return (
            <Link
              key={link.href}
              href={link.href}
              className={`rounded-full px-3 py-1.5 text-sm transition ${
                active ? activeClass : "text-ink-400 hover:bg-ink-800/60 hover:text-ink-200"
              }`}
            >
              {link.label}
            </Link>
          );
        })}

      <div className="ml-auto flex items-center gap-3 text-sm">
        {user ? (
          <Dropdown.Root>
            <Button color="tertiary" size="sm" className="gap-2 pr-2">
              <span className="flex items-center gap-2">
                <span className="hidden text-ink-400 sm:inline">
                  {user.display_name || user.email} <span className="text-ink-700">·</span>{" "}
                  <span className="capitalize text-gold-400">{user.role}</span>
                </span>
                <Avatar size="xs" initials={(user.display_name || user.email).slice(0, 1).toUpperCase()} />
              </span>
            </Button>

            <Dropdown.Popover className="w-72">
              <Dropdown.Menu>
                <Dropdown.Section>
                  <Dropdown.SectionHeader className="px-2.5 py-2">
                    <p className="truncate text-sm font-semibold text-ink-100">{user.display_name || user.email}</p>
                    <p className="truncate text-xs text-ink-500">{user.email}</p>
                    <p className="mt-1 text-xs text-ink-500">
                      <span className="capitalize text-gold-400">{user.role}</span>{" "}
                      <span className="text-ink-700">·</span> {user.org_name}
                    </p>
                  </Dropdown.SectionHeader>
                </Dropdown.Section>

                <Dropdown.Separator />

                <Dropdown.Item icon={Settings01} href="/settings">
                  Settings
                </Dropdown.Item>
                <Dropdown.Item icon={Zap} onAction={() => setTheme(nextTheme(theme))}>
                  Switch to {THEME_LABEL[nextTheme(theme)]} theme
                </Dropdown.Item>

                <Dropdown.Separator />

                <Dropdown.Item icon={LogOut01} onAction={() => logout()}>
                  Log out
                </Dropdown.Item>
              </Dropdown.Menu>
            </Dropdown.Popover>
          </Dropdown.Root>
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
