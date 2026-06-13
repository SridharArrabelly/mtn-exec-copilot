using AvatarForge.MeetingBot.Bot;
using AvatarForge.MeetingBot.Configuration;

var builder = WebApplication.CreateBuilder(args);

// ── Configuration ──
// Bind Bot:* from appsettings + environment. AppSecret should come from the
// environment (Bot__AppSecret) — never appsettings.json. In the avatar-forge
// MngEnv setup it is the azd env value BOT_CLIENT_SECRET.
builder.Services.Configure<BotOptions>(builder.Configuration.GetSection(BotOptions.SectionName));

// Allow the standard env var BOT_CLIENT_SECRET to populate Bot:AppSecret.
var envSecret = Environment.GetEnvironmentVariable("BOT_CLIENT_SECRET");
if (!string.IsNullOrWhiteSpace(envSecret))
{
    builder.Services.PostConfigure<BotOptions>(o => o.AppSecret = envSecret);
}

builder.Services.AddControllers();
builder.Services.AddSingleton<MeetingBot>();

// ── Kestrel ──
// Two public surfaces (see docs/teams-meeting-bot.md §8):
//   - HTTPS signaling/webhook + operator API on SignalingPort
//   - the media platform binds its own TLS media port (MediaPort) internally
// Both require the publicly-trusted cert (CertificateThumbprint) on a Windows
// host. We let the media SDK own the media port; Kestrel owns signaling.
var botSection = builder.Configuration.GetSection(BotOptions.SectionName);
var signalingPort = botSection.GetValue<int?>("SignalingPort") ?? 9441;

builder.WebHost.ConfigureKestrel(kestrel =>
{
    // TODO(prod, Windows): bind HTTPS with the LocalMachine\My cert matching
    // ServiceFqdn (CertificateThumbprint). Example:
    //   kestrel.ListenAnyIP(signalingPort, lo => lo.UseHttps(StoreName.My,
    //       botOptions.ServiceFqdn, allowInvalid: false));
    // Left as HTTP here so the project runs for local wiring tests; Graph
    // requires HTTPS in production.
    kestrel.ListenAnyIP(signalingPort);
});

var app = builder.Build();

// Eagerly construct the bot so the calling client + media platform initialize
// at startup (and fail fast on bad config).
_ = app.Services.GetRequiredService<MeetingBot>();

app.MapControllers();

app.Logger.LogInformation(
    "Avatar-Forge meeting bot started. Signaling on :{Port}/api/calling, operator API at /api/join.",
    signalingPort);

app.Run();
