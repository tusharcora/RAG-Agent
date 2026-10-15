import type { ReactNode } from "react";

/** Atelier page header: a small mono eyebrow over a large serif title. */
export function PageHeader({
  eyebrow,
  title,
  description,
  trailing,
}: {
  eyebrow: string;
  title: ReactNode;
  description?: ReactNode;
  trailing?: ReactNode;
}) {
  return (
    <header className="flex flex-wrap items-end justify-between gap-5">
      <div className="flex flex-col gap-2.5">
        <span className="font-mono text-[11px] tracking-[0.1em] text-ink-500">{eyebrow}</span>
        <h1 className="font-serif text-[44px] leading-none tracking-[-0.015em] text-ink-100 md:text-[52px]">{title}</h1>
        {description && <p className="max-w-xl text-sm text-ink-400">{description}</p>}
      </div>
      {trailing}
    </header>
  );
}
