import { Inter, JetBrains_Mono, Orbitron, Share_Tech_Mono } from "next/font/google";
import "./globals.css";
import Nav from "@/components/Nav";
import { AuthProvider } from "@/lib/auth-context";
import { ThemeProvider } from "@/lib/theme-context";
import { ToastRegionMount } from "@/components/base/toast/toast-region";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter" });
const orbitron = Orbitron({ subsets: ["latin"], variable: "--font-orbitron" });
const jetbrainsMono = JetBrains_Mono({ subsets: ["latin"], variable: "--font-jetbrains-mono" });
const shareTechMono = Share_Tech_Mono({ subsets: ["latin"], weight: "400", variable: "--font-share-tech-mono" });

export const metadata = {
  title: "RAG Knowledge Agent",
  description: "Notion/Jira-synced retrieval agent with citation tracing",
};

// Applies the persisted theme choice before hydration so there is no flash
// of the wrong theme — none of dark-mode/light-mode/theme-cyberpunk are part
// of the SSR className (that would mismatch this client-only localStorage
// read), so the very first paint has no theme class at all and falls back to
// the dark values baked into styles/brand.css's @theme block until this runs.
const THEME_INIT_SCRIPT = `
try {
  var t = window.localStorage.getItem("theme");
  var root = document.documentElement;
  if (t === "light") {
    root.classList.add("light-mode");
  } else {
    root.classList.add("dark-mode");
    if (t === "cyberpunk") root.classList.add("theme-cyberpunk");
  }
} catch (e) {
  document.documentElement.classList.add("dark-mode");
}
`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html
      lang="en"
      className={`${inter.variable} ${orbitron.variable} ${jetbrainsMono.variable} ${shareTechMono.variable}`}
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
            <ToastRegionMount />
          </AuthProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
