"use client";

import { useState } from "react";
import { Copy01 } from "@untitledui/icons";
import { inviteMember } from "@/lib/api";
import { toast } from "@/lib/toast";
import { useCopyToClipboard } from "@/hooks/useCopyToClipboard";
import { Button } from "@/components/base/buttons/button";
import { Input } from "@/components/base/input/input";
import { NativeSelect } from "@/components/base/select/select-native";
import { Modal } from "@/components/base/modal/modal";

const ROLE_OPTIONS = [
  { label: "Member", value: "member" },
  { label: "Admin", value: "admin" },
];

// Owner/admin only — matches the require_role(auth, "owner", "admin") check
// on POST /auth/invite; a member calling this would just get a 403.
export function InviteSection() {
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("member");
  const [sending, setSending] = useState(false);
  const [inviteToken, setInviteToken] = useState<string | null>(null);
  const copy = useCopyToClipboard();

  async function handleInvite() {
    if (!email.trim()) return;
    setSending(true);
    try {
      const result = await inviteMember(email.trim(), role);
      setInviteToken(result.token);
      setEmail("");
      setRole("member");
    } catch {
      toast.error("Couldn't send invite", "Please try again.");
    } finally {
      setSending(false);
    }
  }

  return (
    <section className="cyber-chamfer rounded-2xl border border-ink-800 bg-ink-900/60 p-5 shadow-panel">
      <h2 className="mb-1 text-sm font-semibold text-ink-100">Invite a member</h2>
      <p className="mb-3 text-sm text-ink-500">
        Generates a one-time invite token to share manually — no email is sent yet.
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <Input
          size="sm"
          type="email"
          placeholder="teammate@company.com"
          value={email}
          onChange={setEmail}
          wrapperClassName="min-w-56 flex-1"
        />
        <NativeSelect
          size="sm"
          options={ROLE_OPTIONS}
          value={role}
          onChange={(e) => setRole(e.target.value)}
          className="w-32"
        />
        <Button color="secondary" size="sm" isLoading={sending} isDisabled={!email.trim()} onPress={handleInvite}>
          Send invite
        </Button>
      </div>

      <Modal
        isOpen={inviteToken !== null}
        onOpenChange={(open) => !open && setInviteToken(null)}
        aria-label="Invite created"
      >
        <p className="text-sm font-semibold text-ink-100">Invite created</p>
        <p className="mt-1.5 text-sm text-ink-400">
          Share this token with the invitee manually — no email was sent. It expires in 7 days.
        </p>
        <div className="mt-3 flex items-center gap-2 rounded-lg border border-ink-800 bg-ink-950/60 px-3 py-2">
          <code className="flex-1 truncate font-mono text-xs text-ink-200">{inviteToken}</code>
          <button
            type="button"
            aria-label="Copy invite token"
            onClick={() => inviteToken && copy(inviteToken, "Invite token copied")}
            className="shrink-0 rounded-md p-1 text-ink-500 transition hover:text-ink-200"
          >
            <Copy01 className="h-4 w-4" />
          </button>
        </div>
        <div className="mt-4 flex justify-end">
          <Button color="secondary" size="sm" onPress={() => setInviteToken(null)}>
            Done
          </Button>
        </div>
      </Modal>
    </section>
  );
}
