# Avatar-Forge Teams meeting media bot (.NET / Windows)

> **Phase 2b, issue #27 — Slice 1 (audio).** This is the thin .NET/Windows media
> relay described in [`docs/teams-meeting-bot.md`](../docs/teams-meeting-bot.md).
> It joins a Teams meeting, captures the **mixed participant audio**, and forwards
> raw PCM16 over a WebSocket to the **unchanged** Python backend
> (`backend/acs/bridge.py::AcsVoiceBridge`). All answering / RAG / turn-taking
> stays in Python.

## Why this exists (the one-paragraph version)

A browser/Teams-tab client can only ever hear **its own mic** (Teams client
isolation), and ACS Call Automation server-side media does **not** carry Teams
*meeting* audio — both proven live. The **only** way for Nuru to hear everyone in
the room is the Graph **Real-Time Media Platform** (`Microsoft.Skype.Bots.Media`),
which is **.NET + Windows-only**. So this is a small, separate Windows service that
acts as a dumb media pump into the existing Python brain.

## ⚠️ Platform reality — must build AND run on Windows

`Microsoft.Graph.Communications.Calls.Media` carries a native Windows media stack.
The bot **must run on a Windows Server guest OS** (Cloud Service, Service Fabric +
VMSS, IaaS VM, or AKS Windows node pool). It **cannot** run on Linux/macOS or an
Azure Web App / Linux Container App. You may *edit* the C# anywhere; you must
*build & host* on Windows (`RuntimeIdentifier=win-x64`).

## Architecture

```
Teams meeting ──mixed audio──▶ [Skype.Bots.Media AudioSocket]
                                        │ PCM16 16 kHz
                                        ▼
                               [VoiceLiveBridgeClient]  ──WSS──▶  Python /ws/acs/audio
                                        ▲                          (AcsVoiceBridge ▸ VoiceSessionHandler
                                        │ PCM16 (Nuru's answer)     ▸ Voice Live ▸ Foundry RAG+news)
                               [AudioSocket.Send] ──audio──▶ Teams meeting
```

The seam is the **already-built** `/ws/acs/audio` endpoint. The bot just speaks its
wire protocol (`AudioMetadata` → base64-PCM16 `AudioData` frames; inbound
`AudioData` to play, `StopAudio` for barge-in). **No Python changes are required**
for Slice 1 beyond setting `ACS_AUDIO_SAMPLE_RATE=16000` (see below).

## Project layout

| Path | Role |
| --- | --- |
| `Program.cs` | ASP.NET host; binds config, starts the bot, maps controllers. |
| `Configuration/BotOptions.cs` | Strongly-typed config (`Bot:*`). |
| `Bot/MeetingBot.cs` | Owns the `ICommunicationsClient`; `JoinMeetingAsync` / `LeaveAsync`. |
| `Bot/CallHandler.cs` | Per-call media plumbing: AudioSocket ⇄ bridge. |
| `Bot/AuthenticationProvider.cs` | App-only Graph token (MSAL) + inbound validation. |
| `Bridge/VoiceLiveBridgeClient.cs` | **The Python contract.** WS client speaking the AcsVoiceBridge protocol. Unit-tested, no media-SDK deps. |
| `Http/JoinController.cs` | Operator API: `POST /api/join`, `POST /api/leave`. |
| `Http/CallingController.cs` | Bot Framework calling webhook (`POST /api/calling`). |
| `infra/host.bicep` | **Standalone** Windows VM + NSG + calling-bot registration. |

## Configuration

Set via `appsettings.json` or environment (`Bot__*`). **Never commit the secret.**

| Key | Value (MngEnv) |
| --- | --- |
| `Bot:AppId` | `860ecee0-c226-4930-8c00-e37bae4a3ae5` (`avatar-forge-meeting-bot`) |
| `Bot:TenantId` | `349b3dac-8649-4410-acdc-ef8bbcb7a46f` |
| `Bot:AppSecret` | from env `BOT_CLIENT_SECRET` (stored in azd env, git-ignored) |
| `Bot:ServiceFqdn` | the Windows host public FQDN (from `host.bicep` output) |
| `Bot:CertificateThumbprint` | a publicly-trusted cert in `LocalMachine\My` matching the FQDN |
| `Bot:BridgeWebSocketUrl` | `wss://<your-app>.azurecontainerapps.io/ws/acs/audio` |
| `Bot:BridgeSampleRate` | `16000` |

## Runbook (operator — Windows host required)

1. **Provision the host + calling registration** (standalone, additive — does NOT
   touch the web app deploy):
   ```pwsh
   az deployment group create -g rg-avatar-mngenv `
     -f meeting-bot/infra/host.bicep `
     -p botAppId=860ecee0-c226-4930-8c00-e37bae4a3ae5 `
        botAppTenantId=349b3dac-8649-4410-acdc-ef8bbcb7a46f `
        adminPassword='<strong-password>' dnsLabel=avatar-meeting-bot
   ```
   Note the `publicFqdn` / `signalingEndpoint` outputs.
2. **Install a publicly-trusted TLS cert** on the VM (`LocalMachine\My`) whose
   subject matches `publicFqdn`; record its thumbprint.
3. **Open the firewall** on the VM for the signaling + media ports (the NSG already
   allows them inbound).
4. **Set the Python side** so the bridge runs at 16 kHz to match the media platform:
   `azd env set ACS_AUDIO_SAMPLE_RATE 16000` and `azd env set ACS_ENABLED true`,
   then redeploy the web app. (`ACS_REQUIRE_WAKE_PHRASE=true` keeps Nuru silent
   until addressed — recommended for multi-exec meetings.)
5. **Build & publish the bot on the Windows host:**
   ```pwsh
   dotnet publish meeting-bot/MeetingBot.csproj -c Release -r win-x64 --self-contained
   ```
   Set `Bot__AppSecret` (= `BOT_CLIENT_SECRET`), `Bot__ServiceFqdn`,
   `Bot__CertificateThumbprint`, `Bot__BridgeWebSocketUrl`, then run the published exe.
6. **Teams manifest:** set `supportsCalling: true` (and `supportsVideo` later for
   Slice 2) on the bot entry, rebuild the package (`teams/build_package.py`), and
   upload. Requires a tenant **custom-app** policy + a **meeting policy allowing
   bots** (you are global admin in MngEnv, so self-serviceable).
7. **Test:** start a Teams meeting in the MngEnv tenant, then
   `POST https://<publicFqdn>:9441/api/join { "joinUrl": "<meeting link>" }`. Nuru
   should appear in the roster, hear the room, and answer aloud on the wake phrase.

## What is verified vs. pending

- ✅ **`VoiceLiveBridgeClient` — the Python contract — is unit-tested** (metadata,
  outbound `AudioData`, inbound `AudioData` dispatch, `StopAudio` barge-in all pass
  a round-trip against a mock server).
- ✅ `infra/host.bicep` compiles clean (`az bicep build`).
- ⏳ The media-SDK code (`MeetingBot.cs`, `CallHandler.cs`) can only be built/run on
  a Windows host with the Graph media packages restored and a real meeting. Points
  needing live confirmation are marked `TODO(prod...)` in the source: HTTPS/cert
  binding in `Program.cs`, inbound notification validation in `AuthenticationProvider`,
  and exact `AudioSocket`/`AudioSendBuffer` API shapes against the restored SDK
  version.

## Cost / honesty note

Per the ADR in `docs/teams-meeting-bot.md`, this breaks the pure-Python / Linux-ACA
guardrail **only** for the media leg, because no alternative can hear the room. The
brain stays Python; this service stays a dumb pump. The real tax is the Windows host
+ certs + one extra PCM hop — not the language.
