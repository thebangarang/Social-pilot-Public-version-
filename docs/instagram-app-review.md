# Instagram App Review — Submission Plan

Goal: take the Meta app from Development mode to Live + Advanced Access so Social
Pilot can read/manage comments and publish for **client** Instagram business
accounts (not just our own test account).

## Current state (as of Sept 2026)

- Meta app registered, "Instagram API with Instagram Login" (graph.instagram.com).
- Scopes requested in code (`app/api/oauth.py` `INSTAGRAM_SCOPES`):
  `instagram_business_basic`, `instagram_business_content_publish`,
  `instagram_business_manage_comments`, `instagram_business_manage_insights`.
- OAuth flow + publish + comment read + comment reply WORK (verified live on the
  dummy business account). App is now **Live** (Phase 1 Go-live done).
- Media insights: after granting `instagram_business_manage_insights`, the
  metrics reach/saved/shares/views/likes/comments work. `impressions` is NOT
  supported for FEED media (API 400, and one bad metric fails the whole request).
  The media object's `like_count`/`comments_count` work with basic alone.
- Stable public URLs already live: privacy `https://socialpilot.aiturn.io/privacy.html`,
  terms `https://socialpilot.aiturn.io/terms.html`, app `https://app.socialpilot.aiturn.io`,
  redirect `https://app.socialpilot.aiturn.io/oauth/callback/instagram`.

## The two levels (important)

Meta splits Instagram Login into two access levels:

| Scenario | Access | Review |
|---|---|---|
| App serves accounts **you own/manage** (added as testers) | Standard | Not required |
| **Tech Provider** serving **multiple businesses** (clients) | Advanced | **Required** |

Social Pilot is the second row → Advanced Access → App Review required. But there
is a free intermediate win (Phase 1) that unblocks comment reads for our own
test account immediately.

---

## Phase 1 — Publish / Go live (unblocks our own account's comments, no review)

There is **no "Development → Live" toggle** for this app type. Apps created via
"Instagram API with Instagram Login" are *use-case* apps; Meta's docs state
business/use-case apps "do not have app modes" — they publish through a separate
flow. (The top "Development → Live" switch only exists on other app types.)

1. App Dashboard → **App settings → Basic** — the Publish page lists these as
   requirements; the "Publish" button stays blurred until every circle is filled:
   - App icon (1024×1024).
   - Privacy Policy URL → `https://socialpilot.aiturn.io/privacy.html`
   - Terms of Service URL → `https://socialpilot.aiturn.io/terms.html` (explicitly required to go live)
   - User Data Deletion URL → a page/section explaining how users delete their data
     (may point to the privacy policy's "Your rights" section)
   - App Category → "Business".
   - Business email → the email in Developer Settings.
2. App Dashboard → left panel **Publish** → **Go live** → click the **Go live**
   button in the **bottom-right corner** (unlocks once every requirement circle is filled).
3. Re-test: Community Manager should now read the dummy account's comments.

Note: Business Verification is NOT required to go live — only to access data you
don't own (client accounts / Advanced Access). Ignore it for the Publish button.

## Phase 2 — App Review (Advanced Access, for client accounts)

Exact click path (the flow changed — don't use the old "switch to live" for this):

1. App Dashboard → left menu **Products → Instagram → API setup with Instagram login**.
2. **Complete app review** section → click the chevron (top-right) → **Continue to app review**.
3. You land on **App Review → Requests** → click **Edit**.
4. Complete each action item (below).

### Action items

**A. Complete App Settings** — icon (1024²), Privacy Policy URL, App Category,
Business Email. Same URLs as Phase 1.

**B. Complete App Verification (Provide verification details)** — Platform = **Web**
(only Web/mobile-web supports Instagram Login). Provide step-by-step reviewer
instructions + test credentials. See prepared text below.

**C. Permission & feature requests** — for each of the 4 permissions: a usage
description + a screencast + "agree to allowed usage". See prepared text below.

**D. Confirm app loads externally** — the app is publicly reachable at
`https://app.socialpilot.aiturn.io` (behind a login), so reviewers need test
credentials (B).

### Before you submit

- **Create a reviewer test account** in Social Pilot (Settings → Users) — a
  regular operator (no publish permission needed) named e.g. `meta-reviewer`,
  and give its email + password to Meta in the Credentials field. The reviewer
  will log in with it.
- **Ensure the login/connect button is visible** and adheres to Instagram brand
  guidelines (the "Connect Instagram" button on the Settings page).
- **Switch the app UI to English** while recording the screencast (reviewers
  request English).

---

## Prepared submission text (copy/paste)

### Use-case description — instagram_business_basic

> Social Pilot is a self-hosted social media operating tool for businesses. We use
> instagram_business_basic to read the connected Instagram professional account's
> basic profile information (username, display name) so the account owner can see
> which of their accounts is connected and manage it from the app's brand settings.

### Use-case description — instagram_business_content_publish

> Social Pilot lets a business draft, review and publish posts to its own Instagram
> professional account. A team member composes a post, a manager approves it, and the
> app publishes the image/video with its caption to the business's own account via the
> Content Publishing API. Publishing is always human-approved — there is no automated
> or bulk posting.

### Use-case description — instagram_business_manage_comments

> Social Pilot's community management feature lets a business read and reply to
> comments on its own Instagram posts from the app. A community manager reviews
> incoming comments and writes replies; every reply is composed and sent by a human.
> The app never auto-replies and never interacts with accounts other than the
> authenticated business's own.

### Use-case description — instagram_business_manage_insights

> Social Pilot's performance tracking lets a business see how its own Instagram
> posts perform — impressions, reach, saves, likes and comments — in the app's
> Performance tab. The app reads these metrics for the authenticated business's
> own media only and displays them for the business's internal review. No data is
> shared, and no metrics are read from accounts other than the business's own.

### Platform Settings / reviewer step-by-step (Web)

> 1. Open https://app.socialpilot.aiturn.io and log in with the test credentials
>    provided below.
> 2. Go to Settings → Social accounts → click "Connect Instagram".
> 3. You are redirected to Instagram's authorization screen — grant the requested
>    permissions (basic profile, content publishing, comment management) for your
>    Instagram professional account.
> 4. Back in the app, the connected account appears under Social accounts.
> 5. To test publishing: open the Generate tab, write a short post, select "Image",
>    generate a proposal, validate it, then Publish — the post appears on your own
>    Instagram account.
> 6. To test comment management: open Community Manager, where comments on your own
>    Instagram posts are listed; you can write a reply to one.
>
> Note: the app is a whitelabel tool; the connect button and features above are
> reached through the account that is logged in.

### Screencast script (record one per permission, ~1-2 min each, English UI)

1. **basic** — log in → Settings → Social accounts → show the connected Instagram
   account (handle + display name) populated from the API.
2. **content_publish** — full flow: compose a post → generate/validate → click
   Publish → switch to Instagram and show the post live on the account.
3. **manage_comments** — Community Manager → show the list of comments pulled from
   an Instagram post → write a reply → show it appear under the post on Instagram.
4. **manage_insights** — Performance tab → show impressions/reach/saves/likes/
   comments for a published post, populated from the API.

Caption any non-obvious UI elements (buttons, status labels) per Meta's screencast
guidance.

---

## Verify scope naming (one-time check)

The code requests `instagram_business_content_publish`, but Meta's current docs
reference `instagram_business_content_publishing` (they are standardising scope
values to match permission names). Publish works today, so don't change blindly —
but in App Review → Requests, confirm which exact permission name appears and that
the code's `INSTAGRAM_SCOPES` matches it. If the dashboard shows the `…_publishing`
variant, update `app/api/oauth.py` before submitting, otherwise the review maps to
the wrong permission.

## Post-approval

- Approval is tied to app + scopes, not hosting — moving VPS/domain later only
  needs updating redirect URI + privacy URL (no re-review).
- Adding scopes (e.g. `instagram_business_manage_messages` for DMs later) triggers
  a new review — don't request it now.
