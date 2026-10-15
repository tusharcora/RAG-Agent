declare module "cloudflare:test" {
  interface ProvidedEnv {
    PENDING: KVNamespace;
    BROKER_URL: string;
    RESULT_KEY: string;
  }
}
