"use client";

import { useCallback, useRef, useState } from "react";
import { completeLocal, formatBytes, formatProgress, getSetupStatus, installedName, isInstalled, pullModel, type SetupStatus } from "@/lib/setup";
import { Button } from "@/components/base/buttons/button";
import { PILL_PRIMARY, SetupShell } from "./SetupShell";

interface Row {
  state: "idle" | "pulling" | "done" | "error";
  completed?: number;
  total?: number;
  status?: string;
  message?: string;
}

// Approximate download sizes, shown before anything is downloaded.
const SIZE_HINT: Record<string, string> = { "nomic-embed-text": "274 MB", "llama3.2": "2.0 GB" };

// Picks the answer model to preselect: the default if it's already installed,
// otherwise a model the user already has, and only failing both the default
// (which then needs a download).
function initialAnswerModel(defaultAnswer: string, candidates: string[], installed: string[]): string {
  return installedName(defaultAnswer, installed) ?? candidates[0] ?? defaultAnswer;
}

export function ModelsStep({ initial, onDone }: { initial: SetupStatus; onDone: () => void }) {
  const [embedModel, defaultAnswer] = initial.required_models;
  const [installed, setInstalled] = useState<string[]>(initial.ollama.models);
  const [answerModel, setAnswerModel] = useState(() =>
    initialAnswerModel(defaultAnswer, initial.answer_model_candidates, initial.ollama.models),
  );
  const [rows, setRows] = useState<Record<string, Row>>({});
  const [finishing, setFinishing] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const installedRef = useRef(installed);
  installedRef.current = installed;
  const running = useRef(false);

  const pulling = Object.values(rows).some((r) => r.state === "pulling");

  // Downloads whatever in `wanted` is missing, one model at a time. Returns
  // false at the first failure. Nothing is downloaded until the user asks.
  const pullMissing = useCallback(async (wanted: string[]): Promise<boolean> => {
    if (running.current) return false;
    running.current = true;
    try {
      for (const model of wanted) {
        if (isInstalled(model, installedRef.current)) continue;
        setRows((r) => ({ ...r, [model]: { state: "pulling" } }));
        try {
          await pullModel(model, (p) =>
            setRows((r) => ({
              ...r,
              [model]: {
                state: "pulling",
                completed: p.completed ?? r[model]?.completed,
                total: p.total ?? r[model]?.total,
                status: p.status,
              },
            })),
          );
        } catch (e) {
          setRows((r) => ({ ...r, [model]: { ...r[model], state: "error", message: e instanceof Error ? e.message : "Download failed" } }));
          return false;
        }
        setRows((r) => ({ ...r, [model]: { ...r[model], state: "done" } }));
        installedRef.current = [...installedRef.current, model];
        setInstalled(installedRef.current);
      }
      return true;
    } finally {
      running.current = false;
      getSetupStatus()
        .then((s) => setInstalled(s.ollama.models))
        .catch(() => {});
    }
  }, []);

  const embedInstalled = isInstalled(embedModel, installed);
  const answerInstalled = isInstalled(answerModel, installed);
  const missing = [embedModel, answerModel].filter((m) => !isInstalled(m, installed));

  const answerOptions = [
    ...initial.answer_model_candidates.map((m) => ({ value: m, label: `${m} · installed` })),
    ...(isInstalled(defaultAnswer, initial.ollama.models)
      ? []
      : [{ value: defaultAnswer, label: `${defaultAnswer} · download ${SIZE_HINT[defaultAnswer] ?? ""}`.trim() }]),
  ];

  const onContinue = async () => {
    setSubmitError(null);
    if (missing.length > 0 && !(await pullMissing(missing))) return;
    setFinishing(true);
    try {
      await completeLocal(installedName(answerModel, installedRef.current) ?? answerModel);
      onDone();
    } catch (e) {
      setSubmitError((e as { detail?: string }).detail || (e instanceof Error ? e.message : "Couldn't finish setup."));
      setFinishing(false);
    }
  };

  const continueLabel =
    missing.length === 0
      ? "Continue"
      : `Download ${missing.map((m) => SIZE_HINT[m] ?? m).join(" + ")} and continue`;

  return (
    <SetupShell
      status={
        <span className="inline-flex items-center gap-2 text-sm text-success-primary">
          <span className="h-2.5 w-2.5 rounded-full bg-current" aria-hidden="true" />
          Ollama is running
        </span>
      }
      eyebrow="SET UP · STEP 3 OF 3"
      title={missing.length === 0 ? "Your models are ready" : "Choose your models"}
    >
      <div className="flex flex-col rounded-[22px] border border-ink-800 bg-ink-900">
        <ModelRow
          label="Search model"
          model={embedModel}
          blurb="Reads your documents so the right passages can be found"
          installed={embedInstalled}
          installedAs={installedName(embedModel, installed)}
          sizeHint={SIZE_HINT[embedModel]}
          note="Needed for search; your chat models can't do this job."
          row={rows[embedModel]}
          onRetry={() => void pullMissing([embedModel])}
          bordered
        />
        <ModelRow
          label="Answer model"
          model={answerModel}
          blurb="Writes the answers, with citations"
          installed={answerInstalled}
          installedAs={installedName(answerModel, installed)}
          sizeHint={SIZE_HINT[answerModel]}
          row={rows[answerModel]}
          onRetry={() => void pullMissing([answerModel])}
        >
          <label className="flex flex-wrap items-center gap-2.5 text-[13px] text-ink-400">
            {initial.answer_model_candidates.length > 0 ? "Use one of your models:" : "Model:"}
            <select
              value={answerModel}
              disabled={pulling}
              onChange={(e) => setAnswerModel(e.target.value)}
              className="h-9 rounded-[10px] border border-ink-700 bg-ink-950 px-2.5 font-mono text-[13px] text-ink-100 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {answerOptions.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </label>
        </ModelRow>
      </div>

      <div className="flex flex-wrap items-center gap-4">
        <button type="button" className={PILL_PRIMARY} disabled={pulling || finishing} onClick={onContinue}>
          {pulling ? "Downloading…" : continueLabel}
        </button>
        {missing.length > 0 && (
          <span className="text-[13px] text-ink-500">Nothing downloads until you press this. If it stops, Retry picks up where it left off.</span>
        )}
      </div>
      {submitError && (
        <p role="alert" className="-mt-5 text-sm text-error-primary">
          {submitError}
        </p>
      )}
    </SetupShell>
  );
}

function ModelRow({
  label,
  model,
  blurb,
  installed,
  installedAs,
  sizeHint,
  note,
  row,
  onRetry,
  bordered,
  children,
}: {
  label: string;
  model: string;
  blurb: string;
  installed: boolean;
  installedAs?: string | null;
  sizeHint?: string;
  note?: string;
  row?: Row;
  onRetry: () => void;
  bordered?: boolean;
  children?: React.ReactNode;
}) {
  const failed = row?.state === "error";
  const hasTotal = row?.total !== undefined && row.total > 0;
  const pct = installed ? 100 : hasTotal ? Math.min(100, ((row?.completed ?? 0) / row!.total!) * 100) : 0;

  let right: React.ReactNode;
  if (installed) {
    right = <span className="text-success-primary">✓ {hasTotal ? `${formatBytes(row!.total!)} · done` : row?.state === "done" ? "done" : "installed"}</span>;
  } else if (failed) {
    right = <span className="text-error-primary">Failed</span>;
  } else if (row?.state === "pulling") {
    right = hasTotal ? formatProgress(row.completed ?? 0, row.total!) : row.status || "Starting…";
  } else {
    right = sizeHint ? `${sizeHint} download` : "Not installed";
  }

  return (
    <div className={`flex flex-col gap-3 px-7 py-[22px] ${bordered ? "border-b border-ink-800" : ""}`}>
      <div className="flex items-baseline justify-between gap-4">
        <span className="flex flex-col gap-[3px]">
          <span className="text-base font-semibold text-ink-100">
            {label} <span className="font-mono text-[13px] font-normal text-ink-500">{installedAs ?? model}</span>
          </span>
          <span className="text-[13px] text-ink-400">{blurb}</span>
          {!installed && note && <span className="text-[13px] text-ink-500">{note}</span>}
        </span>
        <span className="shrink-0 font-mono text-[13px] text-ink-400">{right}</span>
      </div>
      <div
        role="progressbar"
        aria-label={`${model} download`}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(pct)}
        className="h-1.5 overflow-hidden rounded-full bg-ink-800"
      >
        <div className="h-1.5 rounded-full bg-ink-100 transition-[width] duration-300" style={{ width: `${pct}%` }} />
      </div>
      {failed && (
        <div className="flex flex-wrap items-center gap-3">
          <p role="alert" className="text-[13px] text-error-primary">
            {row?.message}
          </p>
          <Button size="sm" color="secondary" onPress={onRetry}>
            Retry
          </Button>
        </div>
      )}
      {children}
    </div>
  );
}
