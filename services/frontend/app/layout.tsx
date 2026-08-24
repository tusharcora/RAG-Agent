import { Inter, JetBrains_Mono, Orbitron, Share_Tech_Mono } from "next/font/google";
import "./globals.css";
import Nav from "@/components/Nav";
import { AuthProvider } from "@/lib/auth-context";
import { ThemeProvider } from "@/lib/theme-context";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter" });
const orbitron = Orbitron({ subsets: ["latin"], variable: "--font-orbitron" });
const jetbrainsMono = JetBrains_Mono({ subsets: ["latin"], variable: "--font-jetbrains-mono" });
const shareTechMono = Share_Tech_Mono({ subsets: ["latin"], weight: "400", variable: "--font-share-tech-mono" });

export const metadata = {
  title: "RAG Knowledge Agent",
  description: "Notion/Jira-synced retrieval agent with citation tracing",
};

// Applies the persisted theme choice before hydration so there is no
// flash of the wrong theme. theme-cyberpunk is never part of the SSR
// className (that would mismatch this client-only localStorage read).
const THEME_INIT_SCRIPT = `
try {
  if (window.localStorage.getItem("theme") === "cyberpunk") {
    document.documentElement.classList.add("theme-cyberpunk");
  }
} catch (e) {}
`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    // dark-mode is a real class Untitled UI's theme.css keys its dark-palette
    // overrides off (see styles/theme.css's ".dark-mode" block) — this app has
    // no light-mode toggle, so it's forced on unconditionally here rather than
    // wired to a theme switcher that doesn't exist. theme-cyberpunk layers on
    // top of it (see styles/cyberpunk.css) and is toggled client-side only,
    // by ThemeProvider and the inline script below.
    <html
      lang="en"
      className={`${inter.variable} ${orbitron.variable} ${jetbrainsMono.variable} ${shareTechMono.variable} dark-mode`}
      suppressHydrationWarning
    >
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
      </head>
      <body className="font-sans">
        <ThemeProvider>
          <AuthProvider>
            <Nav />
            <main className="min-h-[calc(100vh-57px)]">{children}</main>
          </AuthProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
