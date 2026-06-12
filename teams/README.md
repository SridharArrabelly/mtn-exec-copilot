# Avatar Forge — Microsoft Teams app (tab + conversational bot)

This folder packages the Avatar Forge web app as a **Microsoft Teams app** with two
surfaces in **one package**: a personal **tab** that embeds the web UI (Phase 1,
scope 1A) and an installable, @mentionable **conversational bot** (Phase 2a, issue
#53). Both are additive and sideloadable with **no Teams-admin access required**.

- **Phase 1 — personal tab** (below): an anonymous, sideloaded prototype that embeds
  the existing web UI (mic + WebRTC avatar). No SSO, no org publishing.
- **Phase 2a — conversational bot** ([jump down](#phase-2a--conversational-bot-issue-53)):
  a bot that answers via the same Foundry agent and deep-links back into the tab.
- **Phase 2b — in-call audio participant** ([jump down](#phase-2b--in-call-audio-participant-issue-27)):
  the avatar joins the **call** as an audio participant and answers spoken questions aloud
  (opt-in via ACS; off by default).

Start with the tab walkthrough, then the bot section if you're enabling it.

- Personal-scope **static tab** that embeds the existing web UI (mic + WebRTC avatar).
- **No SSO** and **no org/admin publishing** — those are a later phase you'll drive
  from the Teams admin center once the prototype works.
- Additive only: a templated manifest, two icons, a build script, a no-op-unless-in-Teams
  frontend init (`frontend/teams.js`), and one `frame-ancestors` CSP header in the backend.

> The same package built here is reused unchanged when you later publish it through the
> admin center — so the manifest stays templated and the zip stays valid (manifest + 2
> icons at the archive root).

## Contents

| File | Purpose |
| --- | --- |
| `manifest.template.json` | Teams manifest (schema v1.17) with `{{HOSTNAME}}`, `{{VERSION}}`, `{{APP_ID}}` placeholders. |
| `icons/color.png` | 192×192 color app icon. |
| `icons/outline.png` | 32×32 transparent outline icon (Teams recolors it). |
| `build_package.py` | Stdlib-only script that renders the manifest and zips a sideloadable package. |
| `build/avatar-forge-teams.zip` | Build output (git-ignored). |

## Build the package

You need the **live HTTPS hostname** of the deployed app — the bare host of your
Azure Container App, e.g. `avatar-forge.<region>.azurecontainerapps.io` (no
`https://`, no path, no port).

```bash
uv run python teams/build_package.py --hostname <your-app>.azurecontainerapps.io
```

For the **current deployment** the bare host is
`ca-mtn-agent-forge-hz3cp52lid6xq.whitedune-5a2336c6.swedencentral.azurecontainerapps.io`,
so the exact rebuild command is:

```bash
uv run python teams/build_package.py \
  --hostname ca-mtn-agent-forge-hz3cp52lid6xq.whitedune-5a2336c6.swedencentral.azurecontainerapps.io
```

> **Rebuild after a redeploy:** the hostname only changes if the Container App is
> recreated (a fresh `azd up` into a new environment). A normal `azd deploy` /
> image push keeps the same host, so the existing `avatar-forge-teams.zip` stays
> valid and you do **not** need to rebuild or re-sideload. Rebuild only when the
> host changes — then re-run the command above and re-upload the new zip.

Optional flags (env var equivalents in parentheses):

- `--version` (`TEAMS_APP_VERSION`) — manifest version, default `1.0.0`.
- `--app-id` (`TEAMS_APP_ID`) — stable app GUID. If omitted, a deterministic GUID is
  derived from the hostname so rebuilds produce the same id.

Output: `teams/build/avatar-forge-teams.zip` containing `manifest.json`,
`color.png`, and `outline.png` at the archive root.

## Run it in Teams (no admin access needed)

You do **not** need to publish to an org catalog. Two sideload routes — try A first; if
your tenant has custom-app upload disabled, use B.

### Route A — Upload a custom app (personal scope)

1. In Teams, go to **Apps → Manage your apps → Upload an app → Upload a custom app**.
2. Select `teams/build/avatar-forge-teams.zip`.
3. Add the app; open the **Avatar** personal tab.
4. When prompted, **allow microphone** (and camera if requested) for the tab.

> ⚠️ Even this basic, personal sideload can be blocked: the **"Upload a custom app"**
> option only appears if the tenant policy *Allow uploading custom apps* is enabled. If you
> don't see it, you're not doing anything wrong — use Route B instead.

### Route B — Teams Developer Portal "Preview in Teams" (admin-free fallback)

The Developer Portal lets you preview a sideloaded app without the upload-custom-app
policy, and is the recommended no-admin path for this prototype.

1. Open the **Teams Developer Portal** — <https://dev.teams.microsoft.com> (also available
   as the **Developer Portal** app inside Teams).
2. **Apps → Import app** and select `teams/build/avatar-forge-teams.zip`.
3. Open the imported app and click **Preview in Teams** (top right). Teams opens and adds
   the app for you.
4. Open the **Avatar** personal tab and **allow microphone** (and camera if requested).

If neither route is available, the last admin-free option is to **add the app to a team
you own** (some tenants allow app uploads scoped to a team even when personal upload is
off) — but for this prototype Route B is the simplest.

## Validation checklist (run in Teams **web** AND **desktop**)

- [ ] The tab loads the avatar UI over HTTPS (no blank frame / framing error).
- [ ] Microphone permission prompt appears and, once granted, `getUserMedia` succeeds.
- [ ] The WebRTC avatar **video** renders and the avatar **speaks** (audio out).
- [ ] Talking to the avatar works end-to-end (the WSS voice socket connects).
- [ ] Switching the Teams theme (light ↔ dark) updates the app theme live.
- [ ] The standalone app (`uv run avatar-forge`, port 3000) is unchanged — the Teams
      SDK is never loaded outside Teams.

If the avatar video or mic fails inside Teams, that is the gating risk for 1A — capture
the client (web/desktop), the console errors, and the permission state, and report back
before proceeding.

## How the in-Teams detection works

`frontend/teams.js` activates only when the page is inside Teams — detected via the
`?inTeams=1` query param that the manifest's `contentUrl` carries, with a framed-window
fallback. Outside Teams it returns immediately and the Teams JS SDK is never fetched, so
the standalone experience is byte-for-byte the same. Inside Teams it initializes the SDK
and mirrors the host theme into the app's existing `applyTheme()` hook.

## Embedding header

The backend sends a single response header so the Teams clients can frame the app:

```
Content-Security-Policy: frame-ancestors 'self' \
  https://teams.microsoft.com https://*.teams.microsoft.com \
  https://teams.live.com https://*.teams.live.com \
  https://*.skype.com
```

Only `frame-ancestors` is set on purpose — a full CSP (`script-src`/`connect-src`/
`media-src`) would break inline JS, the WSS voice socket, and WebRTC.

## Deferred to a later phase (not in this prototype)

- Publishing through the **Teams admin center** (org catalog / targeted release / admin
  approval) — you'll do this from the admin portal once the sideloaded prototype works.
  The package built here is reused unchanged for that step.
- Real privacy-policy and terms-of-use pages (the manifest currently points at repo pages,
  which is fine for sideload).

---

# Phase 2a — Conversational bot (issue #53)

Phase 2a adds an **installable, @mentionable bot** to the **same Teams app package** (the
manifest now carries both a `staticTabs` entry **and** a `bots` entry — one app, two
surfaces). The bot answers questions using the **same Foundry agent** the voice avatar
uses (Azure AI Search RAG + Bing grounding), returns answers as Adaptive Cards with
sources, and can deep-link back into the Phase 1 tab for the live avatar.

It is **additive**: the Phase 1 tab and the standalone web app are unchanged, and no Node
toolchain is introduced. The bot is hosted **inside the existing FastAPI app** (new
`POST /api/messages` route) using the **Microsoft 365 Agents SDK** (`microsoft-agents-*`,
FastAPI adapter), so it ships in the same Container App — the messaging endpoint is just
the existing ACA HTTPS URL + `/api/messages`.

## What changed

| Area | Change |
| --- | --- |
| `teams/manifest.template.json` | Added a `bots` entry (`personal` + `team` + `groupchat` scopes), a `commandLists`, a `{{BOT_ID}}` placeholder, and `token.botframework.com` to `validDomains`. The static tab is untouched. |
| `teams/build_package.py` | Optional `--bot-id` / `TEAMS_BOT_ID` input fills `{{BOT_ID}}`. **When omitted, the build is tab-only** (the `bots` entry is dropped) so the Phase 1 Tab package always builds. The zip stays flat (manifest + 2 icons). |
| `backend/bot/` | The bot: SDK app + `/api/messages` route (`app.py`), Foundry-agent bridge (`agent_runtime.py`), Adaptive Card + deep link (`cards.py`). |
| `backend/main.py` | Mounts the bot router before the static SPA; closes the agent client on shutdown. |
| `infra/` | New `modules/botService.bicep` (Azure Bot + Teams channel), conditional on a bot app id; container env + secret wiring. |

## Identity model (read this first)

The bot needs a **bot identity** = an **Entra app registration** (client id + secret),
registered as an **Azure Bot** resource with the **Teams channel** enabled. This is
**separate** from the backend's managed identity (which still reaches Foundry/Search) and
**separate** from user SSO (deferred — see below).

- **Azure Bot + Teams channel + container wiring** are created by `infra/` when you supply
  the bot app id/secret — **no Teams admin access required** (these are *Azure* RBAC
  actions in your subscription).
- **User SSO is deferred** (Phase 2a ships with bot-framework identity only). The bot does
  not yet exchange a user token, so it does not need `webApplicationInfo` in the manifest
  for the MVP. Adding SSO later requires an exposed API scope + `webApplicationInfo` +
  token-exchange handling.

## Steps you must do yourself (portal / CLI)

These cannot be done from this repo because they create an **app registration** (an
identity object), which lives outside the resource-group deployment:

1. **Create the bot's Entra app registration** (single-tenant is simplest):
   ```bash
   az ad app create --display-name "Avatar Forge Bot" --sign-in-audience AzureADMyOrg
   # note the appId (this is your BOT app id), then add a client secret:
   az ad app credential reset --id <bot-app-id> --append
   # note the returned password (client secret)
   ```
2. **Give azd the bot values** (the infra wires the Azure Bot + container env from these):
   ```bash
   azd env set BOT_APP_ID <bot-app-id>
   azd env set BOT_APP_PASSWORD <bot-client-secret>   # stored as an ACA secret
   azd env set TEAMS_APP_ID <teams-app-id>            # same id you build the package with
   ```
   > These map to the `botAppId` / `botAppPassword` / `teamsAppId` Bicep params. If
   > `BOT_APP_ID` is unset, the bot infra is skipped entirely and the deploy behaves
   > exactly as Phase 1.
3. **Provision + deploy**: `azd up` (or `azd provision` then `azd deploy`). This creates
   the Azure Bot, enables the Teams channel, and sets the messaging endpoint to
   `https://<aca-host>/api/messages`. The endpoint is also emitted as the
   `BOT_MESSAGING_ENDPOINT` output.

## Build the package (with the bot id)

The bot is **additive and opt-in**: omit `--bot-id` to build the tab-only Phase 1 package
(the `bots` entry is dropped). To include the bot, pass `--bot-id` (the bot app id from step 1):

```bash
uv run python teams/build_package.py \
  --hostname <your-app>.azurecontainerapps.io \
  --bot-id <bot-app-id>
```

`--app-id` / `TEAMS_APP_ID` behaves as before (deterministic from the hostname if
omitted). Output is the same flat `teams/build/avatar-forge-teams.zip`. **Sideload it
exactly as in Phase 1** (Route A or Route B above) — the same package now installs both
the tab and the bot.

## Validate the bot (web AND desktop)

- [ ] **Personal chat:** open the bot, send a question, get an answer **with a Sources
      list** and an **"Open the live avatar"** button that launches the tab.
- [ ] **Group chat:** add the app, **@mention** the bot, get an answer (bots only see
      messages they're @mentioned in, in group/meeting chat).
- [ ] **Meeting chat:** add the app to a meeting, **@mention** the bot in the meeting chat,
      get an answer (chat-only — no in-call media yet; that's Phase 2b / #27).
- [ ] **Parity:** the same question asked to the bot and to the voice avatar returns
      consistent answers + citations (both go through the same Foundry agent).
- [ ] **No regressions:** the Phase 1 tab still loads and the standalone web app
      (`uv run avatar-forge`) is unchanged.

> The bot endpoint also answers `GET /api/messages` with `{"status":"ok"}` for a quick
> liveness check once deployed.

## Known gating risks

- **Turn latency:** a grounded answer can take several seconds (AI Search + Bing). To stay
  within the Teams ~15s activity-response window, the bot **acknowledges immediately** (typing
  indicator) and runs the Foundry agent in the **background**, then posts the answer (Adaptive
  Card with sources) as a **proactive message** to the same conversation. `BOT_RUN_TIMEOUT_S`
  (default 60s) caps the background run; on timeout the bot posts a brief "took too long" reply.
  This requires the bot's app id (`CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID`) to be
  set so proactive `continue_conversation` can authenticate.
- **Conversational memory:** the MVP treats each turn statelessly. Multi-turn memory
  (threading via the Responses API) is wired but off by default to avoid cross-user
  context bleed in group/meeting chats.
- **SSO:** deferred — the bot does not yet know *who* asked. Add `webApplicationInfo` +
  token exchange in a follow-up if per-user identity is required.

---

# Phase 2b — In-call audio participant (issue #27)

Phase 2a lets people **@mention the avatar in the meeting chat** (text). Phase 2b adds
the missing piece: the avatar joins the **call itself** as an **audio participant**, so
anyone in the meeting can **say a wake phrase and ask a question aloud** and hear the
answer spoken back — grounded in the same Foundry agent (AI Search + Bing) used
everywhere else. This is the fix for the Phase 2a symptom where pulling the bot into the
call made it *"come and leave"* (the chat bot has no real-time media to sustain a call).

It is **audio-only** by design (no avatar face in the call roster — that animated face is
a separate, optional Companion surface). It is **non-recording**: the avatar listens live
only to answer when addressed; it does **not** record or transcribe the meeting.

## How it works (and why there's a browser page)

ACS Call Automation has **no "join a Teams meeting by URL" API**. So the join happens in
**two steps**:

1. **Browser joins the meeting** — `/acs-join.html` loads the **ACS Calling Web SDK**
   (from a CDN — no Node toolchain is added to the server) and joins the meeting as an
   **anonymous interop guest**. Anonymous join is governed by the **meeting lobby**, so it
   needs **no Teams-admin consent**. This yields a `ServerCallId`.
2. **Server attaches the voice bridge** — the page posts the `ServerCallId` to
   `POST /api/acs/call`; the FastAPI server calls ACS `connect_call(...)` with
   **bidirectional audio media streaming** (MIXED — the whole-room mix) over a WebSocket
   the server hosts (`/ws/acs/audio`). That socket is bridged to the existing Voice Live
   session (`AcsVoiceBridge` ↔ `VoiceSessionHandler`): meeting audio → Voice Live →
   Foundry agent → spoken answer back into the call.

```
Teams meeting ──(anonymous join, lobby)──► ACS Calling Web SDK (browser /acs-join.html)
                                                  │ ServerCallId
                                                  ▼
                                    POST /api/acs/call → connect_call(media_streaming)
                                                  │
              wss://…/ws/acs/audio  ◄────────────┘   (16-bit PCM mono, 24 kHz, MIXED)
                      │
              AcsVoiceBridge ◄──► VoiceSessionHandler ◄──► Voice Live + Foundry agent
```

## Acceptance criteria → mechanism

| Requirement | How it's met |
|---|---|
| 1. Add the avatar to the meeting **invite** (pre-scheduled) | **Partial today** — a launcher opens `/acs-join.html` at meeting start. Fully-unattended pre-scheduled join needs the A3 (.NET) joiner upgrade (the bridge is unchanged). |
| 2. **Pull the avatar in when needed** (on demand) | Open `/acs-join.html`, paste the meeting link, **Join** — mid-meeting, on demand. The optional **Companion control panel** (below) makes this a one-click in-meeting action. |
| 3. Anyone **unmutes and asks by voice**, she answers aloud | The ACS participant hears the **MIXED** room audio; a **wake phrase** (`Hey Nuru`) gates her reply so she answers only when addressed and never talks over people. |

## Companion control panel (optional in-meeting surface)

An optional, additive Teams **meeting side-panel** that is the in-meeting front door to
the ACS participant — **not** a second avatar and **not** a video face (an unsynced face
would be misleading, so it is deliberately not built). It is a durable *control plane* for
the audio spine. From inside a meeting it:

- shows whether **Nuru is live in the call** (polls `GET /api/acs/status`),
- has a **"Bring Nuru into this call"** button that opens the proven `/acs-join.html`
  joiner in a **separate window** (kept outside the Teams meeting webview on purpose, to
  avoid a second in-client audio leg / echo), prefilled with the meeting link,
- surfaces the **consent notice**, wake-phrase usage, and troubleshooting,
- offers an optional, clearly-labelled link to the **private** Phase 1 avatar tab (a
  personal preview — separate from the in-call participant, not shown to the meeting).

It is **off by default**. Build the Teams package with `--enable-companion` (or
`TEAMS_ENABLE_COMPANION=1`) to include the `configurableTabs` meeting entry; without the
flag the package is byte-for-byte the Phase 1/2a shape. Pages: `frontend/companion.html`
(panel), `frontend/companion-config.html` (tab config). Add it in a meeting via
**+ (Add an app) → Nuru**. Requires the same tenant custom-app permission as the rest of
the Teams app (no extra admin consent / no RSC permissions).

> The Companion does not need server-side media changes — it reuses the ACS endpoints. A
> future enhancement could auto-detect the meeting join link via TeamsJS meeting APIs, but
> that needs admin-approved meeting permissions, so today the link is pasted (no admin).

## Enable it

Phase 2b is **off unless ACS is configured** — every `/api/acs/*` endpoint returns 503 and
the bridge never runs, so a deploy without it is unchanged. To turn it on:

1. **Provision ACS** — set `ENABLE_ACS=true` (and optionally `ACS_DATA_LOCATION`) before
   `azd up`; the conditional `infra/modules/communicationServices.bicep` creates the
   resource and passes `ACS_ENDPOINT` to the container automatically.
2. **Configure auth** — when `ENABLE_ACS=true`, `infra` automatically grants the
   container's managed identity a role on the ACS resource (`acsRoleForApp.bicep`), so the
   `ACS_ENDPOINT` + managed-identity path works out of the box. Alternatively set
   `ACS_CONNECTION_STRING` to bypass RBAC.
3. **Set the knobs** (optional) — `ACS_WAKE_PHRASES`, `ACS_REQUIRE_WAKE_PHRASE`,
   `ACS_AUDIO_SAMPLE_RATE`, `ACS_IDLE_TIMEOUT_S`, `ACS_CALLBACK_BASE_URL`. See
   `.env.example`.
4. **Use it** — open `https://<your-app>/acs-join.html`, paste a Teams meeting link, Join.
   In the meeting, say **"Hey Nuru, …"** and ask a question.

> **Local dev:** ACS must reach your server's HTTPS callback + `wss://` media URL, so run
> behind a Dev Tunnel / ngrok and set `ACS_CALLBACK_BASE_URL` to that public URL.

## Compliance (live audio participant, even without recording)

Even though Phase 2b **does not record or transcribe**, a live AI participant that listens
to the room carries notification/consent obligations:

- **Tell participants an AI assistant is present and listening.** `/acs-join.html` shows a
  consent notice to the launcher; you should also announce it verbally and/or rename the
  participant clearly (it joins as **"Nuru (AI assistant)"**). Where required by law
  (two-party-consent jurisdictions) or policy, get explicit consent before joining.
- **No recording.** The bridge streams audio transiently to answer in real time and does
  not persist meeting audio. If recording/transcription is added later, that is a separate
  phase with heavier consent + retention obligations.
- **Tenant policy.** Some tenants restrict bots/automated participants in meetings and
  custom-app upload. Confirm your tenant permits this (see *Steps you must do yourself*).

## Steps you must do yourself (portal / admin)

- **Confirm tenant meeting policy** allows an automated/ACS participant and custom-app use
  in meetings. *(You have no Teams-admin access — this is the key potential blocker; ask
  whoever holds Teams-admin to confirm.)*
- **Provision/authorize ACS:** create the ACS resource (or `ENABLE_ACS=true`). With
  `ENABLE_ACS=true` the managed-identity role on the ACS resource is granted automatically;
  otherwise set `ACS_CONNECTION_STRING`.
- **Meeting lobby:** anonymous join is lobby-governed. For unattended/auto-admit, the
  organizer sets "Anyone can bypass the lobby" (or admits the participant manually).

## Known limitations / follow-ups

- **Unattended pre-scheduled join (req #1)** needs a human/launcher to open the browser
  page today. Upgrade path: an isolated **A3 (.NET) calling-client** joiner — it replaces
  only step 1; the `AcsVoiceBridge` / `connect_call` / `/ws/acs/audio` server path is
  unchanged.
- **Turn-taking** is a first, tunable slice (wake-phrase gating + barge-in). Half-duplex
  behaviour over live, noisy room audio needs tuning during the live verification spike.
- **Latency:** the ACS hop stacks on top of Voice Live's first-token latency; measure
  end-to-end against a real meeting.
