import { SearchLg } from "@untitledui/icons";
import { Button } from "@/components/base/buttons/button";
import { EmptyState } from "@/components/base/empty-state/empty-state";

export default function NotFound() {
  return (
    <div className="flex h-[calc(100vh-57px)] items-center justify-center px-6">
      <div className="text-center">
        <EmptyState icon={SearchLg} title="Page not found" description="Nothing lives at this URL." />
        <Button color="secondary" size="sm" href="/">
          Go home
        </Button>
      </div>
    </div>
  );
}
