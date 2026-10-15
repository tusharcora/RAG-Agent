"use client";

import { useState, type KeyboardEvent, type ReactNode } from "react";
import { ArrowUp, Stop } from "@untitledui/icons";
import { TextArea } from "@/components/base/textarea/textarea";

/**
 * The composer: one rounded pill surface with the send button inside it.
 * "hero" is the large first-run version on Home; "dock" is the follow-up bar
 * under a conversation. The textarea's own chrome is stripped so the outer
 * div is the only visible surface.
 */
export function ChatInput({
  disabled,
  onSend,
  isStreaming,
  onStop,
  variant = "dock",
  placeholder,
  adornment,
}: {
  disabled: boolean;
  onSend: (question: string) => void;
  isStreaming?: boolean;
  onStop?: () => void;
  variant?: "hero" | "dock";
  placeholder?: string;
  adornment?: ReactNode;
}) {
  const [value, setValue] = useState("");
  const hero = variant === "hero";

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

  const buttonSize = hero ? "h-[52px] w-[52px]" : "h-11 w-11";

  return (
    <div
      className={`flex items-center gap-3 rounded-[28px] bg-ink-900 shadow-[0_0_0_1px_var(--color-ink-700),0_18px_40px_-24px_rgb(0_0_0/0.35)] transition focus-within:shadow-[0_0_0_1px_var(--color-ink-100),0_18px_40px_-24px_rgb(0_0_0/0.35)] ${
        hero ? "py-2.5 pr-2.5 pl-7" : "py-2 pr-2 pl-[22px]"
      }`}
    >
      <TextArea
        aria-label={hero ? "Ask a question" : "Ask a follow-up"}
        value={value}
        onChange={setValue}
        onKeyDown={handleKeyDown}
        isDisabled={disabled}
        placeholder={disabled ? "Waiting for the current answer…" : placeholder ?? (hero ? "What would you like to know?" : "Ask a follow-up…")}
        rows={1}
        className="min-w-0 flex-1 self-center"
        textAreaClassName={`max-h-48 resize-none rounded-none border-0 bg-transparent px-0 py-1.5 shadow-none ring-0 focus:ring-0 ${
          hero ? "text-[17px]" : "text-[15px]"
        }`}
      />
      {adornment}
      {isStreaming ? (
        <button
          type="button"
          onClick={onStop}
          aria-label="Stop generating"
          className={`flex shrink-0 items-center justify-center rounded-full border border-ink-700 bg-ink-900 text-ink-100 transition hover:bg-ink-800 ${buttonSize}`}
        >
          <Stop className="h-[18px] w-[18px]" aria-hidden="true" />
        </button>
      ) : (
        <button
          type="button"
          onClick={submit}
          disabled={disabled || !value.trim()}
          aria-label="Send"
          className={`flex shrink-0 items-center justify-center rounded-full bg-ink-100 text-ink-950 transition hover:scale-105 disabled:opacity-40 disabled:hover:scale-100 ${buttonSize}`}
        >
          <ArrowUp className="h-[18px] w-[18px]" aria-hidden="true" />
        </button>
      )}
    </div>
  );
}
