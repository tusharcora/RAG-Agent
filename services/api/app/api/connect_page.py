"""The page the browser lands on at the end of a desktop Connect / sign-in flow.

Self-contained HTML in the app's own style: the app's mark and the provider's
logo side by side, a serif headline, and a card for the account, workspace or
site. Fonts come from `app/static/connect/` (served at `assets/…` relative to
the finish URL), so the page needs no internet. Provider logos are the
official files dropped into the desktop UI's `public/brand/` (served at
`/brand/{provider}.svg|png`); when a file is missing the tile shows the
provider's name instead. No script runs on this page.
"""

import html
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from fastapi.responses import HTMLResponse

ASSETS_DIR = Path(__file__).resolve().parent.parent / "static" / "connect"
ASSET_TYPES = {".ttf": "font/ttf"}

PROVIDER_NAME = {"notion": "Notion", "jira": "Jira", "google": "Google", "github": "GitHub"}
LOGO_EXTS = ("svg", "png")

Kind = Literal["signed_in", "connected", "error"]


@dataclass
class Card:
    title: str
    subtitle: str | None = None


def asset_path(name: str) -> Path | None:
    """The bundled font file for `name`, or None (only known files, no paths)."""
    path = ASSETS_DIR / name
    if "/" in name or "\\" in name or path.suffix not in ASSET_TYPES or not path.is_file():
        return None
    return path


def _logo_url(provider: str) -> str | None:
    static = os.environ.get("DESKTOP_STATIC_DIR")
    if not static or provider not in PROVIDER_NAME:
        return None
    for ext in LOGO_EXTS:
        if (Path(static) / "brand" / f"{provider}.{ext}").is_file():
            return f"/brand/{provider}.{ext}"
    return None


def _e(text: str) -> str:
    return html.escape(text, quote=True)


_CHECK = (
    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" '
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>'
)
_CROSS = (
    '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" '
    'stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>'
)
_LOCK = (
    '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="4" y="11" width="16" height="10" '
    'rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/></svg>'
)

_CSS = """
@font-face{font-family:"Instrument Serif";src:url(assets/InstrumentSerif-Regular.ttf) format("truetype");font-display:swap}
@font-face{font-family:"Instrument Serif";font-style:italic;src:url(assets/InstrumentSerif-Italic.ttf) format("truetype");font-display:swap}
@font-face{font-family:"Geist Mono";src:url(assets/GeistMono-Regular.ttf) format("truetype");font-display:swap}
:root{--bg:#F5F5F2;--card:#FFFFFF;--line:#E3E2DD;--ink:#111113;--muted:#55555C;--faint:#6B6B72;--hl:228 255 92;--err:229 115 90;color-scheme:light dark}
@media (prefers-color-scheme:dark){:root{--bg:#262624;--card:#30302E;--line:#3D3D3A;--ink:#FAF9F5;--muted:#B0AEA5;--faint:#9C9A92}}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:flex;background:var(--bg);color:var(--ink);font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif;-webkit-font-smoothing:antialiased}
main{margin:auto;width:100%;max-width:560px;padding:56px 24px;display:flex;flex-direction:column;align-items:center;gap:28px;text-align:center}
.pair{display:flex;align-items:center;margin-bottom:6px}
.tile{width:84px;height:84px;border-radius:24px;background:var(--card);border:1px solid var(--line);display:grid;place-items:center;box-shadow:0 1px 0 rgb(0 0 0/.03),0 8px 24px -12px rgb(0 0 0/.18)}
.tile.app{background:var(--ink);border-color:var(--ink);color:var(--bg);font:400 40px/1 "Instrument Serif",ui-serif,Georgia,serif}
.tile img{width:44px;height:44px;object-fit:contain}
.tile .name{font:500 13px/1.2 system-ui,sans-serif;color:var(--ink)}
.link{position:relative;width:96px;height:2px;background:repeating-linear-gradient(90deg,var(--faint) 0 4px,transparent 4px 9px)}
.badge{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);width:30px;height:30px;border-radius:50%;display:grid;place-items:center;background:rgb(var(--hl));color:#111113;box-shadow:0 0 0 5px var(--bg)}
.badge.err{background:rgb(var(--err));color:#fff}
.eyebrow{display:flex;align-items:center;gap:10px;font:500 11.5px/1 "Geist Mono",ui-monospace,monospace;letter-spacing:.1em;text-transform:uppercase;color:var(--faint)}
.dot{width:7px;height:7px;border-radius:50%;background:rgb(var(--hl));box-shadow:0 0 0 3px rgb(var(--hl)/.25)}
.dot.err{background:rgb(var(--err));box-shadow:0 0 0 3px rgb(var(--err)/.22)}
h1{margin:0;font:400 64px/1.02 "Instrument Serif",ui-serif,Georgia,serif;letter-spacing:-.015em;overflow-wrap:anywhere}
h1 em{font-style:italic;color:#111113;padding:0 .12em;margin:0 -.04em;background:linear-gradient(transparent 12%,rgb(var(--hl)) 12%,rgb(var(--hl)) 88%,transparent 88%);-webkit-box-decoration-break:clone;box-decoration-break:clone}
.lead{margin:0;max-width:30rem;font-size:16px;color:var(--muted)}
.card{width:100%;display:flex;align-items:center;gap:14px;padding:16px 18px;text-align:left;background:var(--card);border:1px solid var(--line);border-radius:16px}
.avatar{flex:none;width:44px;height:44px;border-radius:50%;display:grid;place-items:center;background:var(--ink);color:var(--bg);font:400 22px/1 "Instrument Serif",ui-serif,Georgia,serif}
.who{min-width:0;flex:1;line-height:1.3}
.who b{display:block;font-size:15px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.who span{display:block;font-size:13.5px;color:var(--faint);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.tag{flex:none;font:500 10.5px/1 "Geist Mono",ui-monospace,monospace;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);border:1px solid var(--line);border-radius:999px;padding:6px 10px}
.msg{width:100%;padding:14px 16px;text-align:left;font-size:14.5px;border-radius:14px;border:1px solid rgb(var(--err)/.35);background:rgb(var(--err)/.08)}
.btn{display:inline-flex;align-items:center;height:44px;padding:0 22px;border-radius:999px;background:var(--ink);color:var(--bg);font-size:14.5px;font-weight:500;text-decoration:none}
.btn:focus-visible{outline:2px solid rgb(var(--hl));outline-offset:3px}
.foot{width:100%;display:flex;align-items:center;justify-content:center;gap:9px;padding-top:22px;border-top:1px solid var(--line);font-size:13px;color:var(--faint)}
@media (max-width:520px){h1{font-size:46px}main{padding:40px 16px}.link{width:56px}}
"""


def _pair(provider: str | None, ok: bool) -> str:
    if provider not in PROVIDER_NAME:
        return '<div class="pair"><div class="tile app" aria-hidden="true">K</div></div>'
    name = PROVIDER_NAME[provider]
    logo = _logo_url(provider)
    inner = f'<img src="{_e(logo)}" alt="{_e(name)}">' if logo else f'<span class="name">{_e(name)}</span>'
    badge = f'<span class="badge">{_CHECK}</span>' if ok else f'<span class="badge err">{_CROSS}</span>'
    return (
        '<div class="pair"><div class="tile app" aria-hidden="true">K</div>'
        f'<div class="link">{badge}</div><div class="tile">{inner}</div></div>'
    )


def _card(card: Card, provider: str) -> str:
    initial = (card.title.strip()[:1] or "?").upper()
    sub = f"<span>{_e(card.subtitle)}</span>" if card.subtitle else ""
    tag = f'<span class="tag">{_e(PROVIDER_NAME.get(provider, provider))}</span>'
    return (
        f'<div class="card"><span class="avatar" aria-hidden="true">{_e(initial)}</span>'
        f'<div class="who"><b>{_e(card.title)}</b>{sub}</div>{tag}</div>'
    )


def render(
    kind: Kind,
    provider: str | None,
    *,
    title: str,
    card: Card | None = None,
    message: str | None = None,
) -> HTMLResponse:
    """`title` is the plain summary ("Signed in as Ada", "Connected to Notion",
    or the error) used for the tab title; the body is built per `kind`."""
    name = PROVIDER_NAME.get(provider or "", "")
    if kind == "signed_in" and card:
        first = card.title.split()[0] if card.title.split() else card.title
        eyebrow = f'<span class="dot"></span>Signed in with {_e(name)}'
        headline = f"Welcome, <em>{_e(first)}</em>."
        lead = (
            f"Your {_e(name)} account is linked to Knowledge Agent. "
            "The app has already updated, so you can close this tab."
        )
        body = f'<p class="lead">{lead}</p>{_card(card, provider or "")}'
    elif kind == "connected":
        eyebrow = '<span class="dot"></span>Connected'
        headline = f"{_e(name)} is <em>connected</em>."
        lead = (
            "Your first sync has started in the app. New content will show up in the knowledge base "
            "within a few minutes. You can close this tab."
        )
        body = f'<p class="lead">{lead}</p>' + (_card(card, provider or "") if card else "")
    else:
        eyebrow = '<span class="dot err"></span>Not connected'
        headline = "That didn&#x27;t go through."
        body = (
            f'<div class="msg" role="alert">{_e(message or title)}</div>'
            '<p class="lead">Nothing was changed. Start again from the app whenever you&#x27;re ready.</p>'
        )

    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_e(title)} · Knowledge Agent</title>
<style>{_CSS}</style></head>
<body><main>
{_pair(provider, kind != "error")}
<div class="eyebrow">{eyebrow}</div>
<h1>{headline}</h1>
{body}
<a class="btn" href="/">Back to Knowledge Agent →</a>
<div class="foot">{_LOCK}Your documents and chats stay on this computer.</div>
</main></body></html>"""
    return HTMLResponse(
        page,
        status_code=200,
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": (
                "default-src 'none'; style-src 'unsafe-inline'; font-src 'self'; img-src 'self'; "
                "frame-ancestors 'none'; base-uri 'none'"
            ),
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
        },
    )
