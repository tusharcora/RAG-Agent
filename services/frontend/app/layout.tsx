import { Inter } from "next/font/google";
import "./globals.css";
import Nav from "@/components/Nav";
import { AuthProvider } from "@/lib/auth-context";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter" });

export const metadata = {
  title: "RAG Knowledge Agent",
  description: "Notion/Jira-synced retrieval agent with citation tracing",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    // dark-mode is a real class Untitled UI's theme.css keys its dark-palette
    // overrides off (see styles/theme.css's ".dark-mode" block) — this app has
    // no light-mode toggle, so it's forced on unconditionally here rather than
    // wired to a theme switcher that doesn't exist.
    <html lang="en" className={`${inter.variable} dark-mode`}>
      <body className="font-sans">
        <AuthProvider>
          <Nav />
          <main className="min-h-[calc(100vh-57px)]">{children}</main>
        </AuthProvider>
      </body>
    </html>
  );
}
