using AvatarForge.MeetingBot.Configuration;
using Microsoft.Extensions.Options;

// Graph Communications SDK namespaces — resolve on a Windows build host once the
// media packages are restored.
using Microsoft.Graph.Communications.Calls;
using Microsoft.Graph.Communications.Calls.Media;
using Microsoft.Graph.Communications.Client;
using Microsoft.Graph.Communications.Common.Telemetry;
using Microsoft.Skype.Bots.Media;
using Microsoft.Graph.Communications.Resources;

namespace AvatarForge.MeetingBot.Bot;

/// <summary>
/// The bot singleton: owns the <see cref="ICommunicationsClient"/> (Graph
/// calling + media platform) and the join logic. One instance for the process;
/// it spins up a <see cref="CallHandler"/> per joined meeting.
///
/// Mirrors the official Graph Communications "local media" sample shape, trimmed
/// to exactly what Slice 1 (audio) needs.
/// </summary>
public sealed class MeetingBotService : IDisposable
{
    private readonly BotOptions _options;
    private readonly ILoggerFactory _loggerFactory;
    private readonly ILogger<MeetingBotService> _logger;
    private readonly ICommunicationsClient _client;
    private readonly Dictionary<string, CallHandler> _handlers = new();

    public MeetingBotService(IOptions<BotOptions> options, ILoggerFactory loggerFactory)
    {
        _options = options.Value;
        _options.Validate();
        _loggerFactory = loggerFactory;
        _logger = loggerFactory.CreateLogger<MeetingBotService>();

        // Telemetry/logging sink required by the SDK.
        var graphLogger = new GraphLogger(nameof(MeetingBotService));

        // Build the calling client. The media platform is configured with our
        // public FQDN, media port and TLS cert (see BotOptions) so the
        // Real-Time Media Platform can negotiate media with Teams.
        var builder = new CommunicationsClientBuilder(
                appName: "AvatarForgeMeetingBot",
                appId: _options.AppId,
                logger: graphLogger)
            .SetAuthenticationProvider(
                new AuthenticationProvider(
                    _options.AppId,
                    _options.AppSecret,
                    _options.TenantId,
                    graphLogger))
            .SetNotificationUrl(new Uri($"https://{_options.ServiceFqdn}:{_options.SignalingPort}/api/calling"))
            .SetMediaPlatformSettings(BuildMediaPlatformSettings())
            .SetServiceBaseUrl(new Uri("https://graph.microsoft.com/v1.0"));

        _client = builder.Build();
        _client.Calls().OnIncoming += OnIncomingCall;
        _client.Calls().OnUpdated += OnCallsUpdated;
    }

    private MediaPlatformSettings BuildMediaPlatformSettings() => new()
    {
        MediaPlatformInstanceSettings = new MediaPlatformInstanceSettings
        {
            CertificateThumbprint = _options.CertificateThumbprint,
            InstanceInternalPort = _options.MediaPort,
            InstancePublicPort = _options.MediaPort,
            InstancePublicIPAddress = System.Net.IPAddress.Any,
            ServiceFqdn = _options.ServiceFqdn,
        },
        ApplicationId = _options.AppId,
    };

    /// <summary>
    /// Join a Teams meeting by its full join URL (the "Click here to join the
    /// meeting" link). Anonymous app-hosted-media join — no per-user token.
    /// Returns the call id.
    /// </summary>
    public async Task<string> JoinMeetingAsync(string joinUrl, string? displayName = null)
    {
        // Parse the join URL into the chat + meeting info the SDK needs.
        var (chatInfo, meetingInfo) = JoinInfo.ParseJoinURL(joinUrl);

        var mediaSession = CreateLocalMediaSession();

        var joinParams = new JoinMeetingParameters(chatInfo, meetingInfo, mediaSession)
        {
            // How Nuru appears in the roster.
            TenantId = _options.TenantId,
        };
        if (!string.IsNullOrWhiteSpace(displayName))
        {
            joinParams.GuestIdentity = new Microsoft.Graph.Models.Identity
            {
                DisplayName = displayName,
                Id = Guid.NewGuid().ToString(),
            };
        }

        var call = await _client.Calls().AddAsync(joinParams).ConfigureAwait(false);
        _logger.LogInformation("Joining meeting; call id = {CallId}", call.Id);

        var handler = new CallHandler(call, _options, _loggerFactory);
        _handlers[call.Id] = handler;
        await handler.StartAsync().ConfigureAwait(false);
        return call.Id;
    }

    /// <summary>Leave / end a joined call.</summary>
    public async Task LeaveAsync(string callId)
    {
        if (_handlers.Remove(callId, out var handler))
        {
            try { await handler.Call.DeleteAsync().ConfigureAwait(false); }
            finally { await handler.DisposeAsync().ConfigureAwait(false); }
        }
    }

    /// <summary>
    /// Build the local media session: an audio-only session for Slice 1. (Slice
    /// 2 — the avatar face — would add a VideoSocket here; see
    /// docs/teams-meeting-bot.md §10.)
    /// </summary>
    private ILocalMediaSession CreateLocalMediaSession()
    {
        return _client.CreateMediaSession(
            new AudioSocketSettings
            {
                StreamDirections = StreamDirection.Sendrecv,
                // MIXED whole-room audio at 16 kHz; no per-participant unmixing.
                SupportedAudioFormat = AudioFormat.Pcm16K,
            });
    }

    private void OnIncomingCall(object? sender, CollectionEventArgs<ICall> args)
    {
        // We are an outbound joiner, not an answerer, so incoming calls are
        // unexpected. Log and ignore (or redirect) per policy.
        foreach (var call in args.AddedResources)
            _logger.LogWarning("Unexpected incoming call {CallId} — ignoring.", call.Id);
    }

    private void OnCallsUpdated(object? sender, CollectionEventArgs<ICall> args)
    {
        foreach (var call in args.RemovedResources)
        {
            if (_handlers.Remove(call.Id, out var handler))
            {
                _logger.LogInformation("Call {CallId} ended; tearing down handler.", call.Id);
                _ = handler.DisposeAsync();
            }
        }
    }

    /// <summary>Expose the SDK's HTTP request processor for the calling webhook.</summary>
    public ICommunicationsClient Client => _client;

    public void Dispose()
    {
        foreach (var h in _handlers.Values) _ = h.DisposeAsync();
        _handlers.Clear();
        _client.Dispose();
    }
}
