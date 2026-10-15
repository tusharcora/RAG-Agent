"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { completeLocal, formatBytes, formatProgress, getSetupStatus, isInstalled, pullModel, type SetupStatus } from "@/lib/setup";
import { Button } from "@/components/base/buttons/button";
import { PILL_PRIMARY, SetupShell } from "./SetupShell";

interface Row {
  state: "idle" | "pulling" | "done" | "error";
  completed?: number;
  total?: number;
  status?: string;
  message?: string;
}

export function ModelsStep({ initial, onDone }: { initial: SetupStatus; onDone: () => void }) {
  const [embedModel, defaultAnswer] = initial.required_models;
  const [installed, setInstalled] = useState<string[]>(initial.ollama.models);
  const [answerModel, setAnswerModel] = useState(defaultAnswer);
  const [rows, setRows] = useState<Record<string, Row>>({});
  const [finishing, setFinishing] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const installedRef = useRef(installed);
  installedRef.current = installed;
  const running = useRef(false);

  const patch = (model: string, row: Row) => setRows((r) => ({ ...r, [model]: row }));
  const pulling = Object.values(rows).some((r) => r.state === "pulling");

  // Downloads whatever is missing, one model at a time, stopping at a failure.
  const pullMissing = useCallback(
    async (wanted: string[]) => {
      if (running.current) return;
      running.current = true;
      try {
        for (const model of wanted) {
          if (isInstalled(model, installedRef.current)) continue;
          patch(model, { state: "pulling" });
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
            return;
          }
          setRows((r) => ({ ...r, [model]: { ...r[model], state: "done" } }));
          setInstalled((cur) => (isInstalled(model, cur) ? cur : [...cur, model]));
          installedRef.current = [...installedRef.current, model];
          getSetupStatus()
            .then((s) => setInstalled(s.ollama.models))
            .catch(() => {});
        }
      } finally {
        running.current = false;
      }
    },
    [],
  );

  useEffect(() => {
    void pullMissing([embedModel, answerModel]);
  }, [answerModel, embedModel, pullMissing]);

  const retry = (model: string) => void pullMissing([embedModel, answerModel].filter((m) => m === model || !isInstalled(m, installedRef.current)));

  const ready = isInstalled(embedModel, installed) && isInstalled(answerModel, installed);

  const answerOptions = [
    { value: defaultAnswer, label: `${defaultAnswer} (download)` },
    ...initial.answer_model_candidates
      .filter((m) => m.replace(/:latest$/, "") !== defaultAnswer)
      .map((m) => ({ value: m, label: `${m} · installed` })),
  ];

  const onContinue = async () => {
    setFinishing(true);
    setSubmitError(null);
    try {
      await completeLocal(answerModel);
      onDone();
    } catch (e) {
      setSubmitError((e as { detail?: string }).detail || (e instanceof Error ? e.message : "Couldn't finish setup."));
      setFinishing(false);
    }
  };

  return (
    <SetupShell
      status={
        <span className="inline-flex items-center gap-2 text-sm text-success-primary">
          <span className="h-2.5 w-2.5 rounded-full bg-current" aria-hidden="true" />
          Ollama is running
        </span>
      }
      eyebrow="SET UP · STEP 3 OF 3"
      title="Download the models"
    >
      <div className="flex flex-col rounded-[22px] border border-ink-800 bg-ink-900">
        <ModelRow
          label="Search model"
          model={embedModel}
          blurb="Reads your documents so the right passages can be found"
          installed={isInstalled(embedModel, installed)}
          row={rows[embedModel]}
          onRetry={() => retry(embedModel)}
          bordered
        />
        <ModelRow
          label="Answer model"
          model={answerModel}
          blurb="Writes the answers, with citations"
          installed={isInstalled(answerModel, installed)}
          row={rows[answerModel]}
          onRetry={() => retry(answerModel)}
        >
          <label className="flex flex-wrap items-center gap-2.5 text-[13px] text-ink-400">
            Or use a model you already have:
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
        <button type="button" className={PILL_PRIMARY} disabled={!ready || finishing} onClick={onContinue}>
          Continue
        </button>
        <span className="text-[13px] text-ink-500">If the download stops, Retry picks up where it left off.</span>
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
  row,
  onRetry,
  bordered,
  children,
}: {
  label: string;
  model: string;
  blurb: string;
  installed: boolean;
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
    right = "Waiting…";
  }

  return (
    <div className={`flex flex-col gap-3 px-7 py-[22px] ${bordered ? "border-b border-ink-800" : ""}`}>
      <div className="flex items-baseline justify-between gap-4">
        <span className="flex flex-col gap-[3px]">
          <span className="text-base font-semibold text-ink-100">
            {label} <span className="font-mono text-[13px] font-normal text-ink-500">{model}</span>
          </span>
          <span className="text-[13px] text-ink-400">{blurb}</span>
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
