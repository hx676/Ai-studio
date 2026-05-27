using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Text;
using System.Text.Json;

namespace SynCanvasLauncher;

public sealed class SupervisorClient
{
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNameCaseInsensitive = true,
        WriteIndented = true
    };

    public string RootDir { get; }
    public string PythonExe { get; }
    public string SupervisorScript { get; }
    public string LogDir => Path.Combine(RootDir, "data", "service-logs");
    public string InputDir => Path.Combine(RootDir, "assets", "input");
    public string OutputDir => Path.Combine(RootDir, "assets", "output");
    public string VoiceDir => Path.Combine(RootDir, "index-tts-2", "assets", "bak");
    public string HeyGemOutputDir => Path.Combine(RootDir, "heygem-win-fix", "heygem-win", "视频输出");

    public SupervisorClient()
    {
        RootDir = FindRootDir();
        PythonExe = File.Exists(Path.Combine(RootDir, "python", "python.exe"))
            ? Path.Combine(RootDir, "python", "python.exe")
            : "python";
        SupervisorScript = Path.Combine(RootDir, "tools", "service_supervisor.py");
    }

    public async Task<SupervisorStatus?> GetStatusAsync(bool includeGpu = false, bool quick = true)
    {
        var args = includeGpu
            ? "tools\\service_supervisor.py --status --json"
            : "tools\\service_supervisor.py --status --json --no-gpu";
        if (quick)
        {
            args += " --quick";
        }
        var timeout = quick ? TimeSpan.FromSeconds(8) : (includeGpu ? TimeSpan.FromSeconds(60) : TimeSpan.FromSeconds(15));
        var result = await RunPythonAsync(args, timeout: timeout);
        return JsonSerializer.Deserialize<SupervisorStatus>(result.Stdout, JsonOptions);
    }

    public async Task<LauncherConfig?> GetConfigAsync()
    {
        var result = await RunPythonAsync("tools\\service_supervisor.py --config");
        var payload = JsonSerializer.Deserialize<LauncherConfigRoot>(result.Stdout, JsonOptions);
        return payload?.Config;
    }

    public async Task<string> SaveConfigAsync(LauncherConfig config)
    {
        var json = JsonSerializer.Serialize(new { config }, JsonOptions);
        var encoded = Convert.ToBase64String(Encoding.UTF8.GetBytes(json));
        var result = await RunPythonAsync($"tools\\service_supervisor.py --save-config-b64 {encoded}");
        return result.Stdout;
    }

    public async Task<SupervisorActionResult> StartAsync()
    {
        var result = await RunPythonAsync("tools\\service_supervisor.py --start-once");
        return ParseActionResult(result.Stdout);
    }

    public async Task<SupervisorActionResult> StopAsync()
    {
        var result = await RunPythonAsync("tools\\service_supervisor.py --stop", throwOnNonZero: false, timeout: TimeSpan.FromSeconds(30));
        var output = string.IsNullOrWhiteSpace(result.Stdout) ? result.Stderr : result.Stdout;
        return new SupervisorActionResult { Ok = result.ExitCode == 0, RawOutput = output };
    }

    public async Task<ProjectBackendPids?> GetProjectBackendPidsAsync()
    {
        var result = await RunPythonAsync("tools\\service_supervisor.py --project-backend-pids", timeout: TimeSpan.FromSeconds(5));
        return JsonSerializer.Deserialize<ProjectBackendPids>(result.Stdout, JsonOptions);
    }

    public async Task<LogPayload?> ReadLogAsync(string service, string stream)
    {
        var result = await RunPythonAsync($"tools\\service_supervisor.py --logs {service} --stream {stream}", timeout: TimeSpan.FromSeconds(5));
        return JsonSerializer.Deserialize<LogPayload>(result.Stdout, JsonOptions);
    }

    public async Task<ConsoleLogsPayload?> ReadConsoleLogsAsync(Dictionary<string, long>? cursor = null)
    {
        var args = "tools\\service_supervisor.py --logs all --stream both";
        if (cursor is { Count: > 0 })
        {
            var json = JsonSerializer.Serialize(cursor, JsonOptions);
            var encoded = Convert.ToBase64String(Encoding.UTF8.GetBytes(json));
            args += $" --cursor-b64 {encoded}";
        }
        var result = await RunPythonAsync(args, timeout: TimeSpan.FromSeconds(5));
        return JsonSerializer.Deserialize<ConsoleLogsPayload>(result.Stdout, JsonOptions);
    }

    public void OpenMain(string url)
    {
        OpenUrl(url);
    }

    public void OpenRoot() => OpenExternal(RootDir);

    public void OpenInput() => OpenDirectory(InputDir, createIfMissing: false);

    public void OpenOutput() => OpenDirectory(OutputDir, createIfMissing: false);

    public void OpenVoices() => OpenDirectory(VoiceDir, createIfMissing: false);

    public void OpenHeyGemOutput() => OpenDirectory(HeyGemOutputDir, createIfMissing: false);

    public void OpenLogs()
    {
        OpenDirectory(LogDir, createIfMissing: true);
    }

    public string ExportFullLogs()
    {
        Directory.CreateDirectory(LogDir);
        var exportDir = Path.Combine(RootDir, "data", "exports");
        Directory.CreateDirectory(exportDir);
        var zipPath = Path.Combine(exportDir, $"syncanvas-logs-{DateTime.Now:yyyyMMdd-HHmmss}.zip");
        using var archive = ZipFile.Open(zipPath, ZipArchiveMode.Create);
        foreach (var path in Directory.EnumerateFiles(LogDir, "*.log"))
        {
            archive.CreateEntryFromFile(path, Path.GetFileName(path), CompressionLevel.Optimal);
        }
        return zipPath;
    }

    private static string FindRootDir()
    {
        var dir = AppContext.BaseDirectory;
        var current = new DirectoryInfo(dir);
        while (current != null)
        {
            if (File.Exists(Path.Combine(current.FullName, "main.py")) &&
                Directory.Exists(Path.Combine(current.FullName, "tools")))
            {
                return current.FullName;
            }
            current = current.Parent;
        }
        var fallback = Path.GetFullPath(Path.Combine(AppContext.BaseDirectory, ".."));
        return Directory.Exists(fallback) ? fallback : AppContext.BaseDirectory;
    }

    private async Task<CommandResult> RunPythonAsync(string arguments, bool throwOnNonZero = true, TimeSpan? timeout = null)
    {
        if (!File.Exists(SupervisorScript))
        {
            throw new FileNotFoundException("找不到服务监督脚本", SupervisorScript);
        }

        var psi = new ProcessStartInfo
        {
            FileName = PythonExe,
            Arguments = arguments,
            WorkingDirectory = RootDir,
            UseShellExecute = false,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            CreateNoWindow = true,
            StandardOutputEncoding = Encoding.UTF8,
            StandardErrorEncoding = Encoding.UTF8
        };

        using var process = Process.Start(psi) ?? throw new InvalidOperationException("无法启动 Python 进程");
        var stdoutTask = process.StandardOutput.ReadToEndAsync();
        var stderrTask = process.StandardError.ReadToEndAsync();
        var waitTask = process.WaitForExitAsync();
        if (timeout.HasValue)
        {
            var completed = await Task.WhenAny(waitTask, Task.Delay(timeout.Value));
            if (completed != waitTask)
            {
                TryKillProcessTree(process);
                var stdoutOnTimeout = await CompleteTextTaskAsync(stdoutTask, TimeSpan.FromSeconds(2));
                var stderrOnTimeout = await CompleteTextTaskAsync(stderrTask, TimeSpan.FromSeconds(2));
                var timeoutMessage = $"Command timed out after {timeout.Value.TotalSeconds:0}s: {arguments}";
                var combinedError = string.IsNullOrWhiteSpace(stderrOnTimeout)
                    ? timeoutMessage
                    : $"{stderrOnTimeout.TrimEnd()}{Environment.NewLine}{timeoutMessage}";
                return new CommandResult(stdoutOnTimeout, combinedError, -1, TimedOut: true);
            }
        }
        else
        {
            await waitTask;
        }
        var stdout = await stdoutTask;
        var stderr = await stderrTask;
        if (process.ExitCode != 0 && throwOnNonZero)
        {
            throw new InvalidOperationException(string.IsNullOrWhiteSpace(stderr) ? stdout : stderr);
        }
        return new CommandResult(stdout, stderr, process.ExitCode);
    }

    private static void TryKillProcessTree(Process process)
    {
        try
        {
            if (!process.HasExited)
            {
                process.Kill(entireProcessTree: true);
            }
        }
        catch
        {
            // Best effort only. The caller surfaces the timeout.
        }
    }

    private static async Task<string> CompleteTextTaskAsync(Task<string> task, TimeSpan timeout)
    {
        try
        {
            var completed = await Task.WhenAny(task, Task.Delay(timeout));
            return completed == task ? await task : "";
        }
        catch
        {
            return "";
        }
    }

    private static SupervisorActionResult ParseActionResult(string stdout)
    {
        var payload = new SupervisorActionResult { RawOutput = stdout };
        try
        {
            using var document = JsonDocument.Parse(ExtractJsonObject(stdout));
            var root = document.RootElement;
            payload.Ok = root.TryGetProperty("ok", out var ok) && ok.GetBoolean();
            if (root.TryGetProperty("started", out var started) && started.ValueKind == JsonValueKind.Array)
            {
                payload.Started = started.GetArrayLength();
                payload.StartedItems = JsonSerializer.Deserialize<List<SupervisorActionItem>>(started.GetRawText(), JsonOptions) ?? new();
            }
            if (root.TryGetProperty("reused", out var reused) && reused.ValueKind == JsonValueKind.Array)
            {
                payload.Reused = reused.GetArrayLength();
                payload.ReusedItems = JsonSerializer.Deserialize<List<SupervisorActionItem>>(reused.GetRawText(), JsonOptions) ?? new();
            }
            if (root.TryGetProperty("errors", out var errors) && errors.ValueKind == JsonValueKind.Array)
            {
                payload.Errors = errors.GetArrayLength();
                payload.ErrorItems = JsonSerializer.Deserialize<List<SupervisorActionError>>(errors.GetRawText(), JsonOptions) ?? new();
            }
        }
        catch
        {
            payload.Ok = false;
        }
        return payload;
    }

    private static string ExtractJsonObject(string text)
    {
        if (string.IsNullOrWhiteSpace(text))
        {
            return "{}";
        }
        var start = text.IndexOf('{');
        var end = text.LastIndexOf('}');
        if (start >= 0 && end > start)
        {
            return text[start..(end + 1)];
        }
        return text;
    }

    private static void OpenExternal(string target)
    {
        var psi = new ProcessStartInfo
        {
            FileName = target,
            UseShellExecute = true
        };
        Process.Start(psi);
    }

    private static void OpenUrl(string url)
    {
        if (string.IsNullOrWhiteSpace(url))
        {
            throw new InvalidOperationException("主应用地址为空，无法打开浏览器。");
        }
        if (!Uri.TryCreate(url.Trim(), UriKind.Absolute, out var uri) || uri.Scheme is not ("http" or "https"))
        {
            throw new InvalidOperationException($"主应用地址无效：{url}");
        }

        var target = uri.AbsoluteUri;
        Exception? firstError = null;
        try
        {
            var process = Process.Start(new ProcessStartInfo
            {
                FileName = target,
                UseShellExecute = true
            });
            if (process != null)
            {
                return;
            }
        }
        catch (Exception ex)
        {
            firstError = ex;
        }

        try
        {
            var process = Process.Start(new ProcessStartInfo
            {
                FileName = "explorer.exe",
                Arguments = target,
                UseShellExecute = false,
                CreateNoWindow = true
            });
            if (process != null)
            {
                return;
            }
        }
        catch (Exception ex)
        {
            firstError ??= ex;
        }

        throw new InvalidOperationException(firstError == null
            ? "系统没有返回浏览器进程。"
            : $"默认浏览器打开失败：{firstError.Message}");
    }

    private static void OpenDirectory(string path, bool createIfMissing)
    {
        if (createIfMissing)
        {
            Directory.CreateDirectory(path);
        }
        if (!Directory.Exists(path))
        {
            throw new DirectoryNotFoundException($"目录不存在：{path}");
        }
        OpenExternal(path);
    }

    private sealed record CommandResult(string Stdout, string Stderr, int ExitCode, bool TimedOut = false);
}
