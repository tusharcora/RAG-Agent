import { API_BASE } from "./api";
import type { Source } from "./types";

interface StreamCallbacks {
  onSources: (sessionId: string, sources: Source[]) => void;
  onDelta: (text: string) => void;
  onDone: (sessionId: string, citedIndices: number[], answer: string) => void;
  onError: (message: string) => void;
}

/**
 * Hand-rolled SSE parsing over fetch()'s ReadableStream — EventSource can't
 * be used since it only supports GET with no request body, and /query needs
 * a POST body (the question).
 */
export async function streamQuery(question: string, sessionId: string | null, cb: StreamCallbacks): Promise<void> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };

  let res: Response;
  try {
    res = await fetch(`${API_BASE}/query`, {
      method: "POST",
      headers,
      body: JSON.stringify({ question, session_id: sessionId }),
      credentials: "include",
    });
  } catch (err) {
    cb.onError(err instanceof Error ? err.message : "Network error reaching the API");
    return;
  }

  if (!res.ok || !res.body) {
    cb.onError(`Request failed: ${res.status}`);
    return;
  }

  const reader = res.body.getReader();
  // One persistent decoder with {stream: true} across all chunks — a raw byte
  // chunk boundary can land mid multi-byte UTF-8 character (e.g. mid-emoji,
  // mid-accented character in a citation title). A fresh TextDecoder per
  // chunk would emit replacement characters at that boundary; {stream: true}
  // holds incomplete trailing bytes over to the next chunk instead.
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let boundary = buffer.indexOf("\n\n");
    while (boundary !== -1) {
      const rawEvent = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      dispatch(rawEvent, cb);
      boundary = buffer.indexOf("\n\n");
    }
  }
}

function dispatch(rawEvent: string, cb: StreamCallbacks): void {
  let eventName = "message";
  let dataLine = "";
  for (const line of rawEvent.split("\n")) {
    if (line.startsWith("event:")) eventName = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLine += line.slice(5).trim();
  }
  if (!dataLine) return;

  let data: any;
  try {
    data = JSON.parse(dataLine);
  } catch {
    return;
  }

  switch (eventName) {
    case "sources":
      cb.onSources(data.session_id, data.sources ?? []);
      break;
    case "delta":
      cb.onDelta(data.text ?? "");
      break;
    case "done":
      cb.onDone(data.session_id, data.cited_indices ?? [], data.answer ?? "");
      break;
    case "error":
      cb.onError(data.message ?? "Unknown streaming error");
      break;
  }
}
