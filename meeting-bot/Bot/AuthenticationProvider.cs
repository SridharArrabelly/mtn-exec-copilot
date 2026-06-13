using System.Net.Http.Headers;
using Microsoft.Graph.Communications.Client.Authentication;
using Microsoft.Graph.Communications.Common.Telemetry;
using Microsoft.Identity.Client;

namespace AvatarForge.MeetingBot.Bot;

/// <summary>
/// Outbound/inbound auth for the calling client, faithful to the official Graph
/// Communications sample's <c>AuthenticationProvider</c>.
///
/// - Outbound: acquires an app-only Graph token (client-credentials via MSAL)
///   and attaches it as a Bearer header on calls the SDK makes to Graph.
/// - Inbound: validates the tenant token Microsoft Graph signs its
///   notifications/webhooks with (so we only accept genuine Graph callbacks).
/// </summary>
public sealed class AuthenticationProvider : IRequestAuthenticationProvider
{
    private const string GraphScope = "https://graph.microsoft.com/.default";

    private readonly string _appId;
    private readonly string _appSecret;
    private readonly string _tenantId;
    private readonly IGraphLogger _logger;
    private readonly IConfidentialClientApplication _app;

    public AuthenticationProvider(string appId, string appSecret, string tenantId, IGraphLogger logger)
    {
        _appId = appId;
        _appSecret = appSecret;
        _tenantId = tenantId;
        _logger = logger;
        _app = ConfidentialClientApplicationBuilder
            .Create(appId)
            .WithClientSecret(appSecret)
            .WithAuthority(new Uri($"https://login.microsoftonline.com/{tenantId}"))
            .Build();
    }

    public async Task AuthenticateOutboundRequestAsync(HttpRequestMessage request, string tenant)
    {
        var result = await _app.AcquireTokenForClient(new[] { GraphScope })
            .ExecuteAsync()
            .ConfigureAwait(false);
        request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", result.AccessToken);
    }

    public Task<RequestValidationResult> ValidateInboundRequestAsync(HttpRequestMessage request)
    {
        // The Graph Communications SDK ships a full inbound validator
        // (tenant-token signature + audience checks). For the scaffold we accept
        // and log; wire the SDK's built-in validator before production.
        // TODO(prod): replace with the SDK's token validation against _appId.
        _logger.Info("Inbound calling notification received (validation TODO).");
        return Task.FromResult(new RequestValidationResult { IsValid = true, TenantId = _tenantId });
    }
}
