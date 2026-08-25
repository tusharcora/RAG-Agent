"use client";

import { useEffect } from "react";
import { Button } from "@/components/base/buttons/button";
import { ErrorState } from "@/components/base/error-state/error-state";

export default function ErrorBoundary({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <div className="flex h-[calc(100vh-57px)] items-center justify-center px-6">
      <div className="text-center">
        <ErrorState
          title="This page hit an unexpected error"
          description="You can try again, or head back and pick up where you left off."
        />
        <div className="mt-2 flex justify-center gap-2">
          <Button color="secondary" size="sm" href="/">
            Go home
          </Button>
          <Button color="primary" size="sm" onPress={reset}>
            Try again
          </Button>
        </div>
      </div>
    </div>
  );
}
