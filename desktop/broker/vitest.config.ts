import { defineWorkersConfig } from "@cloudflare/vitest-pool-workers/config";

export default defineWorkersConfig({
  test: {
    poolOptions: {
      workers: {
        wrangler: { configPath: "./wrangler.toml" },
        miniflare: {
          // Required by the test pool itself, not by the Worker.
          compatibilityFlags: ["nodejs_compat"],
          // Fake values only — tests never reach a real provider (fetchMock intercepts every outbound call).
          bindings: {
            BROKER_URL: "https://broker.test",
            NOTION_CLIENT_ID: "notion-id",
            NOTION_CLIENT_SECRET: "notion-secret",
            JIRA_CLIENT_ID: "jira-id",
            JIRA_CLIENT_SECRET: "jira-secret",
            GOOGLE_CLIENT_ID: "google-id",
            GOOGLE_CLIENT_SECRET: "google-secret",
            GITHUB_CLIENT_ID: "github-id",
            GITHUB_CLIENT_SECRET: "github-secret",
            // base64("test-key-test-key-test-key-00000"), 32 bytes
            RESULT_KEY: "dGVzdC1rZXktdGVzdC1rZXktdGVzdC1rZXktMDAwMDA=",
          },
        },
      },
    },
  },
});
