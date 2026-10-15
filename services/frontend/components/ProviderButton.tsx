"use client";

import type { ReactNode } from "react";

const SHOW_MARKS = process.env.NEXT_PUBLIC_BRAND_MARKS === "1";

/** Sign-in button for Google or GitHub. The mark is the official file from
 *  public/brand/, shown only once NEXT_PUBLIC_BRAND_MARKS=1; otherwise text only. */
export function ProviderButton({
  provider,
  onPress,
  isDisabled,
  className = "",
  children,
}: {
  provider: "google" | "github";
  onPress: () => void;
  isDisabled?: boolean;
  className?: string;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onPress}
      disabled={isDisabled}
      className={`inline-flex h-12 items-center justify-center gap-2.5 rounded-full border border-ink-700 bg-ink-900 px-6 text-[15px] font-medium text-ink-100 transition hover:bg-ink-850 disabled:cursor-not-allowed disabled:opacity-50 ${className}`}
    >
      {SHOW_MARKS && <img src={`/brand/${provider}.svg`} alt="" className="h-[18px] w-[18px]" />}
      {children}
    </button>
  );
}
