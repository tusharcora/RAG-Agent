"use client";

import { useEffect, useState } from "react";
import { getOrgMembers } from "@/lib/api";
import { Table, TableCard } from "@/components/application/table/table";
import { Badge } from "@/components/base/badges/badges";
import { Avatar } from "@/components/base/avatar/avatar";
import { TableRowsSkeleton } from "@/components/base/skeleton/skeleton";
import type { OrgMember } from "@/lib/types";

const ROLE_COLOR: Record<string, "success" | "warning" | "gray"> = {
  owner: "success",
  admin: "warning",
  member: "gray",
};

// Read-only — GET /org/members is the only member-management endpoint this
// backend exposes today (no remove-member or change-role route), so there is
// nothing to wire a mutation to yet.
export function MembersSection() {
  const [members, setMembers] = useState<OrgMember[] | null>(null);

  useEffect(() => {
    getOrgMembers()
      .then(setMembers)
      .catch(() => setMembers([]));
  }, []);

  return (
    <TableCard.Root>
      <TableCard.Header title="Members" description="Everyone in your organization." />
      {members === null ? (
        <TableRowsSkeleton columns={3} rows={3} />
      ) : (
        <Table aria-label="Organization members" size="sm">
          <Table.Header>
            <Table.Head id="name" label="Name" isRowHeader className="w-full" />
            <Table.Head id="email" label="Email" />
            <Table.Head id="role" label="Role" />
          </Table.Header>
          <Table.Body items={members}>
            {(m) => (
              <Table.Row id={m.user_id}>
                <Table.Cell>
                  <span className="flex items-center gap-2">
                    <Avatar size="xs" initials={(m.display_name || m.email).slice(0, 1).toUpperCase()} />
                    <span className="truncate font-medium text-secondary">{m.display_name || m.email}</span>
                  </span>
                </Table.Cell>
                <Table.Cell className="text-tertiary">{m.email}</Table.Cell>
                <Table.Cell>
                  <Badge type="pill-color" color={ROLE_COLOR[m.role] ?? "gray"} size="sm" className="capitalize">
                    {m.role}
                  </Badge>
                </Table.Cell>
              </Table.Row>
            )}
          </Table.Body>
        </Table>
      )}
    </TableCard.Root>
  );
}
