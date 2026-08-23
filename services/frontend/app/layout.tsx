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
    <html lang="en" className={inter.variable}>
      <body className="font-sans">
        <AuthProvider>
          <Nav />
          <main className="min-h-[calc(100vh-57px)]">{children}</main>
        </AuthProvider>
      </body>
    </html>
  );
}
