using System.ComponentModel;
using System.Diagnostics;
using System.Runtime.InteropServices;

namespace SynCanvasLauncher;

public sealed class WindowsJobObject
{
    private const int JobObjectExtendedLimitInformationClass = 9;
    private const int JobObjectLimitKillOnJobClose = 0x00002000;
    private const int ProcessTerminate = 0x0001;
    private const int ProcessSetQuota = 0x0100;
    private const int ProcessQueryLimitedInformation = 0x1000;
    private const int ErrorAccessDenied = 5;
    private const int ErrorInvalidParameter = 87;

    private readonly IntPtr _handle;

    private WindowsJobObject(IntPtr handle)
    {
        _handle = handle;
    }

    public static WindowsJobObject CreateForCurrentProcess()
    {
        var handle = CreateJobObject(IntPtr.Zero, $"SynCanvasLauncher-{Environment.ProcessId}");
        if (handle == IntPtr.Zero)
        {
            throw new Win32Exception(Marshal.GetLastWin32Error(), "Cannot create launcher job object.");
        }

        var job = new WindowsJobObject(handle);
        try
        {
            job.EnableKillOnClose();
            job.AssignCurrentProcess();
            return job;
        }
        catch
        {
            CloseHandle(handle);
            throw;
        }
    }

    public bool TryAssignProcess(int pid, out string error)
    {
        error = "";
        if (pid <= 0)
        {
            return true;
        }

        var processHandle = OpenProcess(
            ProcessTerminate | ProcessSetQuota | ProcessQueryLimitedInformation,
            false,
            pid);
        if (processHandle == IntPtr.Zero)
        {
            var code = Marshal.GetLastWin32Error();
            if (code == ErrorInvalidParameter)
            {
                return true;
            }
            error = FormatWin32Error(code, $"Cannot open process {pid}.");
            return false;
        }

        try
        {
            if (IsProcessInJob(processHandle, _handle, out var alreadyInJob) && alreadyInJob)
            {
                return true;
            }

            if (AssignProcessToJobObject(_handle, processHandle))
            {
                return true;
            }

            var code = Marshal.GetLastWin32Error();
            if (code == ErrorInvalidParameter)
            {
                return true;
            }

            error = FormatWin32Error(code, $"Cannot bind process {pid} to launcher lifecycle.");
            return false;
        }
        finally
        {
            CloseHandle(processHandle);
        }
    }

    private void AssignCurrentProcess()
    {
        using var process = Process.GetCurrentProcess();
        if (AssignProcessToJobObject(_handle, process.Handle))
        {
            return;
        }

        var code = Marshal.GetLastWin32Error();
        throw new Win32Exception(code, "Cannot bind launcher process to backend lifecycle.");
    }

    private void EnableKillOnClose()
    {
        var info = new JobObjectExtendedLimitInformation
        {
            BasicLimitInformation = new JobObjectBasicLimitInformation
            {
                LimitFlags = JobObjectLimitKillOnJobClose
            }
        };

        var length = Marshal.SizeOf<JobObjectExtendedLimitInformation>();
        var pointer = Marshal.AllocHGlobal(length);
        try
        {
            Marshal.StructureToPtr(info, pointer, false);
            if (!SetInformationJobObject(_handle, JobObjectExtendedLimitInformationClass, pointer, (uint)length))
            {
                var code = Marshal.GetLastWin32Error();
                throw new Win32Exception(code, "Cannot configure launcher job object.");
            }
        }
        finally
        {
            Marshal.FreeHGlobal(pointer);
        }
    }

    private static string FormatWin32Error(int code, string prefix)
    {
        var detail = new Win32Exception(code).Message;
        if (code == ErrorAccessDenied)
        {
            detail += " The process may already belong to another protected job.";
        }
        return $"{prefix} Win32 error {code}: {detail}";
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern IntPtr CreateJobObject(IntPtr lpJobAttributes, string? lpName);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool SetInformationJobObject(
        IntPtr hJob,
        int jobObjectInfoClass,
        IntPtr lpJobObjectInfo,
        uint cbJobObjectInfoLength);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool AssignProcessToJobObject(IntPtr hJob, IntPtr hProcess);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool IsProcessInJob(IntPtr processHandle, IntPtr jobHandle, out bool result);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern IntPtr OpenProcess(int desiredAccess, bool inheritHandle, int processId);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool CloseHandle(IntPtr handle);

    [StructLayout(LayoutKind.Sequential)]
    private struct IoCounters
    {
        public ulong ReadOperationCount;
        public ulong WriteOperationCount;
        public ulong OtherOperationCount;
        public ulong ReadTransferCount;
        public ulong WriteTransferCount;
        public ulong OtherTransferCount;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct JobObjectBasicLimitInformation
    {
        public long PerProcessUserTimeLimit;
        public long PerJobUserTimeLimit;
        public int LimitFlags;
        public UIntPtr MinimumWorkingSetSize;
        public UIntPtr MaximumWorkingSetSize;
        public int ActiveProcessLimit;
        public UIntPtr Affinity;
        public int PriorityClass;
        public int SchedulingClass;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct JobObjectExtendedLimitInformation
    {
        public JobObjectBasicLimitInformation BasicLimitInformation;
        public IoCounters IoInfo;
        public UIntPtr ProcessMemoryLimit;
        public UIntPtr JobMemoryLimit;
        public UIntPtr PeakProcessMemoryUsed;
        public UIntPtr PeakJobMemoryUsed;
    }
}
