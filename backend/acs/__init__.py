"""Azure Communication Services in-call media participant (issue #27, Phase 2b).

Additive and opt-in: nothing in this package runs unless ACS is configured
(``ACS_ENABLED``). The standalone web app, the Phase 1 personal tab, and the
Phase 2a chat bot are unaffected when ACS is disabled.

Architecture (D5 = A1 browser joiner):

    Teams meeting
      └─ ACS Calling Web SDK (browser, anonymous interop guest, lobby-governed)
           |  emits ServerCallId
           v
    POST /api/acs/call  -->  CallAutomationClient.connect_call(media_streaming=...)
           |
           v
    wss://.../ws/acs/audio  <-->  AcsVoiceBridge  <-->  VoiceSessionHandler
           (16-bit PCM mono, base64-in-JSON)        (existing Voice Live pipeline)

See ``backend/acs/bridge.py`` for the media bridge and ``backend/acs/routes.py``
for the HTTP/WebSocket endpoints.
"""

from .bridge import AcsVoiceBridge
from .routes import build_acs_router

__all__ = ["AcsVoiceBridge", "build_acs_router"]
