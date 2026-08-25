"use client";

import { useState, type KeyboardEvent } from "react";
import { ArrowUp, Stop } from "@untitledui/icons";
import { TextArea } from "@/components/base/textarea/textarea";
import { Button } from "@/components/base/buttons/button";

/**
 * The composer, styled as a single rounded pill-like surface rather than a
 * plain rectangular textarea + separate button - the textarea itself is
 * stripped of its own border/ring/shadow (textAreaClassName overrides win
 * because tailwind-merge resolves conflicting utilities by which one is
 * later in the merged class string, and textAreaClassName is always merged
 * in last) so the surrounding div is the only visible chrome, and the send
 * button is a circular icon button embedded inside it.
 */
export function ChatInput({
  disabled,
  onSend,
  isStreaming,
  onStop,
}: {
  disabled: boolean;
  onSend: (question: string) => void;
  isStreaming?: boolean;
  onStop?: () => void;
}) {
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
    <div className="cyber-chamfer flex items-end gap-2 rounded-[28px] border border-ink-700 bg-ink-900/80 py-2 pr-2 pl-4 shadow-panel transition focus-within:border-coral-500/50">
      <span className="cyber-terminal-prompt self-center">&gt;</span>
      <TextArea
        aria-label="Ask a question"
        value={value}
        onChange={setValue}
        onKeyDown={handleKeyDown}
        isDisabled={disabled}
        placeholder={disabled ? "Waiting for the current answer…" : "Ask about your Notion or Jira content..."}
        rows={1}
        className="flex-1 self-center"
        textAreaClassName="max-h-48 resize-none rounded-none border-0 bg-transparent px-0 py-1.5 shadow-none ring-0 focus:ring-0"
      />
      {isStreaming ? (
        <Button
          color="secondary"
          size="md"
          onPress={onStop}
          iconLeading={Stop}
          className="shrink-0 self-end rounded-full"
          aria-label="Stop generating"
        />
      ) : (
        <Button
          color="primary"
          size="md"
          isDisabled={disabled || !value.trim()}
          onPress={submit}
          iconLeading={ArrowUp}
          className="shrink-0 self-end rounded-full"
          aria-label="Send"
        />
      )}
    </div>
  );
}
