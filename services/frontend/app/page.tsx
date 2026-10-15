"use client";

import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Copy01, RefreshCw01 } from "@untitledui/icons";
import { getConnections, getSession, setMessageFeedback } from "@/lib/api";
import { streamQuery } from "@/lib/sse";
import { useAuth } from "@/lib/auth-context";
import { toast } from "@/lib/toast";
import { providerLabel, SESSIONS_CHANGED_EVENT } from "@/lib/format";
import { useCopyToClipboard } from "@/hooks/useCopyToClipboard";
import type { ChatMessage, ConnectionStatus, Source } from "@/lib/types";
import { SourcesPanel } from "@/components/chat/SourcesPanel";
import { ChatInput } from "@/components/chat/ChatInput";
import { CitationText } from "@/components/chat/CitationText";
import { LandingPage } from "@/components/landing/LandingPage";
import { HomeView } from "@/components/home/HomeView";

// Sent as a normal follow-up question in the same session when the user hits
// "Continue" — Gemini already has the truncated turn in its own context via
// load_history, so no dedicated backend endpoint is needed, just a nudge.
const CONTINUATION_PROMPT = "Please continue your previous answer from exactly where it left off, with no repetition.";

export default function HomePage() {
  const { user, loading } = useAuth();

  if (loading) {
    return (
      <div className="flex h-screen items-center justify-center">
        <span className="h-6 w-6 animate-spin rounded-full border-2 border-ink-700 border-t-ink-100" />
      </div>
    );
  }
  if (!user) return <LandingPage />;
  return (
    <Suspense fallback={null}>
      <ChatPage />
    </Suspense>
  );
}

function ChatPage() {
  const sessionParam = useSearchParams().get("session");
  const { user } = useAuth();
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [draftAnswer, setDraftAnswer] = useState("");
  const [draftSources, setDraftSources] = useState<Source[]>([]);
  // True only while a "Continue" call is in flight — tells the render below
  // to append the stream onto the last bubble instead of opening a new one.
  const [continuing, setContinuing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const sessionIdRef = useRef<string | null>(null);
  const [connections, setConnections] = useState<ConnectionStatus[]>([]);
  const scrollRef = useRef<HTMLDivElement>(null);
  // Set when a [n] citation marker in an answer is clicked; cleared after a
  // brief flash so SourcesPanel's highlight ring is transient, not sticky.
  const [highlightedSourceIndex, setHighlightedSourceIndex] = useState<number | null>(null);
  // The last real user question — CONTINUATION_PROMPT never overwrites this,
  // so Regenerate always redoes the actual last question, not the synthetic
  // "continue" nudge.
  const [lastQuestion, setLastQuestion] = useState<string | null>(null);
  // Lets a "Stop" button cut off an in-flight stream — see lib/sse.ts.
  const abortControllerRef = useRef<AbortController | null>(null);

  const handleCitationClick = useCallback((index: number) => {
    setHighlightedSourceIndex(index);
    setTimeout(() => setHighlightedSourceIndex((current) => (current === index ? null : current)), 1200);
  }, []);

  const scrollToBottom = () => {
    requestAnimationFrame(() => scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight }));
  };

  type StreamMode = "new" | "continue" | "regenerate";

  const runStream = (question: string, mode: StreamMode) => {
    setError(null);
    setContinuing(mode === "continue");
    if (mode === "new") {
      setLastQuestion(question);
      setMessages((prev) => [...prev, { role: "user", content: question }]);
      // The backend records this turn (and indexes the session) as soon as the
      // question is sent, not once the answer finishes — bumping this right
      // away lets the sidebar pick up a brand-new conversation immediately
      // instead of waiting for its 15s poll.
      window.dispatchEvent(new Event(SESSIONS_CHANGED_EVENT));
    } else if (mode === "regenerate") {
      // Drop the answer being replaced so the new stream lands in its place
      // instead of appending a duplicate bubble below it.
      setMessages((prev) => {
        const idx = prev.map((m) => m.role).lastIndexOf("assistant");
        return idx === -1 ? prev : prev.slice(0, idx);
      });
    }
    setIsStreaming(true);
    setDraftAnswer("");
    setDraftSources([]);
    scrollToBottom();

    const controller = new AbortController();
    abortControllerRef.current = controller;

    // Scoped to this call, not React state — SSE events for this call arrive
    // in order (sources before done), so this is always fresh when onDone reads it.
    let sourcesForThisTurn: Source[] = [];

    streamQuery(
      question,
      sessionId,
      {
        onSources: (sid, sources) => {
          adoptSession(sid);
          setDraftSources(sources);
          sourcesForThisTurn = sources;
          scrollToBottom();
        },
        onDelta: (text) => {
          setDraftAnswer((prev) => prev + text);
          scrollToBottom();
        },
        onDone: (sid, citedIndices, answer, truncated, messageId, meta) => {
          adoptSession(sid);
          window.dispatchEvent(new Event(SESSIONS_CHANGED_EVENT));
          if (mode === "continue") {
            // Concatenated with no separator on purpose — a MAX_TOKENS cutoff
            // can land mid-word, and the prompt asks Gemini to resume from
            // exactly there.
            setMessages((prev) => {
              const last = prev[prev.length - 1];
              return [
                ...prev.slice(0, -1),
                { ...last, content: last.content + answer, sources: sourcesForThisTurn, citedIndices, truncated, model: meta?.model, elapsedMs: meta?.elapsedMs },
              ];
            });
          } else {
            setMessages((prev) => [
              ...prev,
              { id: messageId || undefined, role: "assistant", content: answer, sources: sourcesForThisTurn, citedIndices, truncated, feedback: null, model: meta?.model, elapsedMs: meta?.elapsedMs },
            ]);
          }
          setIsStreaming(false);
          setContinuing(false);
          setDraftAnswer("");
          setDraftSources([]);
          scrollToBottom();
        },
        onError: (message) => {
          setError(message);
          setIsStreaming(false);
          setContinuing(false);
          setDraftAnswer("");
          scrollToBottom();
        },
      },
      controller.signal,
    );
  };

  const handleSend = (question: string) => runStream(question, "new");

  // Re-asks the truncated turn's own session for "more" — same SSE turn as a
  // normal question, just with no visible user bubble, and the reply gets
  // merged onto the existing assistant bubble instead of starting a new one
  // (see runStream's onDone). The backend still records CONTINUATION_PROMPT
  // as a real turn in Postgres; only the live render hides it.
  const handleContinue = () => runStream(CONTINUATION_PROMPT, "continue");

  const handleRegenerate = () => {
    if (lastQuestion) runStream(lastQuestion, "regenerate");
  };

  // Once a delta may already be on the wire there's no way to ask the server
  // to stop cleanly (see lib/sse.ts) — this just cancels the client-side read
  // and turns whatever text arrived so far into the final answer.
  const handleStop = () => {
    abortControllerRef.current?.abort();
    if (continuing) {
      setMessages((prev) => {
        const last = prev[prev.length - 1];
        if (!last) return prev;
        return [
          ...prev.slice(0, -1),
          { ...last, content: last.content + draftAnswer, sources: draftSources.length ? draftSources : last.sources, truncated: false },
        ];
      });
    } else if (draftAnswer.length > 0 || draftSources.length > 0) {
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: draftAnswer, sources: draftSources, citedIndices: undefined, truncated: false, feedback: null },
      ]);
    }
    setIsStreaming(false);
    setContinuing(false);
    setDraftAnswer("");
    setDraftSources([]);
    scrollToBottom();
  };

  const handleNewChat = () => {
    abortControllerRef.current?.abort();
    setIsStreaming(false);
    sessionIdRef.current = null;
    setSessionId(null);
    setMessages([]);
    setDraftAnswer("");
    setDraftSources([]);
    setContinuing(false);
    setError(null);
  };

  const handleSelectSession = async (id: string) => {
    try {
      const detail = await getSession(id);
      sessionIdRef.current = detail.session_id;
      setSessionId(detail.session_id);
      setMessages(
        detail.history.map((h) => ({
          id: h.id,
          role: h.role as "user" | "assistant",
          content: h.content,
          feedback: h.feedback,
          sources: h.sources ?? undefined,
          citedIndices: h.cited_indices ?? undefined,
          model: h.model ?? undefined,
          elapsedMs: h.elapsed_ms ?? undefined,
        }))
      );
      setDraftAnswer("");
      setDraftSources([]);
      setContinuing(false);
      setError(null);
      scrollToBottom();
    } catch {
      toast.error("Couldn't load that conversation", "It may have expired.");
    }
  };

  const handleFeedback = (index: number, next: "up" | "down" | null) => {
    const target = messages[index];
    if (!target?.id || !sessionId) return;
    const previous = target.feedback ?? null;
    // Optimistic — a thumbs click should feel instant; this is a low-stakes
    // signal, not worth blocking the UI on a round trip. Reverted on failure.
    setMessages((prev) => prev.map((m, i) => (i === index ? { ...m, feedback: next } : m)));
    setMessageFeedback(sessionId, target.id, next).catch(() => {
      setMessages((prev) => prev.map((m, i) => (i === index ? { ...m, feedback: previous } : m)));
      toast.error("Couldn't save feedback", "Please try again.");
    });
  };


  // Keeps the URL (?session=<id>) in step with the open conversation, so the
  // sidebar's Recent list can link to and highlight it.
  const adoptSession = (sid: string) => {
    setSessionId(sid);
    if (sessionIdRef.current !== sid) {
      sessionIdRef.current = sid;
      window.history.replaceState(null, "", `/?session=${sid}`);
    }
  };

  useEffect(() => {
    getConnections()
      .then(setConnections)
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (sessionParam && sessionParam !== sessionIdRef.current) {
      handleSelectSession(sessionParam);
    } else if (!sessionParam && sessionIdRef.current) {
      handleNewChat();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionParam]);

  const lastAssistant = [...messages].reverse().find((m) => m.role === "assistant");
  const activeSources = isStreaming ? draftSources : lastAssistant?.sources ?? [];
  const activeCitedIndices = isStreaming ? null : lastAssistant?.citedIndices ?? null;
  const isEmpty = messages.length === 0 && !isStreaming;
  const firstQuestion = messages.find((m) => m.role === "user")?.content ?? lastQuestion ?? "New conversation";
  const connected = connections.filter((c) => c.connected);

  if (isEmpty) {
    return (
      <HomeView
        firstName={user?.display_name?.split(" ")[0]}
        onSend={handleSend}
        disabled={isStreaming}
        error={error}
      />
    );
  }

  let userTurn = 0;

  return (
    <div className="flex h-screen min-w-0">
      <section className="flex min-w-0 flex-1 flex-col">
        <header className="flex flex-wrap items-center justify-between gap-3.5 px-6 pt-7 md:px-14">
          <div className="flex min-w-0 items-center gap-2.5 text-[13px] text-ink-500">
            <span>Chat</span>
            <span aria-hidden="true">/</span>
            <span className="truncate font-medium text-ink-100">{firstQuestion}</span>
          </div>
          {connected.length > 0 && (
            <div className="flex items-center gap-2" aria-label="Searching these sources">
              {connected.map((c) => (
                <span
                  key={c.provider}
                  className="flex h-[34px] items-center gap-2 rounded-full bg-ink-100 px-3.5 text-[13px] text-ink-950"
                >
                  <span className="h-1.5 w-1.5 rounded-full bg-accent" aria-hidden="true" />
                  {providerLabel(c.provider)}
                </span>
              ))}
            </div>
          )}
        </header>

        <div ref={scrollRef} className="flex-1 overflow-y-auto px-6 py-8 md:px-14">
          <div className="flex max-w-[760px] flex-col gap-[34px]">
            {messages.map((m, i) => {
              const isLast = i === messages.length - 1;
              if (m.role === "user") {
                userTurn += 1;
                return <Question key={i} text={m.content} first={userTurn === 1} />;
              }
              // While a Continue call streams, its text is appended onto the
              // truncated answer so it reads as one continuous reply.
              const liveAppend = continuing && isLast;
              const display = liveAppend ? { ...m, content: m.content + draftAnswer, truncated: false } : m;
              return (
                <Answer
                  key={i}
                  message={display}
                  onFeedback={(next) => handleFeedback(i, next)}
                  onContinue={isLast && !isStreaming && m.truncated ? handleContinue : undefined}
                  onRegenerate={isLast && !isStreaming ? handleRegenerate : undefined}
                  onCitationClick={handleCitationClick}
                />
              );
            })}
            {isStreaming && !continuing && (
              <Answer
                message={{ role: "assistant", content: draftAnswer, sources: draftSources }}
                pending
                onCitationClick={handleCitationClick}
              />
            )}
            {error && (
              <div className="rounded-xl border border-error_subtle bg-error-primary px-4 py-2.5 text-sm text-error-primary">
                {error}
              </div>
            )}
          </div>
        </div>

        <div className="w-full max-w-[868px] px-6 pb-8 md:px-14">
          <ChatInput disabled={isStreaming} onSend={handleSend} isStreaming={isStreaming} onStop={handleStop} />
        </div>
      </section>

      <SourcesPanel
        sources={activeSources}
        citedIndices={activeCitedIndices}
        isStreaming={isStreaming}
        highlightedIndex={highlightedSourceIndex}
      />
    </div>
  );
}

function Question({ text, first }: { text: string; first: boolean }) {
  return (
    <h1
      className={`font-serif font-normal tracking-[-0.015em] text-ink-100 ${
        first ? "text-[34px] leading-[1.12] md:text-[40px]" : "text-[28px] leading-[1.15] md:text-[30px]"
      }`}
    >
      {text}
    </h1>
  );
}

function Answer({
  message,
  pending,
  onFeedback,
  onContinue,
  onRegenerate,
  onCitationClick,
}: {
  message: ChatMessage;
  pending?: boolean;
  onFeedback?: (feedback: "up" | "down" | null) => void;
  onContinue?: () => void;
  onRegenerate?: () => void;
  onCitationClick?: (index: number) => void;
}) {
  const copy = useCopyToClipboard();
  const sources = message.sources ?? [];
  const cited = message.citedIndices;

  return (
    <div className="flex flex-col gap-[22px]">
      {sources.length > 0 && (
        <div className="flex flex-wrap items-center gap-2.5 font-mono text-xs text-ink-400">
          <span>searched your workspace</span>
          <span aria-hidden="true">→</span>
          <span>retrieved {sources.length}</span>
          {cited && (
            <>
              <span aria-hidden="true">→</span>
              <span className="font-medium text-ink-100">cited {cited.length}</span>
            </>
          )}
          {message.model && (
            <>
              <span aria-hidden="true">→</span>
              <span>
                {message.model}
                {message.elapsedMs ? ` ${(message.elapsedMs / 1000).toFixed(1)}s` : ""}
              </span>
            </>
          )}
        </div>
      )}

      <div className="font-serif text-[22px] leading-[1.5] text-ink-100 md:text-[24px]">
        {pending && !message.content ? (
          <span className="text-ink-500">Thinking…</span>
        ) : (
          <CitationText text={message.content} onCitationClick={onCitationClick} />
        )}
      </div>

      {message.truncated && (
        <div className="flex items-center gap-3">
          <p className="text-sm text-ink-500">This answer was cut short.</p>
          {onContinue && (
            <button
              type="button"
              onClick={onContinue}
              className="h-[34px] rounded-full border border-ink-700 px-3.5 text-[13px] text-ink-100 transition hover:border-ink-100"
            >
              Continue
            </button>
          )}
        </div>
      )}

      {!pending && (
        <div className="flex flex-wrap items-center gap-3">
          {sources.length > 0 && cited && (
            <span className="text-[13px] text-ink-400">
              {cited.length} of {sources.length} retrieved sources cited
            </span>
          )}
          <span className="flex-1" />
          <button
            type="button"
            aria-label="Copy answer"
            onClick={() => copy(message.content, "Answer copied")}
            className="flex h-[34px] w-[34px] items-center justify-center rounded-full text-ink-400 transition hover:bg-ink-800 hover:text-ink-100"
          >
            <Copy01 className="h-4 w-4" aria-hidden="true" />
          </button>
          {onRegenerate && (
            <button
              type="button"
              aria-label="Regenerate answer"
              onClick={onRegenerate}
              className="flex h-[34px] w-[34px] items-center justify-center rounded-full text-ink-400 transition hover:bg-ink-800 hover:text-ink-100"
            >
              <RefreshCw01 className="h-4 w-4" aria-hidden="true" />
            </button>
          )}
          {message.id && onFeedback && (
            <>
              <FeedbackPill
                label="Helpful"
                active={message.feedback === "up"}
                onClick={() => onFeedback(message.feedback === "up" ? null : "up")}
              />
              <FeedbackPill
                label="Not helpful"
                active={message.feedback === "down"}
                onClick={() => onFeedback(message.feedback === "down" ? null : "down")}
              />
            </>
          )}
        </div>
      )}
    </div>
  );
}

function FeedbackPill({ label, active, onClick }: { label: string; active: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={`h-[34px] rounded-full border px-3.5 text-[13px] transition ${
        active ? "border-ink-100 bg-ink-100 text-ink-950" : "border-ink-700 text-ink-100 hover:border-ink-100"
      }`}
    >
      {label}
    </button>
  );
}
