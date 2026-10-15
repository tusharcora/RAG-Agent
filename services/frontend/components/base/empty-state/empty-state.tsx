import type { FC, ReactNode } from "react";
import { Button } from "@/components/base/buttons/button";

export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
}: {
  icon?: FC<{ className?: string }>;
  title: string;
  description?: ReactNode;
  action?: { label: string; onPress: () => void };
}) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-12 text-center">
      {Icon && (
        <div className="mb-3 flex h-10 w-10 items-center justify-center rounded-full bg-ink-800">
          <Icon className="h-5 w-5 text-ink-500" />
        </div>
      )}
      <p className="text-sm font-medium text-ink-200">{title}</p>
      {description && <p className="mt-1 max-w-sm text-sm text-ink-500">{description}</p>}
      {action && (
        <Button color="secondary" size="sm" className="mt-4" onPress={action.onPress}>
          {action.label}
        </Button>
      )}
    </div>
  );
}
