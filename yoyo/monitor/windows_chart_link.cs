// Browser URL activation on the viewing Windows PC. Reuses the established
// TradingView executable bridge; no web fallback, listener or UI automation.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Text.RegularExpressions;
using System.Web.Script.Serialization;
using System.Windows.Forms;

public sealed class SpikeChartRequest
{
    public string Symbol { get; private set; }
    public string Timeframe { get; private set; }
    public string ChartUrl { get; private set; }

    public static SpikeChartRequest Parse(string link, string layout)
    {
        // Reject encoded delimiters, duplicate fields, options and shell text.
        // The browser emits this single canonical format, never a caller URL.
        if (link == null || link.Length > 256) throw new ArgumentException();
        Match match = Regex.Match(link, @"\Aspike-tradingview://open\?symbol=([A-Z0-9]{1,30})-(USDT|USDC|USD)-SWAP&timeframe=(5m|15m|30m|1H|4H|1Dutc)\z");
        if (!match.Success) throw new ArgumentException();
        if (layout == null || !Regex.IsMatch(layout, @"\Ahttps://(cn\.|www\.)?tradingview\.com/chart/(?:[A-Za-z0-9]{1,64}/)?\z"))
            throw new ArgumentException();
        var intervals = new Dictionary<string, string> { {"5m", "5"}, {"15m", "15"}, {"30m", "30"}, {"1H", "60"}, {"4H", "240"}, {"1Dutc", "1D"} };
        string basis = match.Groups[1].Value, quote = match.Groups[2].Value, timeframe = match.Groups[3].Value;
        return new SpikeChartRequest {
            Symbol = basis + "-" + quote + "-SWAP", Timeframe = timeframe,
            ChartUrl = layout + "?symbol=OKX%3A" + basis + quote + ".P&interval=" + intervals[timeframe]
        };
    }
}

internal static class SpikeChartLink
{
    private static readonly string Runtime = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Spike");

    private static void Record(SpikeChartRequest request, string status)
    {
        // A dispatch receipt is deliberately not proof that the chart rendered.
        try {
            Directory.CreateDirectory(Runtime);
            var receipt = new Dictionary<string, object> {
                {"symbol", request.Symbol}, {"timeframe", request.Timeframe},
                {"status", status}, {"verified", false}, {"target", "windows"},
                {"updated_at", DateTime.UtcNow.ToString("o")}
            };
            File.WriteAllText(Path.Combine(Runtime, "tradingview-link-last.json"),
                new JavaScriptSerializer().Serialize(receipt), new UTF8Encoding(false));
        } catch (IOException) { } catch (UnauthorizedAccessException) { }
    }

    private static string Failure(string stderr)
    {
        if (stderr.Contains("SPIKE_TV_NOT_INSTALLED")) return "未找到 TradingView Windows 客户端。请先安装并登录客户端。";
        if (stderr.Contains("SPIKE_INTERACTIVE_SESSION_REQUIRED")) return "请在已登录并解锁的 Windows 桌面上点击币种。";
        return "TradingView 未接受打开请求。请确认客户端已登录后重试。";
    }

    [STAThread]
    private static int Main(string[] args)
    {
        // Pure contract-test mode: no process launch, settings, receipt or UI.
        if (args.Length >= 1 && args[0] == "--validate") {
            try {
                if (args.Length != 3) return 2;
                Console.WriteLine(new JavaScriptSerializer().Serialize(SpikeChartRequest.Parse(args[1], args[2])));
                return 0;
            } catch (ArgumentException) { return 2; }
        }
        SpikeChartRequest request = null;
        try {
            if (args.Length != 1) throw new ArgumentException();
            string layoutFile = Path.Combine(Runtime, "tradingview-layout.txt");
            string layout = File.Exists(layoutFile) ? File.ReadAllText(layoutFile).Trim() : "https://www.tradingview.com/chart/";
            request = SpikeChartRequest.Parse(args[0], layout);
            string script = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "windows_tradingview.ps1");
            if (!File.Exists(script)) throw new IOException();
            var start = new ProcessStartInfo {
                FileName = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), @"WindowsPowerShell\v1.0\powershell.exe"),
                Arguments = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File \"" + script + "\" \"" + request.ChartUrl + "\"",
                UseShellExecute = false, CreateNoWindow = true,
                RedirectStandardOutput = true, RedirectStandardError = true
            };
            Record(request, "opening");
            using (Process process = Process.Start(start)) {
                if (process == null) throw new IOException();
                var stdout = process.StandardOutput.ReadToEndAsync();
                var stderr = process.StandardError.ReadToEndAsync();
                if (!process.WaitForExit(20000)) {
                    process.Kill();
                    throw new TimeoutException();
                }
                if (process.ExitCode != 0 || stdout.Result.Trim() != "requested") {
                    Record(request, "failed");
                    MessageBox.Show(Failure(stderr.Result), "Spike · 本机 TradingView", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                    return 1;
                }
            }
            Record(request, "requested");
            return 0;
        } catch (Exception error) {
            if (request != null) Record(request, "failed");
            string message = error is ArgumentException ? "图表链接或本机布局配置无效，请从 Spike 币种列表重新点击。"
                : error is TimeoutException ? "打开 TradingView 超时，请先查看客户端当前图表后重试。"
                : "Spike 本机图表入口不可用，请重新运行 Windows 入口安装程序。";
            MessageBox.Show(message, "Spike · 本机 TradingView", MessageBoxButtons.OK, MessageBoxIcon.Warning);
            return 1;
        }
    }
}
