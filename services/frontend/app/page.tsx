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

  if (loading) {
    return (
      <div className="flex h-[calc(100vh-57px)] items-center justify-center">
        <span className="h-6 w-6 animate-spin rounded-full border-2 border-ink-700 border-t-coral-500" />
      </div>
    );
  }

  if (!user) return <LandingPage />;

  return <ChatPage />;
}

function ChatPage() {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [draftAnswer, setDraftAnswer] = useState("");
  const [draftSources, setDraftSources] = useState<Source[]>([]);
  const [error, setError] = useState<string | null>(null);
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
      onDone: (sid, citedIndices, answer) => {
        setSessionId(sid);
        setMessages((prev) => [...prev, { role: "assistant", content: answer, sources: sourcesForThisTurn, citedIndices }]);
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

  return (
    <div className="flex h-[calc(100vh-57px)]">
      <SessionSidebar activeSessionId={sessionId} onSelect={handleSelectSession} onNewChat={handleNewChat} />

      <div className="flex min-w-0 flex-1 flex-col">
        <div ref={scrollRef} className="flex-1 overflow-y-auto px-6 py-6">
          {messages.length === 0 && !isStreaming && (
            <div className="mx-auto mt-16 max-w-md text-center text-sm text-ink-500">
              <p className="mb-1 text-base font-medium text-ink-200">Ask something about your synced content</p>
              <p>Answers are grounded in what's been ingested from Notion and Jira, with citations you can verify.</p>
            </div>
          )}

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
              <div className="rounded-lg border border-coral-800/60 bg-coral-950/40 px-3 py-2 text-sm text-coral-300">
                {error}
              </div>
            )}
          </div>
        </div>

        <div className="mx-auto w-full max-w-3xl">
          <ChatInput disabled={isStreaming} onSend={handleSend} />
        </div>
      </div>

      <SourcesPanel sources={activeSources} citedIndices={activeCitedIndices} isStreaming={isStreaming} />
    </div>
  );
}

function MessageBubble({ message, pending }: { message: ChatMessage; pending?: boolean }) {
  const isUser = message.role === "user";
  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[85%] rounded-2xl px-4 py-2.5 text-sm leading-relaxed ${
          isUser ? "bg-coral-500 text-white" : "border border-ink-800 bg-ink-900/80 text-ink-100"
        }`}
      >
        {pending ? (
          <span className="text-ink-500">Thinking…</span>
        ) : isUser ? (
          message.content
        ) : (
          <CitationText text={message.content} />
        )}
      </div>
    </div>
  );
}
