using System.Text.Json.Serialization;

namespace SynCanvasLauncher;

public sealed class SupervisorStatus
{
    [JsonPropertyName("ok")]
    public bool Ok { get; set; }

    [JsonPropertyName("time")]
    public string Time { get; set; } = "";

    [JsonPropertyName("root")]
    public string Root { get; set; } = "";

    [JsonPropertyName("main_url")]
    public string MainUrl { get; set; } = "";

    [JsonPropertyName("log_dir")]
    public string LogDir { get; set; } = "";

    [JsonPropertyName("config")]
    public LauncherConfig Config { get; set; } = new();

    [JsonPropertyName("services")]
    public List<ServiceStatus> Services { get; set; } = new();

    [JsonPropertyName("diagnostics")]
    public List<DiagnosticItem> Diagnostics { get; set; } = new();

    [JsonPropertyName("counts")]
    public Dictionary<string, int> Counts { get; set; } = new();
}

public sealed class ServiceStatus
{
    [JsonPropertyName("key")]
    public string Key { get; set; } = "";

    [JsonPropertyName("label")]
    public string Label { get; set; } = "";

    [JsonPropertyName("state")]
    public string State { get; set; } = "";

    [JsonPropertyName("ready")]
    public bool Ready { get; set; }

    [JsonPropertyName("managed")]
    public bool Managed { get; set; }

    [JsonPropertyName("pid")]
    public int? Pid { get; set; }

    [JsonPropertyName("source")]
    public string Source { get; set; } = "";

    [JsonPropertyName("checks")]
    public List<ServiceCheck> Checks { get; set; } = new();
}

public sealed class ServiceCheck
{
    [JsonPropertyName("label")]
    public string Label { get; set; } = "";

    [JsonPropertyName("url")]
    public string Url { get; set; } = "";

    [JsonPropertyName("port")]
    public int Port { get; set; }

    [JsonPropertyName("ready")]
    public bool Ready { get; set; }

    [JsonPropertyName("port_open")]
    public bool PortOpen { get; set; }

    [JsonPropertyName("error")]
    public string Error { get; set; } = "";
}

public sealed class DiagnosticItem
{
    [JsonPropertyName("group")]
    public string Group { get; set; } = "";

    [JsonPropertyName("key")]
    public string Key { get; set; } = "";

    [JsonPropertyName("label")]
    public string Label { get; set; } = "";

    [JsonPropertyName("status")]
    public string Status { get; set; } = "";

    [JsonPropertyName("detail")]
    public string Detail { get; set; } = "";

    [JsonPropertyName("suggestion")]
    public string Suggestion { get; set; } = "";
}

public sealed class LogPayload
{
    [JsonPropertyName("service")]
    public string Service { get; set; } = "";

    [JsonPropertyName("stream")]
    public string Stream { get; set; } = "";

    [JsonPropertyName("path")]
    public string Path { get; set; } = "";

    [JsonPropertyName("text")]
    public string Text { get; set; } = "";

    [JsonPropertyName("size")]
    public long Size { get; set; }

    [JsonPropertyName("mtime")]
    public string Mtime { get; set; } = "";

    [JsonPropertyName("next_offset")]
    public long NextOffset { get; set; }
}

public sealed class ConsoleLogsPayload
{
    [JsonPropertyName("ok")]
    public bool Ok { get; set; }

    [JsonPropertyName("time")]
    public string Time { get; set; } = "";

    [JsonPropertyName("logs")]
    public List<LogPayload> Logs { get; set; } = new();

    [JsonPropertyName("next_cursor")]
    public Dictionary<string, long> NextCursor { get; set; } = new();
}

public sealed class SupervisorActionResult
{
    public bool Ok { get; set; }
    public string RawOutput { get; set; } = "";
    public int Started { get; set; }
    public int Reused { get; set; }
    public int Errors { get; set; }
    public List<SupervisorActionItem> StartedItems { get; set; } = new();
    public List<SupervisorActionItem> ReusedItems { get; set; } = new();
    public List<SupervisorActionError> ErrorItems { get; set; } = new();
}

public sealed class SupervisorActionItem
{
    [JsonPropertyName("key")]
    public string Key { get; set; } = "";

    [JsonPropertyName("label")]
    public string Label { get; set; } = "";

    [JsonPropertyName("source")]
    public string Source { get; set; } = "";

    [JsonPropertyName("pid")]
    public int? Pid { get; set; }
}

public sealed class SupervisorActionError
{
    [JsonPropertyName("key")]
    public string Key { get; set; } = "";

    [JsonPropertyName("label")]
    public string Label { get; set; } = "";

    [JsonPropertyName("message")]
    public string Message { get; set; } = "";
}

public sealed class ProjectBackendPids
{
    [JsonPropertyName("ok")]
    public bool Ok { get; set; }

    [JsonPropertyName("pids")]
    public List<int> Pids { get; set; } = new();

    [JsonPropertyName("processes")]
    public List<ProjectBackendProcess> Processes { get; set; } = new();
}

public sealed class ProjectBackendProcess
{
    [JsonPropertyName("pid")]
    public int Pid { get; set; }

    [JsonPropertyName("key")]
    public string Key { get; set; } = "";

    [JsonPropertyName("label")]
    public string Label { get; set; } = "";
}

public sealed class LauncherConfigRoot
{
    [JsonPropertyName("ok")]
    public bool Ok { get; set; }

    [JsonPropertyName("config")]
    public LauncherConfig Config { get; set; } = new();
}

public sealed class LauncherConfig
{
    [JsonPropertyName("launcher")]
    public LauncherSection Launcher { get; set; } = new();

    [JsonPropertyName("main")]
    public MainSection Main { get; set; } = new();

    [JsonPropertyName("tts")]
    public TtsSection Tts { get; set; } = new();

    [JsonPropertyName("heygem")]
    public HeyGemSection HeyGem { get; set; } = new();
}

public sealed class LauncherSection
{
    [JsonPropertyName("port")]
    public int Port { get; set; } = 2999;

    [JsonPropertyName("open_main_after_ready")]
    public bool OpenMainAfterReady { get; set; } = false;
}

public sealed class MainSection
{
    [JsonPropertyName("base_url")]
    public string BaseUrl { get; set; } = "http://127.0.0.1:3000/";

    [JsonPropertyName("port")]
    public int Port { get; set; } = 3000;

    [JsonPropertyName("python_path")]
    public string PythonPath { get; set; } = "";

    [JsonPropertyName("script_path")]
    public string ScriptPath { get; set; } = "";
}

public sealed class TtsSection
{
    [JsonPropertyName("base_url")]
    public string BaseUrl { get; set; } = "http://127.0.0.1:7861/";

    [JsonPropertyName("port")]
    public int Port { get; set; } = 7861;

    [JsonPropertyName("root_dir")]
    public string RootDir { get; set; } = "";

    [JsonPropertyName("python_path")]
    public string PythonPath { get; set; } = "";

    [JsonPropertyName("script_path")]
    public string ScriptPath { get; set; } = "";
}

public sealed class HeyGemSection
{
    [JsonPropertyName("base_url")]
    public string BaseUrl { get; set; } = "http://127.0.0.1:7860/";

    [JsonPropertyName("port")]
    public int Port { get; set; } = 7860;

    [JsonPropertyName("api_base_url")]
    public string ApiBaseUrl { get; set; } = "http://127.0.0.1:8383/";

    [JsonPropertyName("api_port")]
    public int ApiPort { get; set; } = 8383;

    [JsonPropertyName("root_dir")]
    public string RootDir { get; set; } = "";

    [JsonPropertyName("python_path")]
    public string PythonPath { get; set; } = "";

    [JsonPropertyName("script_path")]
    public string ScriptPath { get; set; } = "";
}
