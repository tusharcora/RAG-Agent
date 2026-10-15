import type { ReactNode } from "react";

/** Full-screen frame shared by the first-run screens. */
export function SetupShell({
  eyebrow,
  title,
  description,
  back,
  status,
  children,
}: {
  eyebrow: string;
  title: ReactNode;
  description?: ReactNode;
  back?: ReactNode;
  status?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="min-h-screen bg-ink-950 px-6 py-14 md:px-[120px] md:py-[72px]">
      <div className="mx-auto flex max-w-[880px] flex-col gap-9">
        <header className="flex flex-col gap-3.5">
          {back}
          {status}
          <span className="font-mono text-xs tracking-[0.1em] text-ink-500">{eyebrow}</span>
          <h1 className="font-serif text-[44px] leading-[1.02] tracking-[-0.01em] text-ink-100 md:text-[60px]">{title}</h1>
          {description && <p className="max-w-[620px] text-base leading-relaxed text-ink-400">{description}</p>}
        </header>
        {children}
      </div>
    </div>
  );
}

export const PILL_PRIMARY =
  "inline-flex h-12 items-center justify-center gap-2 rounded-full bg-ink-100 px-6 text-[15px] font-medium text-ink-950 transition hover:bg-ink-200 disabled:cursor-not-allowed disabled:bg-ink-700 disabled:text-ink-400";

export function BackButton({ onClick }: { onClick: () => void }) {
  return (
    <button type="button" onClick={onClick} className="self-start text-sm text-ink-400 transition hover:text-ink-100">
      ← Back
    </button>
  );
}
