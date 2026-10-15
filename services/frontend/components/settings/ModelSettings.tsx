"use client";

import { useEffect, useState } from "react";
import { ApiError, deleteLlmKey, getLlmSettings, saveLlmSettings, testLlmSettings } from "@/lib/api";
import { toast } from "@/lib/toast";
import type { LlmProvider, LlmSettings, LlmSettingsInput, LlmTestResult } from "@/lib/types";

const PROVIDERS: { id: LlmProvider; name: string; blurb: string }[] = [
  { id: "gemini", name: "Built in", blurb: "Gemini, on the server's key" },
  { id: "anthropic", name: "Claude", blurb: "Your Anthropic API key" },
  { id: "openai", name: "OpenAI", blurb: "Your OpenAI API key" },
  { id: "local", name: "Local model", blurb: "Ollama, LM Studio, vLLM…" },
];

// The API runs in Docker, so "localhost" there is the container itself;
// host.docker.internal is the Mac running Ollama or LM Studio.
const LOCAL_PRESETS = [
  { label: "Ollama", url: "http://host.docker.internal:11434/v1" },
  { label: "LM Studio", url: "http://host.docker.internal:1234/v1" },
];

const inputClass =
  "h-11 w-full rounded-xl border border-ink-700 bg-ink-950 px-3.5 text-sm text-ink-100 outline-none transition placeholder:text-ink-500 focus:border-ink-100 disabled:opacity-60";

export function ModelSettings({ canEdit }: { canEdit: boolean }) {
  const [saved, setSaved] = useState<LlmSettings | null>(null);
  const [provider, setProvider] = useState<LlmProvider>("gemini");
  const [model, setModel] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [test, setTest] = useState<LlmTestResult | null>(null);
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);

  // Fills the form for one provider from what's saved for it.
  const fill = (s: LlmSettings | null, p: LlmProvider) => {
    const cfg = p === "gemini" ? null : s?.providers[p];
    setModel(cfg?.model ?? "");
    setBaseUrl(cfg?.base_url ?? (p === "local" ? LOCAL_PRESETS[0].url : ""));
    setApiKey("");
  };

  const load = (s: LlmSettings, p: LlmProvider = s.provider) => {
    setSaved(s);
    setProvider(p);
    fill(s, p);
  };

  useEffect(() => {
    getLlmSettings()
      .then(load)
      .catch(() => {});
  }, []);

  const keyNeeded = provider === "anthropic" || provider === "openai";
  const providerCfg = provider === "gemini" ? null : saved?.providers[provider];
  const savedKeyHint = providerCfg?.has_api_key ? providerCfg.api_key_hint ?? "saved" : null;
  const [removing, setRemoving] = useState(false);

  const removeKey = async () => {
    if (provider === "gemini") return;
    setRemoving(true);
    try {
      const next = await deleteLlmKey(provider);
      setSaved(next);
      setTest(null);
      toast.success("Saved settings removed");
      fill(next, provider);
    } catch {
      toast.error("Couldn't remove the key");
    } finally {
      setRemoving(false);
    }
  };

  const body = (): LlmSettingsInput => ({
    provider,
    model: provider === "gemini" ? null : model.trim() || null,
    base_url: provider === "local" ? baseUrl.trim() || null : null,
    api_key: apiKey.trim() ? apiKey.trim() : undefined,
  });

  const pick = (p: LlmProvider) => {
    setProvider(p);
    setTest(null);
    fill(saved, p);
  };

  const runTest = async () => {
    setTesting(true);
    setTest(null);
    try {
      const result = await testLlmSettings(body());
      setTest(result);
      if (result.ok && !model && result.models.length > 0) setModel(result.models[0]);
    } catch (err) {
      setTest({ ok: false, models: [], sample: null, error: err instanceof ApiError ? err.detail ?? err.message : "Request failed" });
    } finally {
      setTesting(false);
    }
  };

  const save = async () => {
    setSaving(true);
    try {
      load(await saveLlmSettings(body()), provider);
      setTest(null);
      toast.success("Answer model saved");
    } catch (err) {
      toast.error("Couldn't save", err instanceof ApiError ? err.detail ?? err.message : "Please try again.");
    } finally {
      setSaving(false);
    }
  };

  const current = saved
    ? saved.provider === "gemini"
      ? `Gemini · ${saved.default_model}`
      : `${PROVIDERS.find((p) => p.id === saved.provider)?.name} · ${saved.providers[saved.provider]?.model ?? "?"}`
    : "…";

  return (
    <section aria-labelledby="model-heading" className="flex flex-col gap-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="model-heading" className="text-[15px] font-semibold text-ink-100">
          Answer model
        </h2>
        <span className="font-mono text-xs text-ink-500">in use: {current}</span>
      </div>
      <p className="-mt-2 text-[13px] text-ink-400">
        The model that writes answers in this app&apos;s chat. Keys are stored encrypted and never shown again.
        {!canEdit && " Only owners and admins can change it."}
      </p>

      <div role="radiogroup" aria-label="Answer model provider" className="grid grid-cols-2 gap-3 md:grid-cols-4">
        {PROVIDERS.map((p) => {
          const on = provider === p.id;
          return (
            <button
              key={p.id}
              type="button"
              role="radio"
              aria-checked={on}
              aria-label={`${p.name} — ${p.blurb}`}
              disabled={!canEdit}
              onClick={() => pick(p.id)}
              className={`flex flex-col gap-1 rounded-2xl border bg-ink-900 p-4 text-left transition disabled:cursor-default ${
                on ? "border-ink-100 ring-1 ring-ink-100" : "border-ink-800 hover:border-ink-700"
              }`}
            >
              <span className="flex items-center justify-between">
                <span className="text-sm font-medium text-ink-100">{p.name}</span>
                <span
                  className={`h-4 w-4 rounded-full border ${on ? "border-ink-100 bg-ink-100" : "border-ink-700"}`}
                  aria-hidden="true"
                />
              </span>
              <span className="text-xs text-ink-400">{p.blurb}</span>
              {p.id !== "gemini" && saved?.providers[p.id]?.has_api_key && (
                <span className="mt-1 font-mono text-[10.5px] tracking-[0.04em] text-success-primary">KEY SAVED</span>
              )}
            </button>
          );
        })}
      </div>

      {provider !== "gemini" && (
        <div className="flex flex-col gap-4 rounded-2xl border border-ink-800 bg-ink-900 p-5">
          {provider === "local" && (
            <label className="flex flex-col gap-1.5">
              <span className="text-[13px] font-medium text-ink-200">Server URL (OpenAI-compatible)</span>
              <input
                className={inputClass}
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
                placeholder="http://host.docker.internal:11434/v1"
                disabled={!canEdit}
                spellCheck={false}
              />
              <span className="flex flex-wrap items-center gap-2 text-xs text-ink-500">
                Presets:
                {LOCAL_PRESETS.map((preset) => (
                  <button
                    key={preset.label}
                    type="button"
                    disabled={!canEdit}
                    onClick={() => setBaseUrl(preset.url)}
                    className="rounded-full border border-ink-700 px-2.5 py-1 text-ink-300 transition hover:border-ink-100 hover:text-ink-100"
                  >
                    {preset.label}
                  </button>
                ))}
                <span>— the model server must be running on this computer.</span>
              </span>
            </label>
          )}

          <label className="flex flex-col gap-1.5">
            <span className="text-[13px] font-medium text-ink-200">
              API key{provider === "local" && <span className="font-normal text-ink-500"> (optional)</span>}
            </span>
            <input
              className={inputClass}
              type="password"
              autoComplete="off"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder={
                savedKeyHint
                  ? `Saved (${savedKeyHint}) — leave blank to keep it`
                  : provider === "anthropic"
                    ? "sk-ant-…"
                    : provider === "openai"
                      ? "sk-…"
                      : "Only if your server requires one"
              }
              disabled={!canEdit}
            />
            {savedKeyHint && canEdit && (
              <span className="flex items-center gap-2 text-xs text-ink-400">
                <span className="h-1.5 w-1.5 rounded-full bg-success-solid" aria-hidden="true" />
                Key {savedKeyHint} is saved, and kept when you switch to another model.
                <button
                  type="button"
                  onClick={removeKey}
                  disabled={removing}
                  className="text-error-primary underline-offset-2 hover:underline disabled:opacity-60"
                >
                  {removing ? "Removing…" : "Remove"}
                </button>
              </span>
            )}
            {keyNeeded && (
              <span className="text-xs text-ink-500">
                {provider === "anthropic"
                  ? "Create one in the Claude Console. A Claude Pro or Max subscription can't be used here — that's what the connector below is for."
                  : "Create one at platform.openai.com. A ChatGPT Plus subscription doesn't include API access — use the connector below instead."}
              </span>
            )}
          </label>

          <label className="flex flex-col gap-1.5">
            <span className="text-[13px] font-medium text-ink-200">Model</span>
            {test?.models && test.models.length > 0 ? (
              <select
                className={inputClass}
                value={model}
                onChange={(e) => setModel(e.target.value)}
                disabled={!canEdit}
              >
                {!test.models.includes(model) && model && <option value={model}>{model}</option>}
                {test.models.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
            ) : (
              <input
                className={inputClass}
                value={model}
                onChange={(e) => setModel(e.target.value)}
                placeholder="Test the connection to pick from the available models"
                disabled={!canEdit}
                spellCheck={false}
              />
            )}
          </label>

          {test && (
            <div
              role="status"
              className={`rounded-xl px-4 py-3 text-sm ${
                test.ok ? "bg-ok-bg text-success-primary" : "border border-error_subtle bg-error-primary text-error-primary"
              }`}
            >
              {test.ok
                ? `Connected · ${test.models.length} model${test.models.length === 1 ? "" : "s"} available${
                    test.sample ? ` · test reply: “${test.sample}”` : ""
                  }`
                : test.error}
            </div>
          )}
        </div>
      )}

      {canEdit && (
        <div className="flex flex-wrap gap-2.5">
          {provider !== "gemini" && (
            <button
              type="button"
              onClick={runTest}
              disabled={testing}
              className="h-10 rounded-full border border-ink-700 px-4 text-sm text-ink-100 transition hover:border-ink-100 disabled:opacity-60"
            >
              {testing ? "Testing…" : "Test connection"}
            </button>
          )}
          <button
            type="button"
            onClick={save}
            disabled={saving}
            className="h-10 rounded-full bg-ink-100 px-4 text-sm font-medium text-ink-950 transition hover:bg-ink-200 disabled:opacity-60"
          >
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
      )}
    </section>
  );
}
