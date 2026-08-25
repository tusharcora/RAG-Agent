import { AlertCircle } from "@untitledui/icons";
import { Button } from "@/components/base/buttons/button";

export function ErrorState({
  title = "Something went wrong",
  description,
  onRetry,
}: {
  title?: string;
  description?: string;
  onRetry?: () => void;
}) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-12 text-center">
      <div className="mb-3 flex h-10 w-10 items-center justify-center rounded-full bg-coral-500/10">
        <AlertCircle className="h-5 w-5 text-coral-400" />
      </div>
      <p className="text-sm font-medium text-ink-200">{title}</p>
      {description && <p className="mt-1 max-w-sm text-sm text-ink-500">{description}</p>}
      {onRetry && (
        <Button color="secondary" size="sm" className="mt-4" onPress={onRetry}>
          Retry
        </Button>
      )}
    </div>
  );
}
