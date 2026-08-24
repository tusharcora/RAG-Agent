import { Badge } from "@/components/base/badges/badges";
import type { BadgeColor } from "@/components/base/badges/badges";
import { badgeTypes } from "@/components/base/badges/badge-types";

const COLORS: Record<string, BadgeColor<typeof badgeTypes.pillColor>> = {
  received: "gray",
  processing: "warning",
  succeeded: "success",
  failed: "error",
  dead_lettered: "error",
};

export function StatusBadge({ status }: { status: string }) {
  return (
    <Badge type={badgeTypes.pillColor} color={COLORS[status] ?? "gray"} size="sm">
      {status}
    </Badge>
  );
}
