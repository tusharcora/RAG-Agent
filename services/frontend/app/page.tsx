"use client";

import { useRef, useState } from "react";
import { getSession } from "@/lib/api";
import { streamQuery } from "@/lib/sse";
import { useAuth } from "@/lib/auth-context";
import type { ChatMessage, Source } from "@/lib/types";
import { SessionSidebar } from "@/components/chat/SessionSidebar";
import { SourcesPanel } from "@/components/chat/SourcesPanel";
import { ChatInput } from "@/components/chat/ChatInput";
import { CitationText } from "@/components/chat/CitationText";
import { LandingPage } from "@/components/landing/LandingPage";

export default function HomePage() {
  const { user, loading } = useAuth();

  // Wrapped in .cyber-full so the chat homepage (both its logged-out
  // landing page and its logged-in chat UI) is the one place cyberpunk
  // theme runs full animated glitch/motion effects — every other screen
  // gets the calmer, static-only version of the same look.
  return (
    <div className="cyber-full">
      {loading ? (
        <div className="flex h-[calc(100vh-57px)] items-center justify-center">
          <span className="h-6 w-6 animate-spin rounded-full border-2 border-ink-700 border-t-coral-500" />
        </div>
      ) : !user ? (
        <LandingPage />
      ) : (
        <ChatPage />
      )}
    </div>
  );
}

const SUGGESTIONS = [
  "What did we ship in the last sprint?",
  "Summarize our current product roadmap",
  "Are there any open blockers right now?",
  "What's documented about our onboarding flow?",
];

function greeting(): string {
  const hour = new Date().getHours();
  if (hour < 12) return "Good morning";
  if (hour < 18) return "Good afternoon";
  return "Good evening";
}

function ChatPage() {
  const { user } = useAuth();
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [draftAnswer, setDraftAnswer] = useState("");
  const [draftSources, setDraftSources] = useState<Source[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [sessionRefreshSignal, setSessionRefreshSignal] = useState(0);
  const scrollRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = () => {
    requestAnimationFrame(() => scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight }));
  };

  const handleSend = (question: string) => {
    setError(null);
    setMessages((prev) => [...prev, { role: "user", content: question }]);
    setIsStreaming(true);
    setDraftAnswer("");
    setDraftSources([]);
    scrollToBottom();
    // The backend records this turn (and indexes the session) as soon as the
    // question is sent, not once the answer finishes — bumping this right
    // away lets the sidebar pick up a brand-new conversation immediately
    // instead of waiting for its 15s poll.
    setSessionRefreshSignal((n) => n + 1);

    // Scoped to this call, not React state — SSE events for this call arrive
    // in order (sources before done), so this is always fresh when onDone reads it.
    let sourcesForThisTurn: Source[] = [];

    streamQuery(question, sessionId, {
      onSources: (sid, sources) => {
        setSessionId(sid);
        setDraftSources(sources);
        sourcesForThisTurn = sources;
        scrollToBottom();
      },
      onDelta: (text) => {
        setDraftAnswer((prev) => prev + text);
        scrollToBottom();
      },
      onDone: (sid, citedIndices, answer, truncated) => {
        setSessionId(sid);
        setMessages((prev) => [
          ...prev,
          { role: "assistant", content: answer, sources: sourcesForThisTurn, citedIndices, truncated },
        ]);
        setIsStreaming(false);
        setDraftAnswer("");
        setDraftSources([]);
        scrollToBottom();
      },
      onError: (message) => {
        setError(message);
        setIsStreaming(false);
        setDraftAnswer("");
        scrollToBottom();
      },
    });
  };

  const handleNewChat = () => {
    setSessionId(null);
    setMessages([]);
    setDraftAnswer("");
    setDraftSources([]);
    setError(null);
  };

  const handleSelectSession = async (id: string) => {
    try {
      const detail = await getSession(id);
      setSessionId(detail.session_id);
      setMessages(detail.history.map((h) => ({ role: h.role as "user" | "assistant", content: h.content })));
      setDraftAnswer("");
      setDraftSources([]);
      setError(null);
      scrollToBottom();
    } catch {
      setError("Couldn't load that conversation — it may have expired.");
    }
  };

  const lastAssistant = [...messages].reverse().find((m) => m.role === "assistant");
  const activeSources = isStreaming ? draftSources : lastAssistant?.sources ?? [];
  const activeCitedIndices = isStreaming ? null : lastAssistant?.citedIndices ?? null;
  const isEmpty = messages.length === 0 && !isStreaming;

  return (
    <div className="flex h-[calc(100vh-57px)]">
      <SessionSidebar
        activeSessionId={sessionId}
        onSelect={handleSelectSession}
        onNewChat={handleNewChat}
        refreshSignal={sessionRefreshSignal}
      />

      {isEmpty ? (
        <div className="flex min-w-0 flex-1 flex-col items-center justify-center px-6">
          <div className="w-full max-w-2xl">
            <div className="mb-8 text-center">
              <h1 className="cyber-glitch-text cyber-glitch-anim text-3xl font-semibold text-ink-50">
                {greeting()}
                {user?.display_name ? `, ${user.display_name.split(" ")[0]}` : ""}
              </h1>
              <p className="mt-2 text-ink-500">Ask anything about your synced Notion and Jira content.</p>
            </div>

            <ChatInput disabled={isStreaming} onSend={handleSend} />

            <div className="mt-4 flex flex-wrap justify-center gap-2">
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => handleSend(s)}
                  className="cyber-chamfer-sm rounded-full border border-ink-800 bg-ink-900/60 px-3.5 py-2 text-sm text-ink-300 transition hover:border-ink-700 hover:bg-ink-800/60 hover:text-ink-100"
                >
                  {s}
                </button>
              ))}
            </div>

            {error && (
              <div className="mt-4 cyber-chamfer-sm rounded-lg border border-coral-800/60 bg-coral-950/40 px-3 py-2 text-sm text-coral-300">
                {error}
              </div>
            )}
          </div>
        </div>
      ) : (
        <div className="flex min-w-0 flex-1 flex-col">
          <div ref={scrollRef} className="flex-1 overflow-y-auto px-6 py-6">
            <div className="mx-auto max-w-3xl space-y-4">
              {messages.map((m, i) => (
                <MessageBubble key={i} message={m} />
              ))}
              {isStreaming && (
                <MessageBubble
                  message={{ role: "assistant", content: draftAnswer || "…" }}
                  pending={draftAnswer.length === 0}
                />
              )}
              {error && (
                <div className="cyber-chamfer-sm rounded-lg border border-coral-800/60 bg-coral-950/40 px-3 py-2 text-sm text-coral-300">
                  {error}
                </div>
              )}
            </div>
          </div>

          <div className="mx-auto w-full max-w-3xl px-6 pb-6">
            <ChatInput disabled={isStreaming} onSend={handleSend} />
          </div>
        </div>
      )}

      <SourcesPanel sources={activeSources} citedIndices={activeCitedIndices} isStreaming={isStreaming} />
    </div>
  );
}

function MessageBubble({ message, pending }: { message: ChatMessage; pending?: boolean }) {
  const isUser = message.role === "user";
  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`cyber-chamfer-sm max-w-[85%] rounded-2xl px-4 py-2.5 text-sm leading-relaxed ${
          isUser ? "bg-coral-500 text-white" : "border border-ink-800 bg-ink-900/80 text-ink-100"
        }`}
      >
        {pending ? (
          <span className="text-ink-500">Thinking…</span>
        ) : isUser ? (
          message.content
        ) : (
          <>
            <CitationText text={message.content} />
            {message.truncated && (
              <p className="mt-1.5 text-xs text-ink-500">Response was cut short — ask to continue for more.</p>
            )}
          </>
        )}
      </div>
    </div>
  );
}
