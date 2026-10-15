import { Geist, Geist_Mono, Instrument_Serif } from "next/font/google";
import "./globals.css";
import Sidebar from "@/components/Sidebar";
import { AuthProvider } from "@/lib/auth-context";
import { ThemeProvider } from "@/lib/theme-context";
import { ToastRegionMount } from "@/components/base/toast/toast-region";

const geist = Geist({ subsets: ["latin"], variable: "--font-geist" });
const geistMono = Geist_Mono({ subsets: ["latin"], variable: "--font-geist-mono" });
const instrument = Instrument_Serif({ subsets: ["latin"], weight: "400", style: ["normal", "italic"], variable: "--font-instrument" });

export const metadata = {
  title: "RAG Knowledge Agent",
  description: "Notion/Jira-synced retrieval agent with citation tracing",
};

// Applies the persisted theme before hydration so there is no flash of the
// wrong theme. System is the default and follows prefers-color-scheme.
const THEME_INIT_SCRIPT = `
try {
  var m = window.localStorage.getItem("atelier-theme") || "system";
  var a = window.localStorage.getItem("atelier-accent") || "lime";
  var dark = m === "dark" || (m === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  var r = document.documentElement;
  if (dark) r.classList.add("dark-mode");
  r.setAttribute("data-accent", a);
} catch (e) {}
`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html
      lang="en"
      className={`${geist.variable} ${geistMono.variable} ${instrument.variable}`}
      suppressHydrationWarning
    >
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
      </head>
      <body className="font-sans">
        <ThemeProvider>
          <AuthProvider>
            <div className="flex min-h-screen flex-col md:flex-row">
              <Sidebar />
              <main className="min-w-0 flex-1">{children}</main>
            </div>
            <ToastRegionMount />
          </AuthProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
