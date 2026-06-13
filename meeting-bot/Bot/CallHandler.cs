using System.Collections.Concurrent;
using AvatarForge.MeetingBot.Bridge;
using AvatarForge.MeetingBot.Configuration;

// NOTE: these usings resolve only once the Graph Communications media packages
// are restored on a Windows build host. They are the real SDK namespaces used
// by the official local-media samples (HueBot / PsiBot).
using Microsoft.Graph.Communications.Calls;
using Microsoft.Graph.Communications.Calls.Media;
using Microsoft.Skype.Bots.Media;

namespace AvatarForge.MeetingBot.Bot;

/// <summary>
/// Owns the media plumbing for a single joined call: pumps inbound MIXED
/// participant audio from the Graph <see cref="IAudioSocket"/> to the Python
/// bridge, and plays Nuru's PCM answer (received from the bridge) back into the
/// call. It contains NO answering logic — that all lives in Python.
///
/// ── Audio format ──
/// The Real-Time Media Platform delivers/accepts 16 kHz mono PCM16 (1 channel,
/// 20 ms frames = 640 bytes). We run the bridge at the same rate, so there is
/// no resampling. Keep <see cref="BotOptions.BridgeSampleRate"/> == 16000.
/// </summary>
public sealed class CallHandler : IAsyncDisposable
{
    private readonly ICall _call;
    private readonly BotOptions _options;
    private readonly ILogger<CallHandler> _logger;
    private readonly VoiceLiveBridgeClient _bridge;

    /// <summary>The Graph call this handler owns (used by the bot to leave).</summary>
    public ICall Call => _call;

    // Outbound playout queue: PCM16 chunks from Voice Live, drained at frame
    // cadence onto the AudioSocket. A queue (not direct send) lets barge-in
    // flush everything instantly.
    private readonly ConcurrentQueue<byte[]> _playout = new();
    private volatile bool _flush;

    // 20 ms of 16 kHz mono PCM16 = 16000 * 0.02 * 2 bytes = 640 bytes.
    private const int FrameBytes = 640;

    public CallHandler(ICall call, BotOptions options, ILoggerFactory loggerFactory)
    {
        _call = call;        _options = options;
        _logger = loggerFactory.CreateLogger<CallHandler>();
        _bridge = new VoiceLiveBridgeClient(
            new Uri(options.BridgeWebSocketUrl),
            options.BridgeSampleRate,
            loggerFactory.CreateLogger<VoiceLiveBridgeClient>());
    }

    public async Task StartAsync(CancellationToken ct = default)
    {
        // 1. Connect the bridge to Python (sends AudioMetadata up-front).
        await _bridge.ConnectAsync(ct).ConfigureAwait(false);

        // 2. Nuru's answer audio -> enqueue for playout into the call.
        _bridge.AudioReceived += pcm =>
        {
            _playout.Enqueue(pcm);
            return Task.CompletedTask;
        };

        // 3. Barge-in: flush queued playout so she stops mid-sentence.
        _bridge.StopAudioRequested += () =>
        {
            _flush = true;
            while (_playout.TryDequeue(out _)) { }
            _flush = false;
            return Task.CompletedTask;
        };

        // 4. Wire the Graph AudioSocket (inbound + outbound).
        WireAudioSocket();
    }

    /// <summary>
    /// Wires the call's audio socket. Inbound MIXED audio -> bridge. Outbound
    /// playout queue -> socket. This is the only code that touches the media
    /// SDK; everything above is transport-agnostic.
    /// </summary>
    private void WireAudioSocket()
    {
        var mediaSession = _call.GetLocalMediaSession();
        IAudioSocket audioSocket = mediaSession.AudioSocket;

        // ── Inbound: room -> Python ──
        audioSocket.AudioMediaReceived += async (_, e) =>
        {
            try
            {
                // e.Buffer is unmanaged PCM16; copy to managed before the SDK
                // recycles it, then forward to the bridge.
                var len = (int)e.Buffer.Length;
                var pcm = new byte[len];
                System.Runtime.InteropServices.Marshal.Copy(e.Buffer.Data, pcm, 0, len);

                // Cheap silence flag so Python can short-circuit (it re-checks).
                bool silent = IsSilent(pcm);
                await _bridge.SendAudioFrameAsync(pcm, silent).ConfigureAwait(false);
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "Inbound audio forward failed.");
            }
            finally
            {
                e.Buffer.Dispose();
            }
        };

        // ── Outbound: Python -> room ──
        // Drain the playout queue at 20 ms cadence onto the AudioSocket.
        _ = Task.Run(() => PlayoutLoopAsync(audioSocket));
    }

    private async Task PlayoutLoopAsync(IAudioSocket audioSocket)
    {
        var carry = new List<byte>(FrameBytes * 2);
        var timer = new PeriodicTimer(TimeSpan.FromMilliseconds(20));
        while (await timer.WaitForNextTickAsync().ConfigureAwait(false))
        {
            if (_flush) { carry.Clear(); continue; }

            while (carry.Count < FrameBytes && _playout.TryDequeue(out var chunk))
                carry.AddRange(chunk);

            if (carry.Count < FrameBytes) continue; // not enough buffered yet

            var frame = carry.GetRange(0, FrameBytes).ToArray();
            carry.RemoveRange(0, FrameBytes);

            try
            {
                // Send one 20 ms PCM16 frame into the meeting.
                var buffer = new AudioSendBuffer(frame, AudioFormat.Pcm16K);
                audioSocket.Send(buffer);
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "Outbound audio send failed.");
            }
        }
    }

    private static bool IsSilent(byte[] pcm16)
    {
        // Quick RMS check; threshold chosen for 16-bit samples.
        long sumSq = 0;
        for (int i = 0; i + 1 < pcm16.Length; i += 2)
        {
            short s = (short)(pcm16[i] | (pcm16[i + 1] << 8));
            sumSq += (long)s * s;
        }
        int samples = pcm16.Length / 2;
        if (samples == 0) return true;
        double rms = Math.Sqrt(sumSq / (double)samples);
        return rms < 200; // ~ -40 dBFS
    }

    public async ValueTask DisposeAsync()
    {
        await _bridge.DisposeAsync().ConfigureAwait(false);
    }
}

/// <summary>
/// Minimal AudioSendBuffer wrapper. The real SDK provides
/// <c>Microsoft.Skype.Bots.Media.AudioSendBuffer</c>; this thin subclass just
/// adapts a managed byte[] into the unmanaged buffer the platform expects.
/// (Faithful to the HueBot sample's AudioSendBuffer pattern.)
/// </summary>
internal sealed class AudioSendBuffer : Microsoft.Skype.Bots.Media.AudioMediaBuffer
{
    public AudioSendBuffer(byte[] pcm, AudioFormat format)
    {
        Length = pcm.Length;
        AudioFormat = format;
        Data = System.Runtime.InteropServices.Marshal.AllocHGlobal(pcm.Length);
        System.Runtime.InteropServices.Marshal.Copy(pcm, 0, Data, pcm.Length);
    }

    protected override void Dispose(bool disposing)
    {
        if (Data != IntPtr.Zero)
        {
            System.Runtime.InteropServices.Marshal.FreeHGlobal(Data);
            Data = IntPtr.Zero;
        }
    }
}
