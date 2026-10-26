# Spec — Drivers, Per-Platform Content, Content Engine

North star: **the customer's only job is "approve."** The system proposes content,
the human approves. This spec covers the three workstreams that get us there, in
build order: (1) finish the platform drivers, (2) make generation per-platform
(a partner's insight), (3) the Content Engine that proposes in batches.

---

## 0. Immediate — TikTok connect bug

Our OAuth config is correct (client_key/secret/base_url all set; authorize URL
returns 302 to TikTok's login, i.e. TikTok accepts it). The error page appears on
TikTok's side after login. Likely causes, in order:

1. Redirect URI in the TikTok dev console still points at the dead trycloudflare
   URL → update to `https://app.socialpilot.aiturn.io/oauth/callback/tiktok`.
2. App is in sandbox and the account isn't a Target User (`non_sandbox_target`).
3. Review state changed after the old-link submission was rejected.

Action: read the exact message on TikTok's error page → fix in the TikTok console.
No code change.

---

## 1. Platform drivers (build order)

Adapters implement the existing contract: `publish(publication, account)`,
`comments(...)`, `insights(...)`. Real drivers are additive over the mock; flip a
platform mock→real means walking account-resolution + per-platform media/format
requirements end-to-end.

Priority is set by the target ICP (established local/small businesses too busy
for social) and the "different audience per platform" point:

| # | Platform | API | Auth | Notes |
|---|---|---|---|---|
| 1 | **Facebook** | Graph API v26, Page publishing | **Separate Meta app** (Business type, "manage everything on your page", graph.facebook.com) | Meta allows Facebook Login **or** Instagram Login per app, not both — so Facebook Page publishing cannot share the Instagram-Login app. New app + credentials, but the OAuth pattern already exists. `pages_manage_posts` + `pages_read_engagement`, Page access token. |
| 2 | **Google Business Profile** | Business Profile API | Google OAuth | Local posts — the single highest-value channel for local businesses (maps/SEO). Need a verified listing to test. |
| 3 | **LinkedIn** | Share on LinkedIn (v2) + Sign in with LinkedIn | LinkedIn app | Company Page publishing; the B2B/services channel that matters. Text+image+video. |
| 4 | **X** | X API v2 | X app | Paid tier (Basic ~$100/mo) — lowest priority, revisit when revenue justifies. |
| 5 | **Pinterest** | API v5 | Pinterest app | Business account only; image pins. |
| 6 | **YouTube** | YouTube Data API v3 | Google OAuth (same project as GBP) | Video/Shorts upload; long processing times. |
| 7 | **Threads** | Threads API | Meta app | Requires its own App Review; low urgency, comes free with Meta later. |

Sequencing note: Facebook first (its own Meta Business app — the Instagram app can't
cover it), then Google Business Profile (biggest wedge for the ICP), then LinkedIn
(B2B). X/YouTube/Pinterest/Threads after revenue validates the need.

---

## 2. Per-platform content (the centerpiece)

**Problem (the partner's insight, correct):** one post must not go unchanged to every
network. TikTok, Instagram, LinkedIn, Facebook, Google Business reach different
audiences with different formats, tones, lengths, and hashtag norms. Cross-posting
identical content is a bug, not a feature.

**Current model (wrong):** `generate_proposals` produces one proposal with one
caption/hook/cta/media_prompt; validation creates one `Post`; publishing copies
the same `content` to every `Publication`.

**Target model:** the agent produces, for ONE idea, a per-platform variant.

### Data model

`Proposal.content` and `Post.content` become:

```json
{
  "concept": "summer sale announcement",
  "variants": {
    "tiktok": {
      "caption": "…", "hook": "…", "cta": "…", "hashtags": ["…"],
      "media_prompt": "…", "media_format": "9:16 video"
    },
    "instagram": {
      "caption": "…", "hook": "…", "cta": "…", "hashtags": ["…"],
      "media_prompt": "…", "media_format": "4:5 image / reel"
    },
    "linkedin": {
      "caption": "…", "hook": "…", "cta": "…", "hashtags": ["…"],
      "media_prompt": "…", "media_format": "carousel"
    }
  }
}
```

Key points:
- `concept` = the shared one-liner (one idea, adapted N ways).
- Each variant carries its own caption length/tone, hook, CTA, hashtag set,
  `media_prompt`, and `media_format` (aspect ratio / video vs image vs carousel).
- `Post.media_type` becomes per-variant (`media_format`), not per-post.
- `Publication.caption` and media come from `variants[platform]`, not the top level.

### Agent prompt change

`generate_proposals` gains a `platforms` list (the target networks). The system
prompt instructs the agent to emit the per-platform variant map, with explicit
per-platform rules (TikTok: short, hooky, Gen-Z, heavy hashtags, vertical video;
Instagram: polished visual, moderate hashtags, square/reel; LinkedIn: professional,
longer, few/no hashtags, carousel; Facebook: community tone; Google Business:
local, offers, short). `media_prompt` stays English and per-platform.

### Ripple effects (walk these end-to-end)

1. `_parse_proposals` → parse the variants map (tolerate the old flat shape during migration).
2. Proposal UI → render one proposal with per-platform tabs instead of a single caption.
3. `validate_proposal` → store variants.
4. `generate_media` → generate per-platform media (aspect ratio from `media_format`;
   vertical video for TikTok, square/reel for IG, carousel for LinkedIn).
5. `publish_publication` → pick `variants[platform]` for caption + media.
6. `scan_compliance` → scan per-variant text.
7. `_check_media_text` → per-variant on-image text.

### Reliability work (do alongside)

- Token refresh: long-lived IG tokens (~60d) and LinkedIn/Google refresh must be
  refreshed on a schedule, not only at connect time.
- Idempotency: every real adapter must honor `publication.idempotency_key` (Bluesky
  has no native key — guard by saved `remote_post_id`).
- Per-platform media requirements enforced (no text-only to TikTok/IG; image-only
  to X/Pinterest; aspect-ratio validation).
- Graceful per-publication failure (already in `publish_due` — keep it).

---

## 3. Content Engine (Phase A, after drivers + per-platform)

The agent proposes on its own, in batches, instead of waiting for a manual brief.

**Inputs:** brand kit + art-direction presets (already exist) + a new
**Content Direction** (a short, persistent description: what the business does,
its voice, this month's themes/goals) + a **cadence** (N posts/week + target
platforms).

**Output:** a review queue of ready proposals (each already per-platform), tagged
with suggested date/platform. The operator (or later, the client) approves/edits/
rejects; approved items flow into the existing pipeline automatically.

**New pieces:**
- `ContentDirection` (per brand): stored text + target platforms + cadence.
- A batch job / on-demand action that calls the generation agent with
  (direction + brand kit + presets) and yields a week's proposals.
- A "review queue" view (group proposals, approve/edit/reject in bulk).

This is the feature that removes the "write a brief every time" step and is the
real fix for "too manual."

---

## 4. Sequencing

1. Fix TikTok connect (console config — no code).
2. Facebook driver (reuse Meta plumbing), then Google Business Profile, then LinkedIn.
3. Per-platform content (section 2) — the architectural change, done before adding
   more platforms so every new driver benefits from it.
4. Reliability pass (token refresh, idempotency, format enforcement).
5. Content Engine (section 3).
6. Client approval layer (thin portal / email approve) — only after 3+5 are solid.

Non-negotiable throughout: don't add settings surfaces; remove steps. Every feature
must move the loop toward "agent proposes → human approves."
