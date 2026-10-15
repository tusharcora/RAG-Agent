"use client";

import { useState } from "react";
import { completeKeys, SetupFieldError, type KeysProvider } from "@/lib/setup";
import { Input } from "@/components/base/input/input";
import { BackButton, PILL_PRIMARY, SetupShell } from "./SetupShell";

const PROVIDERS: { id: KeysProvider; name: string; keyLabel: string; defaultModel: string | null }[] = [
  { id: "anthropic", name: "Claude", keyLabel: "Anthropic API key", defaultModel: "claude-sonnet-5-5" },
  { id: "openai", name: "OpenAI", keyLabel: "OpenAI API key", defaultModel: "gpt-5-mini" },
  // The backend always answers with its built-in Gemini model, so no model field.
  { id: "gemini", name: "Gemini", keyLabel: "Gemini API key", defaultModel: null },
];

type Errors = Partial<Record<"voyage_api_key" | "api_key" | "model" | "form", string>>;

export function KeysStep({ onBack, onDone }: { onBack: () => void; onDone: () => void }) {
  const [voyageKey, setVoyageKey] = useState("");
  const [provider, setProvider] = useState<KeysProvider>("anthropic");
  const [apiKey, setApiKey] = useState("");
  const [models, setModels] = useState<Record<string, string>>({ anthropic: "claude-sonnet-5-5", openai: "gpt-5-mini" });
  const [errors, setErrors] = useState<Errors>({});
  const [busy, setBusy] = useState(false);

  const current = PROVIDERS.find((p) => p.id === provider)!;
  const model = models[provider] ?? "";
  const canSubmit = voyageKey.trim() !== "" && apiKey.trim() !== "" && (current.defaultModel === null || model.trim() !== "");

  const submit = async () => {
    setBusy(true);
    setErrors({});
    try {
      await completeKeys({
        voyage_api_key: voyageKey.trim(),
        provider,
        api_key: apiKey.trim(),
        ...(current.defaultModel !== null ? { model: model.trim() } : {}),
      });
      onDone();
    } catch (e) {
      if (e instanceof SetupFieldError && (e.field === "voyage_api_key" || e.field === "api_key" || e.field === "model")) {
        setErrors({ [e.field]: e.fieldMessage });
      } else {
        setErrors({ form: (e as { detail?: string }).detail || (e instanceof Error ? e.message : "Couldn't check your keys.") });
      }
      setBusy(false);
    }
  };

  return (
    <SetupShell back={<BackButton onClick={onBack} />} eyebrow="SET UP · STEP 2 OF 2" title="Add your keys">
      <div className="grid gap-5 md:grid-cols-2">
        <section className="flex flex-col gap-3.5 rounded-[22px] border border-ink-800 bg-ink-900 p-6">
          <h2 className="text-[17px] font-semibold text-ink-100">Search · Voyage</h2>
          <p className="text-sm leading-normal text-ink-400">
            Turns your documents into searchable vectors. The free tier covers most personal workspaces.
          </p>
          <Input
            label="Voyage API key"
            type="password"
            value={voyageKey}
            onChange={setVoyageKey}
            placeholder="pa-…"
            isInvalid={!!errors.voyage_api_key}
            hint={errors.voyage_api_key}
            inputClassName="font-mono"
          />
        </section>

        <section className="flex flex-col gap-3.5 rounded-[22px] border border-ink-800 bg-ink-900 p-6">
          <h2 className="text-[17px] font-semibold text-ink-100">Answers</h2>
          <div role="radiogroup" aria-label="Answer provider" className="flex gap-1.5 self-start rounded-full bg-ink-850 p-1">
            {PROVIDERS.map((p) => (
              <button
                key={p.id}
                type="button"
                role="radio"
                aria-checked={provider === p.id}
                onClick={() => {
                  setProvider(p.id);
                  setErrors((e) => ({ ...e, api_key: undefined, model: undefined }));
                }}
                className={`h-9 rounded-full px-4 text-sm transition ${
                  provider === p.id ? "bg-ink-100 text-ink-950" : "text-ink-100 hover:bg-ink-800"
                }`}
              >
                {p.name}
              </button>
            ))}
          </div>
          <Input
            label={current.keyLabel}
            type="password"
            value={apiKey}
            onChange={setApiKey}
            isInvalid={!!errors.api_key}
            hint={errors.api_key}
            inputClassName="font-mono"
          />
          {current.defaultModel !== null && (
            <Input
              label="Model"
              value={model}
              onChange={(v) => setModels((m) => ({ ...m, [provider]: v }))}
              isInvalid={!!errors.model}
              hint={errors.model}
              inputClassName="font-mono"
            />
          )}
        </section>
      </div>

      <div className="flex flex-wrap items-center gap-4">
        <button type="button" className={PILL_PRIMARY} disabled={!canSubmit || busy} onClick={submit}>
          {busy ? "Checking…" : "Check and continue"}
        </button>
        <span className="text-[13px] text-ink-500">Keys stay on this computer and are only ever sent to their own provider.</span>
      </div>
      {errors.form && (
        <p role="alert" className="-mt-5 text-sm text-error-primary">
          {errors.form}
        </p>
      )}
    </SetupShell>
  );
}
