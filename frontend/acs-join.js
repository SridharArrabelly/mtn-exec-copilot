/*
 * Browser joiner for the in-call avatar (issue #27, Phase 2b — D5 option A1).
 *
 * This is the one piece that cannot run server-side: ACS Call Automation has no
 * "join Teams meeting by URL" API, so a client-side ACS Calling SDK must join the
 * meeting first (as an anonymous interop guest — governed by the meeting lobby,
 * no Teams admin needed) and surface a ServerCallId. We then hand that id to the
 * Python server (POST /api/acs/call) which attaches the bidirectional audio
 * bridge via connect_call().
 *
 * Loaded as an ES module straight from a CDN so there is NO Node build/toolchain
 * on the server — consistent with the repo's no-Node guardrail. Pinned to a
 * specific SDK version for reproducibility.
 */
import {
    CallClient,
} from "https://esm.sh/@azure/communication-calling@1.40.1";
import {
    AzureCommunicationTokenCredential,
} from "https://esm.sh/@azure/communication-common@2.3.1";

const $ = (id) => document.getElementById(id);
const statusEl = $("status");
const joinBtn = $("joinBtn");
const leaveBtn = $("leaveBtn");
const linkEl = $("meetingLink");

let call = null;
let callAgent = null;

function log(msg) {
    console.log("[acs-join]", msg);
    statusEl.textContent = msg;
}

async function ensureEnabled() {
    const res = await fetch("/api/acs/config");
    const cfg = await res.json();
    if (!cfg.enabled) {
        log("ACS in-call media is not enabled on this deployment (set ACS_CONNECTION_STRING / ACS_ENDPOINT and ENABLE_ACS=true).");
        joinBtn.disabled = true;
        return false;
    }
    return true;
}

async function join() {
    const meetingLink = linkEl.value.trim();
    if (!meetingLink) { log("Paste a Teams meeting join link first."); return; }
    joinBtn.disabled = true;

    try {
        log("Requesting an ACS access token…");
        const tokRes = await fetch("/api/acs/token", { method: "POST" });
        if (!tokRes.ok) throw new Error(`token endpoint returned ${tokRes.status}`);
        const { token } = await tokRes.json();

        log("Initialising the ACS Calling SDK…");
        const callClient = new CallClient();
        const credential = new AzureCommunicationTokenCredential(token);
        callAgent = await callClient.createCallAgent(credential, {
            displayName: "Nuru (AI assistant)",
        });

        log("Joining the Teams meeting (you may wait in the lobby until admitted)…");
        call = callAgent.join({ meetingLink }, {
            // Audio-only participant (D2): the animated face is a separate optional
            // surface; the roster participant is voice-only.
            //
            // The launcher's local mic is muted on purpose — Nuru's voice is
            // injected by the *server* (Call Automation bidirectional media via
            // connect_call), not from this browser. Whether server-injected audio
            // is audible while this participant leg is muted is the key thing the
            // 2b.1 live spike must confirm against a real meeting.
            audioOptions: { muted: true },
            videoOptions: { localVideoStreams: [] },
        });

        leaveBtn.disabled = false;
        call.on("stateChanged", async () => {
            log(`Call state: ${call.state}`);
            if (call.state === "Connected") {
                await attachServerMedia();
            }
            if (call.state === "Disconnected") {
                leaveBtn.disabled = true;
                joinBtn.disabled = false;
            }
        });
    } catch (e) {
        log(`Join failed: ${e.message || e}`);
        joinBtn.disabled = false;
    }
}

async function attachServerMedia() {
    try {
        const serverCallId = await call.info.getServerCallId();
        log("Connected. Attaching Nuru's voice bridge on the server…");
        const res = await fetch("/api/acs/call", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ serverCallId }),
        });
        if (!res.ok) {
            const err = await res.text();
            throw new Error(`/api/acs/call returned ${res.status}: ${err}`);
        }
        log("Nuru is in the call. Say your wake phrase (e.g. \"Hey Nuru\") and ask a question aloud.");
    } catch (e) {
        log(`Server media attach failed: ${e.message || e}`);
    }
}

async function leave() {
    leaveBtn.disabled = true;
    try {
        if (call) await call.hangUp();
    } catch (e) {
        console.warn("hangUp error", e);
    }
    call = null;
    log("Left the meeting.");
    joinBtn.disabled = false;
}

joinBtn.addEventListener("click", join);
leaveBtn.addEventListener("click", leave);

// The Companion control panel (companion.html, opened in a separate window so the
// ACS Calling leg runs OUTSIDE the Teams meeting webview) hands the meeting link
// over via ?meeting=. Prefill it so the user does not paste twice.
try {
    const prefill = new URLSearchParams(window.location.search).get("meeting");
    if (prefill) linkEl.value = prefill;
} catch (e) { /* ignore */ }

ensureEnabled();
