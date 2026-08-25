"use client";

import { useEffect } from "react";

// Next.js only renders this in place of the root layout when the layout
// itself throws, so it must supply its own <html>/<body> and can't lean on
// app/globals.css's design tokens being reliably available — kept
// intentionally plain rather than importing the rest of the app's styling.
export default function GlobalError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <html lang="en">
      <body
        style={{
          display: "flex",
          minHeight: "100vh",
          alignItems: "center",
          justifyContent: "center",
          background: "#0a0a0d",
          color: "#e6e6ea",
          fontFamily: "system-ui, sans-serif",
        }}
      >
        <div style={{ textAlign: "center" }}>
          <p style={{ fontSize: 16, fontWeight: 600 }}>The app hit an unexpected error</p>
          <p style={{ marginTop: 8, fontSize: 14, color: "#a9a9b3" }}>Reloading usually fixes this.</p>
          <button
            type="button"
            onClick={reset}
            style={{
              marginTop: 16,
              padding: "8px 16px",
              borderRadius: 8,
              background: "#e8464a",
              color: "white",
              fontSize: 14,
              fontWeight: 600,
              border: "none",
              cursor: "pointer",
            }}
          >
            Try again
          </button>
        </div>
      </body>
    </html>
  );
}
