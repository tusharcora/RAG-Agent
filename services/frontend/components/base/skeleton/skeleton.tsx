import { cx } from "@/utils/cx";

export function Skeleton({ className }: { className?: string }) {
  return <div className={cx("animate-pulse rounded-md bg-ink-800", className)} />;
}

export function SessionListSkeleton() {
  return (
    <div className="space-y-1 px-2">
      {Array.from({ length: 6 }).map((_, i) => (
        <Skeleton key={i} className="h-9 w-full rounded-lg" />
      ))}
    </div>
  );
}

export function ConnectionCardSkeleton() {
  return (
    <div className="cyber-chamfer-sm space-y-3 rounded-xl border border-ink-800 bg-ink-900/60 p-4">
      <div className="flex items-center gap-3">
        <Skeleton className="h-8 w-8 rounded-lg" />
        <Skeleton className="h-4 w-32" />
      </div>
      <Skeleton className="h-3 w-48" />
      <Skeleton className="h-3 w-40" />
      <Skeleton className="h-8 w-24 rounded-lg" />
    </div>
  );
}

export function TableRowsSkeleton({ columns = 4, rows = 6 }: { columns?: number; rows?: number }) {
  return (
    <div className="space-y-3 p-4">
      {Array.from({ length: rows }).map((_, r) => (
        <div key={r} className="flex gap-4">
          {Array.from({ length: columns }).map((_, c) => (
            <Skeleton key={c} className={cx("h-4", c === 0 ? "w-1/3" : "flex-1")} />
          ))}
        </div>
      ))}
    </div>
  );
}

export function DocumentDrawerSkeleton() {
  return (
    <div className="space-y-4 p-4">
      <Skeleton className="h-5 w-2/3" />
      <Skeleton className="h-3 w-1/3" />
      <div className="space-y-2 pt-4">
        {Array.from({ length: 3 }).map((_, i) => (
          <Skeleton key={i} className="h-20 w-full rounded-lg" />
        ))}
      </div>
    </div>
  );
}
