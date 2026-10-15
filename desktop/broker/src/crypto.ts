// PKCE-style challenge check and AES-GCM sealing of results held in KV.

const enc = new TextEncoder();
const dec = new TextDecoder();

export function b64urlEncode(bytes: Uint8Array): string {
  let s = "";
  for (let i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function b64urlDecode(s: string): Uint8Array {
  const b64 = s.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((s.length + 3) % 4);
  return b64Decode(b64);
}

function b64Decode(b64: string): Uint8Array {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

/** base64url(SHA-256(verifier)), no padding — 43 characters. */
export async function challengeOf(verifier: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", enc.encode(verifier));
  return b64urlEncode(new Uint8Array(digest));
}

/** Length-independent-timing string comparison (for comparing challenges). */
export function constantTimeEqual(a: string, b: string): boolean {
  const x = enc.encode(a);
  const y = enc.encode(b);
  let diff = x.length ^ y.length;
  const n = Math.max(x.length, y.length);
  for (let i = 0; i < n; i++) diff |= (x[i] ?? 0) ^ (y[i] ?? 0);
  return diff === 0;
}

async function importKey(keyB64: string): Promise<CryptoKey> {
  let raw: Uint8Array;
  try {
    raw = b64Decode(keyB64.trim());
  } catch {
    throw new Error("RESULT_KEY is not valid base64");
  }
  if (raw.length !== 32) throw new Error("RESULT_KEY must decode to 32 bytes");
  return crypto.subtle.importKey("raw", raw, { name: "AES-GCM" }, false, ["encrypt", "decrypt"]);
}

/**
 * AES-256-GCM encrypt `obj` as JSON. Output: base64url(iv[12] || ciphertext+tag).
 * `aad` (e.g. the state id) binds the ciphertext to its KV entry so it can't be replayed under another key.
 */
export async function seal(obj: unknown, keyB64: string, aad = ""): Promise<string> {
  const key = await importKey(keyB64);
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const ct = new Uint8Array(
    await crypto.subtle.encrypt(
      { name: "AES-GCM", iv, additionalData: enc.encode(aad) },
      key,
      enc.encode(JSON.stringify(obj)),
    ),
  );
  const out = new Uint8Array(iv.length + ct.length);
  out.set(iv, 0);
  out.set(ct, iv.length);
  return b64urlEncode(out);
}

/** Inverse of `seal`. Throws if the key, AAD or ciphertext don't match. */
export async function open(sealed: string, keyB64: string, aad = ""): Promise<unknown> {
  const key = await importKey(keyB64);
  const buf = b64urlDecode(sealed);
  if (buf.length < 12 + 16) throw new Error("sealed value too short");
  const pt = await crypto.subtle.decrypt(
    { name: "AES-GCM", iv: buf.slice(0, 12), additionalData: enc.encode(aad) },
    key,
    buf.slice(12),
  );
  return JSON.parse(dec.decode(pt));
}
