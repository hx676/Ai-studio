using System.Diagnostics;
using System.Globalization;
using System.Text.RegularExpressions;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Documents;
using System.Windows.Media;
using System.Windows.Threading;

namespace SynCanvasLauncher; // 更新为新品牌命名空间

public partial class MainWindow : Window
{
    // ══════════════════════════════════════════════════════════════════════════════
    // 静态只读画刷（避免运行时重复创建 SolidColorBrush 对象）
    // ══════════════════════════════════════════════════════════════════════════════
    private static readonly SolidColorBrush OkBrush = new(Color.FromRgb(16, 185, 129));
    private static readonly SolidColorBrush WarningBrush = new(Color.FromRgb(245, 158, 11));
    private static readonly SolidColorBrush ErrorBrush = new(Color.FromRgb(239, 68, 68));
    private static readonly SolidColorBrush RunningBrush = new(Color.FromRgb(59, 130, 246));
    private static readonly SolidColorBrush IdleBrush = new(Color.FromRgb(107, 114, 128));
    private static readonly SolidColorBrush TextMutedBrush = new(Color.FromRgb(142, 156, 174));
    private static readonly SolidColorBrush TextDimBrush = new(Color.FromRgb(107, 114, 128));
    private static readonly SolidColorBrush PanelBgBrush = new(Color.FromRgb(21, 22, 30));
    private static readonly SolidColorBrush PanelBorderBrush = new(Color.FromRgb(39, 41, 56));
    private static readonly SolidColorBrush LauncherStdoutBrush = new(Color.FromRgb(118, 190, 255));
    private static readonly SolidColorBrush MainServiceBrush = new(Color.FromRgb(184, 247, 194));
    private static readonly SolidColorBrush TtsServiceBrush = new(Color.FromRgb(179, 218, 255));
    private static readonly SolidColorBrush HeyGemServiceBrush = new(Color.FromRgb(230, 210, 160));
    private static readonly SolidColorBrush DefaultServiceBrush = new(Color.FromRgb(211, 211, 211));

    // ══════════════════════════════════════════════════════════════════════════════
    // 预编译正则表达式（避免运行时重复编译）
    // ══════════════════════════════════════════════════════════════════════════════
    private static readonly Regex HealthCheckInfoRegex1 = new(
        @"INFO:\s+\d+\.\d+\.\d+\.\d+:\d+\s+-\s+""GET\s+/api/digital-human/config\s+HTTP/",
        RegexOptions.IgnoreCase | RegexOptions.Compiled);
    private static readonly Regex HealthCheckInfoRegex2 = new(
        @"INFO:\s+\d+\.\d+\.\d+\.\d+:\d+\s+-\s+""GET\s+/api/digital-human/tts/status",
        RegexOptions.IgnoreCase | RegexOptions.Compiled);
    private static readonly Regex HealthCheckInfoRegex3 = new(
        @"INFO:\s+\d+\.\d+\.\d+\.\d+:\d+\s+-\s+""GET\s+/api/digital-human/media",
        RegexOptions.IgnoreCase | RegexOptions.Compiled);
    private static readonly Regex ModelProgressRegex1 = new(@"^\s*\d{1,3}%\||", RegexOptions.Compiled);
    private static readonly Regex ModelProgressRegex2 = new(@"^\s*\d+/\d+\s*\[", RegexOptions.Compiled);
    private static readonly Regex ModelProgressRegex3 = new(@"^\s*torch\.Size\(", RegexOptions.Compiled);
    private static readonly Regex ModelProgressRegex4 = new(@"^\s*\d+%\|.*\|\s*\d+/\d+", RegexOptions.Compiled);
    private static readonly Regex LogTimeBracketRegex = new(@"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", RegexOptions.Compiled);
    private static readonly Regex LogTimePlainRegex = new(@"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", RegexOptions.Compiled);

    // ══════════════════════════════════════════════════════════════════════════════
    // 静态只读集合（避免每次调用时创建新集合）
    // ══════════════════════════════════════════════════════════════════════════════
    private static readonly HashSet<string> HealthCheckNoiseSet = new(StringComparer.OrdinalIgnoreCase)
    {
        "/api/app-info",
        "/api/digital-human/task/",
        "/assets/output/digital-human/audio/",
        "/easy/query?code=0",
        "/easy/query?code=123",
        "GET /config HTTP",
        "GET /favicon.ico",
        "GET / HTTP/1.1",
        "WebSocket /ws/stats",
        "INFO:     connection open",
        "INFO:     connection closed",
        "WS Connected.",
        "WS Disconnected."
    };
    private static readonly HashSet<string> KnownWarningNoiseSet = new(StringComparer.OrdinalIgnoreCase)
    {
        "DeprecationWarning",
        "on_event is deprecated",
        "fastapi.tiangolo.com/advanced/events",
        "Read more about it in the",
        "@app.on_event",
        "PydanticDeprecatedSince20",
        "The `dict` method is deprecated",
        "Pydantic V2 Migration Guide",
        "errors.pydantic.dev",
        "GPT2InferenceModel has generative capabilities",
        "GenerationMixin",
        "trust_remote_code=True",
        "contact the model code owner",
        "Failed to load custom CUDA kernel for BigVGAN",
        "WETEXT INFO",
        "found existing fst",
        "skip building fst",
        "This is a development server",
        "Do not use it in a production deployment",
        "Press CTRL+C to quit",
        "Running on all addresses",
        "Running on http://127.0.0.1:8383",
        "Running on http://192.",
        "Use default multi mode",
        "TransDhTask init",
        "init_wh_process",
        "数字人图片处理进程启动",
        "TransDhServer服务启动",
        "Serving Flask app",
        "Debug mode: off",
        "Loading weights from",
        "Removing weight norm",
        "weights restored from",
        "TextNormalizer loaded",
        "bpe model loaded",
        "cfm loaded",
        "length_regulator loaded",
        "gpt_layer loaded",
        "Worker 1 started",
        "Use the specified emotion vector",
        "Passing a tuple of `past_key_values`",
        "past_key_values is deprecated",
        "audio_transfer",
        "drivered_video",
        "frame_re_index",
        "发送完成数据大小",
        "发送数据大小",
        "Local digital human package"
    };
    private static readonly HashSet<string> DisconnectNoiseSet = new(StringComparer.OrdinalIgnoreCase)
    {
        "Exception in callback _ProactorBasePipeTransport._call_connection_lost",
        "_call_connection_lost(None)",
        "proactor_events.py",
        "asyncio\\events.py",
        "self._sock.shutdown(socket.SHUT_RDWR)",
        "ConnectionResetError: [WinError 10054]",
        "handle: <Handle _ProactorBasePipeTransport",
        "File \"asyncio\\events.py\"",
        "File \"asyncio\\proactor_events.py\"",
        "self._context.run(self._callback",
        "f._context.run(self._callback"
    };
    private static readonly HashSet<string> TailFragmentSet = new(StringComparer.OrdinalIgnoreCase)
    {
        "HTTP/1.1\" 200 -]"
    };
    private static readonly HashSet<string> ModelProgressSet = new(StringComparer.OrdinalIgnoreCase)
    {
        "it/s]", "<?, ?it/s]"
    };

    private readonly SupervisorClient _client = new();
    private readonly DispatcherTimer _timer = new();
    private readonly DispatcherTimer _consoleTimer = new();
    private readonly TimeSpan _startupPollLimit = TimeSpan.FromMinutes(7);
    private WindowsJobObject? _backendJob;
    private SupervisorStatus? _status;
    private LauncherConfig? _config;
    private bool _isConsoleVisible;
    private bool _isConsoleRefreshing;
    private bool _consoleLogStreamingEnabled;
    private bool _isStatusRefreshing;
    private bool _hasStartAttempted;
    private bool _mainOpenedForStartAttempt;
    private DateTime? _startupPollStartedAt;
    private readonly List<ConsoleLine> _launcherEvents = new();
    private readonly List<ConsoleLine> _consoleLines = new();
    private readonly Dictionary<string, long> _consoleCursor = new();
    private int _launcherEventOrder;
    private int _consoleLineOrder;
    private bool _consoleInitialized;
    private bool _allowClose;
    private bool _isClosing;
    private bool _isStopping;
    private bool _backendBindingBlocked;
    private string? _backendBindingError;

    public MainWindow()
    {
        InitializeComponent();
        RootPathText.Text = _client.RootDir;
        InitializeBackendLifecycleJob();
        _timer.Interval = TimeSpan.FromSeconds(5);
        _timer.Tick += async (_, _) => await RefreshStatusAsync(false);
        _consoleTimer.Interval = TimeSpan.FromSeconds(1);
        _consoleTimer.Tick += async (_, _) => await RefreshConsoleAsync();
        Closing += MainWindowClosing;
        StateChanged += MainWindowStateChanged; // 绑定窗口状态改变事件，用于自适应最大化防溢出及更新图标 / Handle window state change
        Loaded += async (_, _) =>
        {
            ShowView(HomePage); // 启动时默认显示首页视图 / Show HomePage by default on startup
            await LoadConfigAsync(); // 异步加载启动器配置 / Load launcher configuration asynchronously
            await BindProjectBackendsAsync(showErrors: true);
            // 冷启动优化：启动时不再主动探测服务状态以防阻塞或耗时等待，同时关闭后台自动循环状态探测定时器 / Disable auto-probing and polling timer on load
        };
    }

    private void InitializeBackendLifecycleJob()
    {
        try
        {
            _backendJob = WindowsJobObject.CreateForCurrentProcess();
        }
        catch (Exception ex)
        {
            BlockBackendBinding($"后台进程无法绑定到启动器生命周期，请先停止残留服务后重试。{Environment.NewLine}{ex.Message}");
        }
    }

    private async Task<bool> BindProjectBackendsAsync(bool showErrors)
    {
        if (_backendJob == null)
        {
            var message = _backendBindingError
                ?? "后台进程无法绑定到启动器生命周期，请先停止残留服务后重试。";
            if (showErrors)
            {
                BlockBackendBinding(message);
            }
            return false;
        }

        try
        {
            var payload = await _client.GetProjectBackendPidsAsync();
            var conflicts = payload?.Conflicts ?? new List<ProjectBackendConflict>();
            if (conflicts.Count > 0)
            {
                var details = conflicts
                    .Select(item => $"{item.Label} PID {item.Pid}: {item.Message}")
                    .ToList();
                var message = "检测到本项目残留 HeyGem app_local.py 进程，可能占用显存或堵塞 HeyGem 内部队列。请先点击一键停止或手动结束残留进程后重试。"
                    + Environment.NewLine
                    + string.Join(Environment.NewLine, details);
                BlockBackendBinding(message);
                return false;
            }
            var pids = (payload?.Pids ?? new List<int>())
                .Concat(payload?.Processes.Select(process => process.Pid) ?? Enumerable.Empty<int>())
                .Where(pid => pid > 0 && pid != Environment.ProcessId)
                .Distinct()
                .ToList();

            var failures = new List<string>();
            foreach (var pid in pids)
            {
                if (!_backendJob.TryAssignProcess(pid, out var error))
                {
                    failures.Add($"PID {pid}: {error}");
                }
            }

            if (failures.Count > 0)
            {
                var message = "后台进程无法绑定到启动器生命周期，请先停止残留服务后重试。"
                    + Environment.NewLine
                    + string.Join(Environment.NewLine, failures);
                BlockBackendBinding(message);
                return false;
            }

            ClearBackendBindingBlock();
            return true;
        }
        catch (Exception ex)
        {
            var message = $"后台进程无法绑定到启动器生命周期，请先停止残留服务后重试。{Environment.NewLine}{ex.Message}";
            if (showErrors)
            {
                BlockBackendBinding(message);
            }
            else
            {
                _backendBindingBlocked = true;
                _backendBindingError = message;
                UpdateStartButtonBindingState();
            }
            return false;
        }
    }

    private void BlockBackendBinding(string message)
    {
        _backendBindingBlocked = true;
        _backendBindingError = message;
        UpdateStartButtonBindingState();
        ShowOperationMessage(message, true);
    }

    private void ClearBackendBindingBlock()
    {
        _backendBindingBlocked = false;
        _backendBindingError = null;
        UpdateStartButtonBindingState();
    }

    private bool HasRunningServices()
    {
        return _status?.Services.Any(s => s.State == "ready" || s.State == "starting" || s.State == "partial") == true;
    }

    private void RenderStoppedStatusAfterStop()
    {
        if (_status == null)
        {
            RenderHomeStartButton();
            return;
        }

        foreach (var service in _status.Services)
        {
            service.State = "stopped";
            service.Ready = false;
            service.Managed = false;
            service.Pid = null;
            service.Source = "none";
            foreach (var check in service.Checks)
            {
                check.Ready = false;
                check.PortOpen = false;
                check.Error = "";
            }
        }

        _status.Diagnostics = _status.Services.Select(service => new DiagnosticItem
        {
            Group = "服务接口",
            Key = service.Key,
            Label = service.Label,
            Status = "idle",
            Detail = "服务尚未启动",
            Suggestion = "点击一键启动后，启动器会等待服务完成预热。"
        }).ToList();
        _status.Counts = new Dictionary<string, int>
        {
            ["ok"] = 0,
            ["warning"] = 0,
            ["error"] = 0,
            ["running"] = 0,
            ["idle"] = _status.Services.Count
        };
        RenderStatus(_status);
    }

    private void UpdateStartButtonBindingState()
    {
        if (_isStopping)
        {
            HomePrimaryButton.IsEnabled = false;
            HomePrimaryButton.Content = "■  正在停止...";
            HomePrimaryButton.Style = (Style)FindResource("DangerButton");
            ChecksStartButton.IsEnabled = false;
            return;
        }
        var canStart = !_backendBindingBlocked;
        HomePrimaryButton.IsEnabled = canStart || HasRunningServices();
        ChecksStartButton.IsEnabled = canStart;
        if (HasRunningServices())
        {
            RenderHomeStopButton();
        }
        else
        {
            RenderHomeStartButton();
        }
    }

    private void RenderHomeStartButton()
    {
        HomePrimaryButton.Content = "▶  一键启动";
        HomePrimaryButton.Style = (Style)FindResource("PrimaryButton");
        HomePrimaryButton.Height = 42;
    }

    private void RenderHomeStopButton()
    {
        HomePrimaryButton.Content = "■  一键停止";
        HomePrimaryButton.Style = (Style)FindResource("DangerButton");
        HomePrimaryButton.Height = 42;
    }

    private async Task RefreshStatusAsync(bool includeGpu)
    {
        if (_isStatusRefreshing)
        {
            return;
        }
        try
        {
            _isStatusRefreshing = true;
            _status = await _client.GetStatusAsync(includeGpu, quick: !includeGpu);
            if (_status == null)
            {
                ShowOperationMessage("状态读取失败。", true);
                return;
            }
            RenderStatus(_status);
            TryOpenMainAfterReady();
            UpdateStartupPolling();
        }
        catch (Exception ex)
        {
            ShowOperationMessage(ex.Message, true);
            if (_startupPollStartedAt.HasValue && DateTime.Now - _startupPollStartedAt.Value >= _startupPollLimit)
            {
                StopStartupPolling(resetAttempt: false);
            }
        }
        finally
        {
            _isStatusRefreshing = false;
        }
    }

    private void BeginStartupPolling()
    {
        _hasStartAttempted = true;
        _mainOpenedForStartAttempt = false;
        _startupPollStartedAt = DateTime.Now;
        _timer.Stop();
    }

    private void StopStartupPolling(bool resetAttempt)
    {
        _timer.Stop();
        _startupPollStartedAt = null;
        if (resetAttempt)
        {
            _hasStartAttempted = false;
            _mainOpenedForStartAttempt = false;
        }
    }

    private void TryOpenMainAfterReady()
    {
        if (!_hasStartAttempted
            || _mainOpenedForStartAttempt
            || !(_config?.Launcher.OpenMainAfterReady ?? false)
            || _status?.Services.Count == 0
            || _status?.Services.All(s => s.Ready) != true)
        {
            return;
        }

        try
        {
            var mainUrl = !string.IsNullOrWhiteSpace(_status.MainUrl)
                ? _status.MainUrl
                : _config?.Main.BaseUrl ?? "";
            _client.OpenMain(mainUrl);
            _mainOpenedForStartAttempt = true;
            AddLauncherEvent("服务全部就绪，已打开主应用。", "stdout");
        }
        catch (Exception ex)
        {
            var message = $"服务已就绪，但打开主应用失败：{ex.Message}";
            AddLauncherEvent(message, "stderr");
            ShowOperationMessage(message, true);
        }
    }

    private void UpdateStartupPolling()
    {
        if (!_startupPollStartedAt.HasValue)
        {
            _timer.Stop();
            return;
        }

        var services = _status?.Services ?? new List<ServiceStatus>();
        var timedOut = DateTime.Now - _startupPollStartedAt.Value >= _startupPollLimit;
        var shouldContinue = !timedOut
            && _hasStartAttempted
            && services.Count > 0
            && !services.All(s => s.Ready);

        if (shouldContinue)
        {
            if (!_timer.IsEnabled)
            {
                _timer.Start();
            }
            return;
        }

        _timer.Stop();
        _startupPollStartedAt = null;
        if (timedOut)
        {
            _hasStartAttempted = false;
            _mainOpenedForStartAttempt = false;
            AddLauncherEvent("启动期自动刷新已到 7 分钟上限，可手动重新诊断继续查看状态。", "stderr");
        }
    }

    private async Task LoadConfigAsync()
    {
        _config = await _client.GetConfigAsync();
        if (_config == null)
        {
            return;
        }
        FillConfigForm(_config);
    }

    private void RenderStatus(SupervisorStatus status)
    {
        MainUrlText.Text = status.MainUrl;
        ConsoleServiceCards.Children.Clear();
        foreach (var service in status.Services)
        {
            ConsoleServiceCards.Children.Add(CreateConsoleServiceCard(service));
        }
        RenderTroubleshooting(status);
        RenderStatusSummary(status);
        UpdateStartButtonBindingState();
        if (_config == null && status.Config != null)
        {
            _config = status.Config;
            FillConfigForm(_config);
        }
    }

    private Border CreateDiagnosticRow(DiagnosticItem item)
    {
        // 使用预定义的静态画刷替代每次 new SolidColorBrush
        var brush = item.Status switch
        {
            "ok" => OkBrush,
            "warning" => WarningBrush,
            "error" => ErrorBrush,
            "running" => RunningBrush,
            _ => IdleBrush
        };
        var statusText = item.Status switch
        {
            "ok" => "正常",
            "warning" => "警告",
            "error" => "错误",
            "running" => "启动中",
            "idle" => "待启动",
            _ => item.Status
        };
        var panel = new StackPanel();
        panel.Children.Add(new TextBlock { Text = $"{item.Group} · {item.Label} · {statusText}", Foreground = brush, FontWeight = FontWeights.Bold, FontSize = 14 });
        panel.Children.Add(new TextBlock { Text = item.Detail, Foreground = TextMutedBrush, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 5, 0, 0), FontSize = 12.5 });
        if (!string.IsNullOrWhiteSpace(item.Suggestion))
        {
            panel.Children.Add(new TextBlock { Text = item.Suggestion, Foreground = TextDimBrush, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 4, 0, 0), FontSize = 11.5 });
        }
        return new Border
        {
            Background = PanelBgBrush,
            BorderBrush = PanelBorderBrush,
            BorderThickness = new Thickness(1),
            CornerRadius = new CornerRadius(8),
            Padding = new Thickness(14),
            Margin = new Thickness(0, 0, 0, 10),
            Child = panel
        };
    }

    private void RenderTroubleshooting(SupervisorStatus status)
    {
        TroubleList.Children.Clear();
        if (status.Services.Count > 0 && status.Services.All(s => s.State == "stopped") && !_hasStartAttempted)
        {
            TroubleSummaryText.Text = "服务尚未启动";
            TroubleHintText.Text = "当前没有故障结论。点击一键启动后，启动器会跟随刷新预热状态。";
            foreach (var service in status.Services)
            {
                TroubleList.Children.Add(CreateTroubleCard(
                    $"{ServiceDisplayName(service.Key, service.Label)} 待启动",
                    "服务还没有启动，接口未连接是正常状态。",
                    "点击一键启动后等待服务完成预热。",
                    "idle"));
            }
            return;
        }

        var actionable = BuildActionableTroubles(status).ToList();
        var problemCount = actionable.Count(item => IsProblemLevel(item.Level));
        if (actionable.Count == 0)
        {
            TroubleSummaryText.Text = "没有需要处理的问题";
            TroubleHintText.Text = "服务都能正常访问。后台细节可以在控制台或完整日志里查看。";
            TroubleList.Children.Add(CreateTroubleCard("正常", "当前没有发现会影响启动或使用的问题。", "可以直接打开主应用继续使用。", "ok"));
            return;
        }

        TroubleSummaryText.Text = problemCount == 0
            ? "服务正在预热"
            : $"发现 {problemCount} 个需要关注的问题";
        TroubleHintText.Text = problemCount == 0
            ? "保持控制台打开，等待接口陆续就绪。"
            : "优先处理启动失败、端口冲突和关键路径缺失。";
        foreach (var item in actionable)
        {
            TroubleList.Children.Add(CreateTroubleCard(item.Title, item.Detail, item.Action, item.Level));
        }
    }

    private IEnumerable<TroubleItem> BuildActionableTroubles(SupervisorStatus status)
    {
        foreach (var service in status.Services)
        {
            if (service.State == "ready")
            {
                continue;
            }
            if (service.State == "stopped" && !_hasStartAttempted)
            {
                continue;
            }
            var missing = service.Checks.Where(c => !c.Ready).Select(c => c.Label).ToList();
            var displayName = ServiceDisplayName(service.Key, service.Label);
            if (service.State == "starting")
            {
                yield return new TroubleItem(
                    $"{displayName} 正在预热",
                    missing.Count == 0 ? "服务进程已启动，正在等待接口响应。" : $"等待接口：{string.Join("、", missing)}。",
                    "保持控制台打开，等待服务完成预热。",
                    "running");
                continue;
            }
            var title = service.State == "stopped"
                ? $"{displayName} 启动后仍未就绪"
                : $"{displayName} 未完全就绪";
            var detail = missing.Count == 0
                ? "服务还没有完成启动。"
                : $"未就绪接口：{string.Join("、", missing)}。";
            var action = service.State == "partial"
                ? "点击一键启动补齐缺失的后台，或打开控制台查看启动输出。"
                : "打开控制台查看启动输出，或重新执行一键启动。";
            yield return new TroubleItem(title, detail, action, service.State == "partial" ? "warning" : "error");
        }

        foreach (var item in status.Diagnostics)
        {
            if (item.Status is "ok" or "idle" or "running")
            {
                continue;
            }
            if (IsLowPriorityDiagnostic(item))
            {
                continue;
            }
            if (item.Group.Contains("服务接口"))
            {
                continue;
            }
            if (item.Group.Contains("端口状态") && item.Status != "error")
            {
                continue;
            }
            var title = $"{item.Group} · {item.Label}";
            var action = string.IsNullOrWhiteSpace(item.Suggestion) ? "打开控制台或导出完整日志查看细节。" : item.Suggestion;
            yield return new TroubleItem(title, item.Detail, action, item.Status);
        }
    }

    private static bool IsProblemLevel(string level) => level is "warning" or "error";

    private static bool IsLowPriorityDiagnostic(DiagnosticItem item)
    {
        return item.Key.Contains("cache", StringComparison.OrdinalIgnoreCase)
            || item.Label.Contains("缓存", StringComparison.OrdinalIgnoreCase)
            || item.Label.Contains("HF", StringComparison.OrdinalIgnoreCase)
            || item.Label.Contains("Transformers", StringComparison.OrdinalIgnoreCase)
            || item.Key.Equals("port_launcher", StringComparison.OrdinalIgnoreCase)
            || item.Label.Contains("Launcher", StringComparison.OrdinalIgnoreCase)
            || item.Group.Contains("GPU", StringComparison.OrdinalIgnoreCase)
            || item.Group.Contains("磁盘空间", StringComparison.OrdinalIgnoreCase);
    }

    private void ShowOperationMessage(string message, bool isError = false)
    {
        if (string.IsNullOrWhiteSpace(message))
        {
            return;
        }

        if (ConfigPage.Visibility == Visibility.Visible)
        {
            ShowConfigMessage(message, isError);
        }
        else if (_isConsoleVisible)
        {
            AddLauncherEvent(message, isError ? "stderr" : "stdout");
        }
        else if (ChecksPage.Visibility == Visibility.Visible)
        {
            TroubleHintText.Text = message;
        }
        else
        {
            HomeActionText.Text = message;
        }
    }

    private void ShowConfigMessage(string message, bool isError = false)
    {
        ConfigStatusText.Text = message;
        ConfigStatusText.Foreground = isError ? ErrorBrush : TextMutedBrush;
    }

    private Border CreateTroubleCard(string title, string detail, string action, string level)
    {
        var brush = level switch
        {
            "ok" => OkBrush,
            "warning" => WarningBrush,
            "running" => RunningBrush,
            "idle" => new SolidColorBrush(Color.FromRgb(148, 163, 184)),
            _ => ErrorBrush
        };
        var panel = new StackPanel();
        panel.Children.Add(new TextBlock { Text = title, Foreground = brush, FontWeight = FontWeights.Bold, FontSize = 15.5 });
        panel.Children.Add(new TextBlock { Text = detail, Foreground = Brushes.White, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 8, 0, 0), FontSize = 13 });
        panel.Children.Add(new TextBlock { Text = action, Foreground = TextMutedBrush, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 6, 0, 0), FontSize = 12 });
        return new Border
        {
            Background = PanelBgBrush,
            BorderBrush = PanelBorderBrush,
            BorderThickness = new Thickness(1),
            CornerRadius = new CornerRadius(8),
            Padding = new Thickness(16),
            Margin = new Thickness(0, 0, 0, 12),
            Child = panel
        };
    }

    private Border CreateConsoleServiceCard(ServiceStatus service)
    {
        var stateText = service.State switch
        {
            "ready" => "就绪",
            "starting" => "预热中",
            "partial" => "部分就绪",
            _ => "未运行"
        };
        var stateBrush = service.State switch
        {
            "ready" => OkBrush,
            "starting" => RunningBrush,
            "partial" => WarningBrush,
            _ => IdleBrush
        };
        var source = service.Source switch
        {
            "managed" => $"PID {service.Pid}",
            "external" => "外部运行",
            "partial" => "部分接口运行",
            "warming" => "端口已打开，等待接口就绪",
            _ => "等待启动"
        };
        var checks = string.Join(" / ", service.Checks.Select(c => $"{c.Label}:{(c.Ready ? "ready" : c.PortOpen ? "端口已开" : "等待")}"));
        var logInfo = LatestLogInfo(service.Key);

        var panel = new StackPanel();
        var titleGrid = new Grid();
        titleGrid.ColumnDefinitions.Add(new ColumnDefinition());
        titleGrid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        titleGrid.Children.Add(new TextBlock { Text = ServiceDisplayName(service.Key, service.Label), FontWeight = FontWeights.Bold, FontSize = 14.5, Foreground = Brushes.White });
        var badge = new TextBlock { Text = stateText, Foreground = stateBrush, FontWeight = FontWeights.Bold, FontSize = 13 };
        Grid.SetColumn(badge, 1);
        titleGrid.Children.Add(badge);
        panel.Children.Add(titleGrid);
        panel.Children.Add(new TextBlock { Text = source, Foreground = TextMutedBrush, Margin = new Thickness(0, 8, 0, 0), FontSize = 12.5 });
        panel.Children.Add(new TextBlock { Text = checks, Foreground = TextDimBrush, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 4, 0, 0), FontSize = 11.5 });
        panel.Children.Add(new TextBlock { Text = logInfo, Foreground = TextDimBrush, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 4, 0, 0), FontSize = 11.5 });

        return new Border
        {
            Background = PanelBgBrush,
            BorderBrush = PanelBorderBrush,
            BorderThickness = new Thickness(1),
            CornerRadius = new CornerRadius(8),
            Padding = new Thickness(12),
            Margin = new Thickness(0, 0, 10, 0),
            Child = panel
        };
    }

    private void RenderStatusSummary(SupervisorStatus status)
    {
        var ready = status.Services.Count(s => s.State == "ready");
        var starting = status.Services.Count(s => s.State == "starting");
        var partial = status.Services.Count(s => s.State == "partial");
        var stopped = status.Services.Count(s => s.State == "stopped");
        var errors = status.Counts.TryGetValue("error", out var errorCount) ? errorCount : 0;
        var warnings = status.Counts.TryGetValue("warning", out var warningCount) ? warningCount : 0;

        var stateText = status.Services.All(s => s.State == "ready")
            ? "已就绪"
            : starting > 0
                ? "预热中"
                : partial > 0
                    ? "部分就绪"
                    : "未运行";

        TopStatusText.Text = $"{stateText} · 正常 {ready}/{status.Services.Count}";
        HeroReadyText.Text = stateText;
        HeroStatusText.Text = $"主应用、TTS、HeyGem 状态：就绪 {ready}，预热中 {starting}，部分就绪 {partial}，未运行 {stopped}";

        // 右下角公告面板已重构为静态使用指南，因此不再动态更新该文本 / Notice panel has been refactored to static guide, no dynamic replacement needed
        /*
        var notable = status.Diagnostics
            .Where(d => d.Status is "warning" or "error" or "running")
            .Take(4)
            .Select(d => $"{StatusLabel(d.Status)} · {d.Label}：{d.Detail}");
        NoticeText.Text = notable.Any()
            ? string.Join(Environment.NewLine + Environment.NewLine, notable)
            : "当前未发现服务异常，系统运行状态良好。可以直接点击一键启动或打开主应用。";
        */

        // 动态切换一键启动/一键停止按钮的内容与样式 / Dynamically switch the content and style of the primary button
        if (status.Services.Any(s => s.State == "ready" || s.State == "starting" || s.State == "partial"))
        {
            RenderHomeStopButton();
        }
        else
        {
            RenderHomeStartButton();
        }
    }

    private static string StatusLabel(string status) => status switch
    {
        "ok" => "正常",
        "warning" => "警告",
        "error" => "错误",
        "running" => "启动中",
        _ => status
    };

    private void FillConfigForm(LauncherConfig config)
    {
        MainBaseUrlBox.Text = config.Main.BaseUrl;
        MainPythonBox.Text = config.Main.PythonPath;
        MainScriptBox.Text = config.Main.ScriptPath;
        TtsBaseUrlBox.Text = config.Tts.BaseUrl;
        TtsRootBox.Text = config.Tts.RootDir;
        TtsPythonBox.Text = config.Tts.PythonPath;
        HeyGemBaseUrlBox.Text = config.HeyGem.BaseUrl;
        HeyGemApiUrlBox.Text = config.HeyGem.ApiBaseUrl;
        HeyGemRootBox.Text = config.HeyGem.RootDir;
        OpenMainAfterReadyBox.IsChecked = config.Launcher.OpenMainAfterReady;
    }

    private LauncherConfig ReadConfigForm()
    {
        var cfg = _config ?? new LauncherConfig();
        cfg.Main.BaseUrl = MainBaseUrlBox.Text.Trim();
        cfg.Main.PythonPath = MainPythonBox.Text.Trim();
        cfg.Main.ScriptPath = MainScriptBox.Text.Trim();
        cfg.Tts.BaseUrl = TtsBaseUrlBox.Text.Trim();
        cfg.Tts.RootDir = TtsRootBox.Text.Trim();
        cfg.Tts.PythonPath = TtsPythonBox.Text.Trim();
        cfg.Tts.ScriptPath = string.IsNullOrWhiteSpace(cfg.Tts.ScriptPath) ? System.IO.Path.Combine(cfg.Tts.RootDir, "app.py") : cfg.Tts.ScriptPath;
        cfg.HeyGem.BaseUrl = HeyGemBaseUrlBox.Text.Trim();
        cfg.HeyGem.ApiBaseUrl = HeyGemApiUrlBox.Text.Trim();
        cfg.HeyGem.RootDir = HeyGemRootBox.Text.Trim();
        cfg.HeyGem.PythonPath = string.IsNullOrWhiteSpace(cfg.HeyGem.PythonPath) ? System.IO.Path.Combine(cfg.HeyGem.RootDir, "py38", "python.exe") : cfg.HeyGem.PythonPath;
        cfg.HeyGem.ScriptPath = string.IsNullOrWhiteSpace(cfg.HeyGem.ScriptPath) ? System.IO.Path.Combine(cfg.HeyGem.RootDir, "app.py") : cfg.HeyGem.ScriptPath;
        cfg.Launcher.OpenMainAfterReady = OpenMainAfterReadyBox.IsChecked == true;
        return cfg;
    }

    private void ShowPage(StackPanel page, string title)
    {
        throw new NotSupportedException("Use ShowView instead.");
    }

    private void ShowView(UIElement page)
    {
        HomePage.Visibility = Visibility.Collapsed;
        ChecksPage.Visibility = Visibility.Collapsed;
        ConfigPage.Visibility = Visibility.Collapsed;
        LogsPage.Visibility = Visibility.Collapsed;
        _isConsoleVisible = page == LogsPage;
        if (_isConsoleVisible)
        {
            EnsureConsoleInitialized();
        }
        else
        {
            _consoleTimer.Stop();
        }
        page.Visibility = Visibility.Visible;
    }

    private void NavigateHome(object sender, RoutedEventArgs e) => ShowView(HomePage); // 导航至首页视图 / Navigate to HomePage view
    private void NavigateChecks(object sender, RoutedEventArgs e)
    {
        ShowView(ChecksPage); // 显示疑难解答页 / Show troubleshooting page
    }
    private void NavigateConfig(object sender, RoutedEventArgs e) => ShowView(ConfigPage); // 导航至设置页 / Navigate to ConfigPage
    private async void NavigateLogs(object sender, RoutedEventArgs e) => await OpenConsoleAsync(false);

    private async Task OpenConsoleAsync(bool startLogSession)
    {
        ShowView(LogsPage); // 显示控制台日志页 / Show console log page
        if (startLogSession || !_consoleLogStreamingEnabled)
        {
            await ResetConsoleViewToCurrentEndAsync(); // 进入控制台时从当前日志末尾开始显示 / Start from current log end when entering console
        }
        if (startLogSession)
        {
            _consoleLogStreamingEnabled = true;
        }
        if (_consoleLogStreamingEnabled)
        {
            _consoleTimer.Start();
        }
    }

    private void MinimizeWindowClicked(object sender, RoutedEventArgs e)
    {
        WindowState = WindowState.Minimized; // 最小化窗口 / Minimize the window
    }

    private void MaximizeWindowClicked(object sender, RoutedEventArgs e)
    {
        if (WindowState == WindowState.Maximized)
        {
            WindowState = WindowState.Normal; // 向下还原窗口 / Restore the window
        }
        else
        {
            WindowState = WindowState.Maximized; // 最大化窗口 / Maximize the window
        }
    }

    private void CloseWindowClicked(object sender, RoutedEventArgs e)
    {
        Close(); // 关闭窗口，将会自动触发 MainWindowClosing 以清理后台进程 / Close window
    }

    private void MainWindowStateChanged(object? sender, EventArgs e)
    {
        if (WindowState == WindowState.Maximized)
        {
            MaximizeButton.Content = "❐"; // 最大化状态下更新按钮图标为重叠双框 / Show restore icon
            MainGrid.Margin = new Thickness(8); // 加上 8px 的外边框安全间距，防止操作系统最大化行为导致的边缘内容截断 / Add margin to prevent native border overflow
        }
        else if (WindowState == WindowState.Normal)
        {
            MaximizeButton.Content = "☐"; // 正常状态下更新按钮图标为单方框 / Show maximize icon
            MainGrid.Margin = new Thickness(0); // 恢复 0px 正常边距 / Reset margin
        }
    }

    private async void RefreshClicked(object sender, RoutedEventArgs e)
    {
        await RefreshStatusAsync(true);
        await BindProjectBackendsAsync(showErrors: true);
    }

    private async void StartAllClicked(object sender, RoutedEventArgs e)
    {
        try
        {
            // 如果是首页按钮，且当前有任何服务在运行，则点击时执行“一键停止”逻辑 / If it's the home button and any service is running, perform "One-click Stop"
            if (sender == HomePrimaryButton && _status?.Services.Any(s => s.State == "ready" || s.State == "starting" || s.State == "partial") == true)
            {
                StopAllClicked(sender, e); // 调用一键停止 / Call one-click stop
                return;
            }
            // 点击启动后，立即跳转至控制台页面，以便直观展现各项服务实时加载的日志流 / Navigate to logs view immediately to show startup console output
            if (!await BindProjectBackendsAsync(showErrors: true))
            {
                return;
            }
            await OpenConsoleAsync(true);
            HomeActionText.Text = "正在发送启动命令...";
            AddLauncherEvent("正在启动全部后台服务。", "stdout");
            var mainPreferredUrl = !string.IsNullOrWhiteSpace(_status?.MainUrl)
                ? _status.MainUrl
                : !string.IsNullOrWhiteSpace(_config?.Main.BaseUrl)
                    ? _config.Main.BaseUrl
                    : MainBaseUrlBox.Text.Trim();
            AddLauncherEvent($"主应用首选地址 {mainPreferredUrl}，冲突时自动选择 3001-3099；TTS 端口 7861，HeyGem 页面端口 7860，HeyGem 接口端口 8383。", "stdout");
            AddExpectedServiceEvents();
            RenderConsoleSnapshot();
            BeginStartupPolling();
            var result = await _client.StartAsync();
            if (!await BindProjectBackendsAsync(showErrors: true))
            {
                await RefreshStatusAsync(false);
                return;
            }
            var message = result.Ok
                ? $"启动命令已完成：新启动 {result.Started}，复用 {result.Reused}，错误 {result.Errors}。详细输出已写入日志。"
                : "启动命令失败，详细输出已写入日志。";
            AddActionResultEvents("启动", result);
            HomeActionText.Text = message;
            await RefreshStatusAsync(false);
            await BindProjectBackendsAsync(showErrors: false);
            AddStatusEvents(_status);
            if (_isConsoleVisible)
            {
                await RefreshConsoleAsync();
            }
        }
        catch (Exception ex)
        {
            HomeActionText.Text = ex.Message;
            await RefreshStatusAsync(false);
        }
    }

    private async void StopAllClicked(object sender, RoutedEventArgs e)
    {
        if (await StopAllClickedFastAsync())
        {
            return;
        }

        try
        {
            StopStartupPolling(resetAttempt: true);
            _consoleLogStreamingEnabled = false;
            _consoleTimer.Stop();
            HomeActionText.Text = "正在停止本次启动的服务...";
            AddLauncherEvent("正在停止本次启动器拉起的后台服务。", "stdout");
            RenderConsoleSnapshot();
            var result = await StopBackendsWithUiTimeoutAsync("停止", TimeSpan.FromSeconds(12));
            var message = result.Ok ? "停止命令已完成，所有本项目后台已清理。" : "停止命令失败，详细输出已写入日志。";
            HomeActionText.Text = message;
            await RefreshStatusAsync(false);
            await BindProjectBackendsAsync(showErrors: false);
            AddStatusEvents(_status);
            if (_isConsoleVisible)
            {
                await RefreshConsoleAsync();
            }
        }
        catch (Exception ex)
        {
            HomeActionText.Text = ex.Message;
        }
    }

    private async Task<bool> StopAllClickedFastAsync()
    {
        if (_isStopping)
        {
            return true;
        }

        try
        {
            _isStopping = true;
            StopStartupPolling(resetAttempt: true);
            _consoleLogStreamingEnabled = false;
            _consoleTimer.Stop();
            UpdateStartButtonBindingState();
            AddLauncherEvent("正在停止本次启动器拉起的后台服务。", "stdout");
            RenderConsoleSnapshot();

            var result = await StopBackendsAsync("停止");
            string? confirmError = null;
            var leftoverPids = new List<int>();
            try
            {
                var leftovers = await _client.GetProjectBackendPidsAsync();
                leftoverPids = leftovers?.Pids?.Where(pid => pid > 0).Distinct().ToList() ?? new List<int>();
            }
            catch (Exception ex)
            {
                confirmError = ex.Message;
            }

            if (result.Ok && confirmError == null && leftoverPids.Count == 0)
            {
                AddLauncherEvent("停止完成，未发现本项目后台残留进程。", "stdout");
                ShowOperationMessage("停止完成，未发现本项目后台残留进程。");
                RenderStoppedStatusAfterStop();
            }
            else
            {
                if (leftoverPids.Count == 0)
                {
                    RenderStoppedStatusAfterStop();
                }
                var message = leftoverPids.Count > 0
                    ? $"停止未完全完成，仍发现本项目后台 PID：{string.Join(", ", leftoverPids)}。请稍后重试或关闭启动器触发生命周期清理。"
                    : confirmError != null
                        ? $"停止命令已返回，但无法确认残留进程：{confirmError}"
                        : result.TimedOut
                            ? "停止命令超时，按钮已恢复。若仍有残留服务，请稍后重试或关闭启动器触发生命周期清理。"
                            : "停止命令失败，按钮已恢复。若仍有残留服务，请稍后重试或关闭启动器触发生命周期清理。";
                AddLauncherEvent(message, "stderr");
                ShowOperationMessage(message, true);
            }
        }
        catch (Exception ex)
        {
            var message = $"停止失败：{ex.Message}";
            AddLauncherEvent(message, "stderr");
            ShowOperationMessage(message, true);
        }
        finally
        {
            _isStopping = false;
            if (_status == null || !HasRunningServices())
            {
                RenderHomeStartButton();
            }
            UpdateStartButtonBindingState();
        }

        return true;
    }

    private async void MainWindowClosing(object? sender, System.ComponentModel.CancelEventArgs e)
    {
        if (_allowClose)
        {
            return;
        }
        if (_isClosing)
        {
            e.Cancel = true;
            return;
        }
        var answer = MessageBox.Show(
            this,
            "启动器将退出，并停止主应用、TTS、HeyGem 等所有本项目后台服务。确定要关闭当前实例吗？",
            "关闭",
            MessageBoxButton.YesNo,
            MessageBoxImage.Question,
            MessageBoxResult.No);
        if (answer != MessageBoxResult.Yes)
        {
            e.Cancel = true;
            return;
        }
        e.Cancel = true;
        _isClosing = true;
        try
        {
            _timer.Stop();
            _consoleTimer.Stop();
            AddLauncherEvent("正在关闭启动器，先清理所有本项目后台服务。", "stdout");
            RenderConsoleSnapshot();
            HomeActionText.Text = "正在关闭后台服务...";
            await StopBackendsWithUiTimeoutAsync("关闭启动器", TimeSpan.FromSeconds(12));
        }
        finally
        {
            _allowClose = true;
            Close();
        }
    }

    private async Task<SupervisorActionResult> StopBackendsWithUiTimeoutAsync(string reason, TimeSpan timeout)
    {
        var task = StopBackendsAsync(reason);
        var completed = await Task.WhenAny(task, Task.Delay(timeout));
        if (completed == task)
        {
            return await task;
        }

        _ = task.ContinueWith(async completedTask =>
        {
            var stream = "stderr";
            var message = $"{reason}：后台停止命令稍后才返回。";
            try
            {
                var result = await completedTask;
                stream = result.Ok ? "stdout" : "stderr";
                message = result.Ok
                    ? $"{reason}：后台停止命令稍后返回，清理完成。"
                    : $"{reason}：后台停止命令稍后返回，但报告失败。";
            }
            catch (Exception ex)
            {
                message = $"{reason}：后台停止命令稍后失败：{ex.Message}";
            }

            try
            {
                await Dispatcher.InvokeAsync(() => AddLauncherEvent(message, stream));
            }
            catch
            {
                // The window may already be closing.
            }
        }, TaskScheduler.Default);

        AddLauncherEvent($"{reason}：后台停止命令超过 {timeout.TotalSeconds:0} 秒，按钮先恢复，稍后继续确认残留进程。", "stderr");
        return new SupervisorActionResult
        {
            Ok = false,
            TimedOut = true,
            RawOutput = $"Launcher UI timeout after {timeout.TotalSeconds:0}s."
        };
    }

    private async Task<SupervisorActionResult> StopBackendsAsync(string reason)
    {
        var result = await _client.StopAsync();
        AddLauncherEvent(result.Ok
            ? $"{reason}：后台清理完成。"
            : $"{reason}：后台清理失败，请导出完整日志查看。", result.Ok ? "stdout" : "stderr");
        return result;
    }

    private async void SaveConfigClicked(object sender, RoutedEventArgs e)
    {
        try
        {
            _config = ReadConfigForm();
            var output = await _client.SaveConfigAsync(_config);
            ShowConfigMessage("配置已保存，并已同步数字人服务配置。");
            await LoadConfigAsync();
            await RefreshStatusAsync(false);
        }
        catch (Exception ex)
        {
            ShowConfigMessage(ex.Message, true);
        }
    }

    private async void ReloadConfigClicked(object sender, RoutedEventArgs e)
    {
        await LoadConfigAsync();
        ShowConfigMessage("配置已重新读取。");
    }

    private async void ReadLogClicked(object sender, RoutedEventArgs e)
    {
        EnsureConsoleInitialized();
        if (!_consoleLogStreamingEnabled)
        {
            await ResetConsoleViewToCurrentEndAsync();
            return;
        }
        await RefreshConsoleAsync();
    }

    private async void ClearConsoleClicked(object sender, RoutedEventArgs e)
    {
        await ResetConsoleViewToCurrentEndAsync();
        if (_consoleLogStreamingEnabled && _isConsoleVisible)
        {
            _consoleTimer.Start();
        }
    }

    private void ExportLogsClicked(object sender, RoutedEventArgs e)
    {
        try
        {
            var zipPath = _client.ExportFullLogs();
            ShowOperationMessage($"完整日志已导出：{zipPath}");
            Process.Start(new ProcessStartInfo
            {
                FileName = System.IO.Path.GetDirectoryName(zipPath) ?? _client.LogDir,
                UseShellExecute = true
            });
        }
        catch (Exception ex)
        {
            ShowOperationMessage(ex.Message, true);
        }
    }

    private void OpenMainClicked(object sender, RoutedEventArgs e)
    {
        var url = _status?.MainUrl ?? MainBaseUrlBox.Text;
        if (!string.IsNullOrWhiteSpace(url))
        {
            _client.OpenMain(url);
        }
    }

    private void OpenRootClicked(object sender, RoutedEventArgs e) => _client.OpenRoot();
    private void OpenLogsDirClicked(object sender, RoutedEventArgs e) => _client.OpenLogs();
    private void OpenInputDirClicked(object sender, RoutedEventArgs e) => OpenDirectoryAction(_client.OpenInput);
    private void OpenOutputDirClicked(object sender, RoutedEventArgs e) => OpenDirectoryAction(_client.OpenOutput);
    private void OpenVoicesDirClicked(object sender, RoutedEventArgs e) => OpenDirectoryAction(_client.OpenVoices);
    private void OpenHeyGemOutputDirClicked(object sender, RoutedEventArgs e) => OpenDirectoryAction(_client.OpenHeyGemOutput);

    private void OpenDirectoryAction(Action open)
    {
        try
        {
            open();
        }
        catch (Exception ex)
        {
            ShowOperationMessage(ex.Message, true);
        }
    }

    private async Task RefreshConsoleAsync()
    {
        if (_isConsoleRefreshing)
        {
            return;
        }
        if (!_isConsoleVisible) return;
        if (!_consoleLogStreamingEnabled) return;
        try
        {
            _isConsoleRefreshing = true;
            if (_status == null)
            {
                await RefreshStatusAsync(false);
            }
            if (_consoleCursor.Count == 0)
            {
                await RefreshConsoleCursorToEndAsync();
                return;
            }
            var payload = await _client.ReadConsoleLogsAsync(_consoleInitialized ? _consoleCursor : null);
            if (payload?.Logs == null)
            {
                AppendConsoleLines(new List<ConsoleLine> { new(null, _consoleLineOrder++, "", "", "暂无日志") });
                return;
            }
            if (payload.NextCursor.Count > 0)
            {
                _consoleCursor.Clear();
                foreach (var item in payload.NextCursor)
                {
                    _consoleCursor[item.Key] = item.Value;
                }
            }
            AppendConsoleLines(BuildConsoleTimeline(payload.Logs));
            _consoleInitialized = true;
        }
        catch (Exception ex)
        {
            AddLauncherEvent(ex.Message, "stderr");
            AppendConsoleLines(new List<ConsoleLine>());
        }
        finally
        {
            _isConsoleRefreshing = false;
        }
    }

    private async Task ResetConsoleViewToCurrentEndAsync()
    {
        _consoleTimer.Stop();
        _launcherEvents.Clear();
        _consoleLines.Clear();
        _consoleCursor.Clear();
        _consoleInitialized = false;
        EnsureConsoleInitialized();
        await RefreshConsoleCursorToEndAsync();
        ConsoleTextBox.ScrollToEnd();
    }

    private async Task RefreshConsoleCursorToEndAsync()
    {
        try
        {
            var payload = await _client.ReadConsoleLogsAsync();
            if (payload?.NextCursor == null)
            {
                return;
            }
            _consoleCursor.Clear();
            foreach (var item in payload.NextCursor)
            {
                _consoleCursor[item.Key] = item.Value;
            }
        }
        catch (Exception ex)
        {
            ShowOperationMessage(ex.Message, true);
        }
    }

    private void AddExpectedServiceEvents()
    {
        foreach (var service in new[]
        {
            ("main", "主应用", "http://127.0.0.1:3000/"),
            ("tts", "TTS", "http://127.0.0.1:7861/"),
            ("heygem", "HeyGem", "http://127.0.0.1:7860/ 和 http://127.0.0.1:8383/")
        })
        {
            AddLauncherEvent($"准备检查/启动 {service.Item2}：{service.Item3}", "stdout", service.Item1);
        }
    }

    private void AddActionResultEvents(string action, SupervisorActionResult result)
    {
        foreach (var item in result.StartedItems)
        {
            AddLauncherEvent($"{action}：{ServiceDisplayName(item.Key, item.Label)} 已启动，PID {item.Pid}", "stdout", item.Key);
        }
        foreach (var item in result.ReusedItems)
        {
            var source = item.Source == "tracked" ? "启动器托管进程" : "已存在服务";
            AddLauncherEvent($"{action}：{ServiceDisplayName(item.Key, item.Label)} 复用{source}，PID {item.Pid}", "stdout", item.Key);
        }
        foreach (var item in result.ErrorItems)
        {
            AddLauncherEvent($"{action}：{ServiceDisplayName(item.Key, item.Label)} 失败：{item.Message}", "stderr", item.Key);
        }
        AddLauncherEvent($"{action}命令完成：新启动 {result.Started}，复用 {result.Reused}，错误 {result.Errors}。", result.Ok ? "stdout" : "stderr");
    }

    private void AddStatusEvents(SupervisorStatus? status)
    {
        if (status == null)
        {
            return;
        }
        foreach (var service in status.Services)
        {
            var checks = string.Join(" / ", service.Checks.Select(check => $"{check.Label}:{(check.Ready ? "ready" : check.PortOpen ? "端口已开，等待接口" : "等待")}"));
            var pidText = service.Pid.HasValue ? $"，PID {service.Pid}" : "";
            AddLauncherEvent($"{ServiceDisplayName(service.Key, service.Label)} 状态：{StateDisplayName(service.State)}{pidText}；{checks}", service.Ready ? "stdout" : "stderr", service.Key);
        }
    }

    private void AddLauncherEvent(string message, string stream = "stdout", string service = "launcher")
    {
        var line = new ConsoleLine(DateTime.Now, _launcherEventOrder++, service, stream, $"[{DateTime.Now:HH:mm:ss}][{service}] {message}");
        _launcherEvents.Add(line);
        if (_launcherEvents.Count > 160)
        {
            _launcherEvents.RemoveRange(0, _launcherEvents.Count - 160);
        }
        if (_isConsoleVisible)
        {
            AppendConsoleLines(new List<ConsoleLine> { line });
        }
    }

    private void RenderConsoleSnapshot()
    {
        if (!_isConsoleVisible)
        {
            return;
        }
        EnsureConsoleInitialized();
        AppendConsoleLines(new List<ConsoleLine>());
        ConsoleTextBox.ScrollToEnd();
    }

    private List<ConsoleLine> MergeConsoleLines(List<ConsoleLine> logLines)
    {
        var merged = _launcherEvents.Concat(logLines).ToList();
        if (merged.Count == 0)
        {
            merged.Add(new ConsoleLine(null, 0, "", "", "暂无关键日志。点击一键启动后会显示启动进度；完整原始日志可用“导出完整日志”查看。"));
            return merged;
        }
        return merged
            .OrderBy(row => row.Time ?? DateTime.MaxValue)
            .ThenBy(row => row.Order)
            .TakeLast(900)
            .ToList();
    }

    private void EnsureConsoleInitialized()
    {
        if (_consoleInitialized)
        {
            return;
        }
        _consoleInitialized = true;
        ConsoleTextBox.Document.Blocks.Clear();
        ConsoleTextBox.Document.Blocks.Add(new Paragraph { Margin = new Thickness(0), LineHeight = 16 });
    }

    private static List<ConsoleLine> BuildConsoleTimeline(IEnumerable<LogPayload> logs)
    {
        var rows = new List<ConsoleLine>();
        var order = 0;
        foreach (var log in logs)
        {
            var text = log.Text ?? "";
            if (string.IsNullOrWhiteSpace(text))
            {
                continue;
            }
            foreach (var raw in text.Replace("\r\n", "\n").Replace('\r', '\n').Split('\n'))
            {
                var line = raw.TrimEnd();
                if (string.IsNullOrWhiteSpace(line))
                {
                    continue;
                }
                if (ShouldHideConsoleLine(log.Service, log.Stream, line))
                {
                    continue;
                }
                rows.Add(new ConsoleLine(TryParseLogTime(line), order++, log.Service, log.Stream, $"[{log.Service}][{log.Stream}] {line}"));
            }
        }

        return rows
            .OrderBy(row => row.Time ?? DateTime.MaxValue)
            .ThenBy(row => row.Order)
            .TakeLast(1000)
            .ToList();
    }

    private void AppendConsoleLines(List<ConsoleLine> lines)
    {
        EnsureConsoleInitialized();
        if (lines.Count == 0)
        {
            if (_consoleLines.Count == 0)
            {
                lines.Add(new ConsoleLine(null, _consoleLineOrder++, "", "", "暂无关键日志。完整原始日志可用“导出完整日志”查看。"));
            }
            else
            {
                return;
            }
        }
        var paragraph = ConsoleTextBox.Document.Blocks.OfType<Paragraph>().FirstOrDefault();
        if (paragraph == null)
        {
            paragraph = new Paragraph { Margin = new Thickness(0), LineHeight = 16 };
            ConsoleTextBox.Document.Blocks.Add(paragraph);
        }
        foreach (var line in lines)
        {
            if (_consoleLines.Count > 0 && line.Text.StartsWith("暂无关键日志。", StringComparison.OrdinalIgnoreCase))
            {
                continue;
            }
            _consoleLines.Add(line);
            paragraph.Inlines.Add(new Run(line.Text + Environment.NewLine) { Foreground = ConsoleLineBrush(line) });
        }
        TrimConsoleLines(paragraph);
        ConsoleTextBox.ScrollToEnd();
    }

    private void TrimConsoleLines(Paragraph paragraph)
    {
        const int maxLines = 1000;
        var extra = _consoleLines.Count - maxLines;
        if (extra <= 0)
        {
            return;
        }
        _consoleLines.RemoveRange(0, extra);
        for (var index = 0; index < extra && paragraph.Inlines.FirstInline != null; index++)
        {
            paragraph.Inlines.Remove(paragraph.Inlines.FirstInline);
        }
    }

    private void SetConsoleText(List<ConsoleLine> lines)
    {
        ConsoleTextBox.Document.Blocks.Clear();
        var paragraph = new Paragraph { Margin = new Thickness(0), LineHeight = 16 };
        if (lines.Count == 0)
        {
            lines.Add(new ConsoleLine(null, 0, "", "", "暂无日志"));
        }
        foreach (var line in lines)
        {
            var run = new Run(line.Text + Environment.NewLine)
            {
                Foreground = ConsoleLineBrush(line)
            };
            paragraph.Inlines.Add(run);
        }
        ConsoleTextBox.Document.Blocks.Add(paragraph);
    }

    private static Brush ConsoleLineBrush(ConsoleLine line)
    {
        if (line.Service == "launcher")
        {
            return line.Stream == "stderr" ? Brushes.IndianRed : LauncherStdoutBrush;
        }
        if (line.Stream == "stderr")
        {
            return line.Text.Contains("ERROR", StringComparison.OrdinalIgnoreCase) || line.Text.Contains("Traceback", StringComparison.OrdinalIgnoreCase)
                ? Brushes.IndianRed
                : Brushes.DarkOrange;
        }
        if (line.Text.Contains("ERROR", StringComparison.OrdinalIgnoreCase))
        {
            return Brushes.IndianRed;
        }
        return line.Service switch
        {
            "main" => MainServiceBrush,
            "tts" => TtsServiceBrush,
            "heygem" => HeyGemServiceBrush,
            _ => DefaultServiceBrush
        };
    }

    private static bool ShouldHideConsoleLine(string service, string stream, string line)
    {
        var text = line.Trim();
        if (string.IsNullOrWhiteSpace(text)) return true;
        if (LooksLikeTailFragment(text)) return true;
        if (IsHealthCheckNoise(service, text)) return true;
        if (IsKnownWarningNoise(text)) return true;
        if (IsModelProgressNoise(text)) return true;
        if (stream == "stderr" && IsDisconnectTraceNoise(text)) return true;
        return false;
    }

    private static bool IsHealthCheckNoise(string service, string text)
    {
        foreach (var prefix in HealthCheckNoiseSet)
        {
            if (text.Contains(prefix, StringComparison.OrdinalIgnoreCase)) return true;
        }
        return HealthCheckInfoRegex1.IsMatch(text)
            || HealthCheckInfoRegex2.IsMatch(text)
            || HealthCheckInfoRegex3.IsMatch(text);
    }

    private static bool IsKnownWarningNoise(string text)
    {
        foreach (var keyword in KnownWarningNoiseSet)
        {
            if (text.Contains(keyword, StringComparison.OrdinalIgnoreCase)) return true;
        }
        return false;
    }

    private static bool IsModelProgressNoise(string text)
    {
        if (ModelProgressRegex1.IsMatch(text) || ModelProgressRegex2.IsMatch(text)
            || ModelProgressRegex3.IsMatch(text) || ModelProgressRegex4.IsMatch(text)) return true;
        foreach (var suffix in ModelProgressSet)
        {
            if (text.Contains(suffix, StringComparison.OrdinalIgnoreCase)) return true;
        }
        return false;
    }

    private static bool IsDisconnectTraceNoise(string text)
    {
        foreach (var noise in DisconnectNoiseSet)
        {
            if (text.Contains(noise, StringComparison.OrdinalIgnoreCase)) return true;
        }
        return false;
    }

    private static bool LooksLikeTailFragment(string text)
    {
        if (text.StartsWith("[") || text.StartsWith("INFO:", StringComparison.OrdinalIgnoreCase) || text.StartsWith("WARNING", StringComparison.OrdinalIgnoreCase))
        {
            return false;
        }
        return text.Contains("HTTP/1.1\" 200 -]", StringComparison.OrdinalIgnoreCase)
            || text.StartsWith("uery?code=", StringComparison.OrdinalIgnoreCase);
    }

    private static DateTime? TryParseLogTime(string line)
    {
        var bracket = Regex.Match(line, @"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})");
        if (bracket.Success && DateTime.TryParseExact(bracket.Groups[1].Value, "yyyy-MM-dd HH:mm:ss", CultureInfo.InvariantCulture, DateTimeStyles.None, out var bracketTime))
        {
            return bracketTime;
        }
        var plain = Regex.Match(line, @"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})");
        if (plain.Success && DateTime.TryParseExact(plain.Groups[1].Value, "yyyy-MM-dd HH:mm:ss", CultureInfo.InvariantCulture, DateTimeStyles.None, out var plainTime))
        {
            return plainTime;
        }
        return null;
    }

    private string LatestLogInfo(string service)
    {
        var dir = System.IO.Path.Combine(_client.LogDir);
        var outPath = System.IO.Path.Combine(dir, $"{service}.out.log");
        var errPath = System.IO.Path.Combine(dir, $"{service}.err.log");
        var times = new[] { outPath, errPath }
            .Where(System.IO.File.Exists)
            .Select(path => System.IO.File.GetLastWriteTime(path))
            .ToList();
        return times.Count == 0 ? "日志：暂无" : $"日志：{times.Max():yyyy-MM-dd HH:mm:ss}";
    }

    private static string ServiceDisplayName(string key, string fallback) => key switch
    {
        "main" => "主应用",
        "tts" => "TTS",
        "heygem" => "HeyGem",
        _ => fallback
    };

    private static string StateDisplayName(string state) => state switch
    {
        "ready" => "就绪",
        "starting" => "预热中",
        "partial" => "部分就绪",
        "stopped" => "未启动",
        _ => state
    };

    private sealed record ConsoleLine(DateTime? Time, int Order, string Service, string Stream, string Text);
    private sealed record TroubleItem(string Title, string Detail, string Action, string Level);
}
