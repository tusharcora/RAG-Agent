"use client";

import { useState, type KeyboardEvent } from "react";
import { TextArea } from "@/components/base/textarea/textarea";
import { Button } from "@/components/base/buttons/button";

export function ChatInput({ disabled, onSend }: { disabled: boolean; onSend: (question: string) => void }) {
  const [value, setValue] = useState("");

  const submit = () => {
    const trimmed = value.trim();
    if (!trimmed || disabled) return;
    onSend(trimmed);
    setValue("");
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  return (
    <div className="border-t border-ink-800 bg-ink-900/40 p-3">
      <div className="flex items-end gap-2">
        <TextArea
          value={value}
          onChange={setValue}
          onKeyDown={handleKeyDown}
          isDisabled={disabled}
          placeholder={disabled ? "Waiting for the current answer…" : "Ask about your Notion or Jira content…"}
          rows={1}
          className="flex-1"
          textAreaClassName="max-h-40"
        />
        <Button color="primary" size="md" isDisabled={disabled || !value.trim()} onPress={submit}>
          Send
        </Button>
      </div>
    </div>
  );
}
