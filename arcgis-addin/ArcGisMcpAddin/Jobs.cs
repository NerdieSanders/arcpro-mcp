using System;
using System.IO;
using System.Text;
using System.Threading;

namespace ArcGisMcpAddin
{
    public sealed class JobBusyException : InvalidOperationException
    {
        public JobBusyException(string message) : base(message) { }
    }

    /// <summary>
    /// One long-running command (execute_code or run_gp_tool). Holds the
    /// cancellation source and the latest progress reported by geoprocessing.
    /// </summary>
    public sealed class JobInfo
    {
        private readonly object _gate = new();
        private string _lastMessage = "";
        private int? _percent;

        public JobInfo(string id, string kind)
        {
            Id = id;
            Kind = kind;
            StartedUtc = DateTime.UtcNow;
        }

        public string Id { get; }
        public string Kind { get; }
        public DateTime StartedUtc { get; }
        public CancellationTokenSource Cts { get; } = new();

        /// <summary>Sentinel file the execute_code toolbox polls for cancellation.</summary>
        public string? CancelFile { get; set; }

        /// <summary>
        /// False for execute_code: cancelling the GP token there stopped the
        /// toolbox's own sentinel-file cancel from working (tested on Pro 3.7.3),
        /// so that job is cancelled through the sentinel file only.
        /// </summary>
        public bool CancelViaToken { get; set; } = true;

        public bool CancelRequested { get; set; }

        /// <summary>File execute_code tees stdout into, read for progress.</summary>
        public string? OutputFile { get; set; }

        public void OnGpEvent(string eventName, object o)
        {
            lock (_gate)
            {
                switch (eventName)
                {
                    case "OnProgressMessage":
                    case "OnMessage":
                        _lastMessage = o?.ToString() ?? "";
                        break;
                    case "OnProgressPos":
                        if (o is int position) _percent = position;
                        break;
                }
            }
        }

        public object Snapshot()
        {
            string message;
            int? percent;
            lock (_gate)
            {
                message = _lastMessage;
                percent = _percent;
            }

            string lastOutput = ReadTail(OutputFile);
            return new
            {
                job_id = Id,
                kind = Kind,
                elapsed_s = Math.Round((DateTime.UtcNow - StartedUtc).TotalSeconds, 1),
                percent,
                last_message = string.IsNullOrEmpty(lastOutput) ? message : lastOutput,
                cancel_requested = CancelRequested
            };
        }

        private static string ReadTail(string? path)
        {
            if (string.IsNullOrEmpty(path) || !File.Exists(path)) return "";
            try
            {
                using var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
                long start = Math.Max(0, stream.Length - 2048);
                stream.Seek(start, SeekOrigin.Begin);
                using var reader = new StreamReader(stream, Encoding.UTF8);
                string[] lines = reader.ReadToEnd().Split('\n', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
                return lines.Length > 0 ? lines[^1] : "";
            }
            catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
            {
                return $"(output unreadable: {ex.Message})";
            }
        }
    }

    /// <summary>
    /// Allows one long-running job at a time. A second job is refused with
    /// JobBusyException instead of queuing silently behind the first.
    /// status, job_status and cancel_job never wait on the job.
    /// </summary>
    public static class JobManager
    {
        private static readonly object Gate = new();
        private static JobInfo? _current;

        public static JobInfo Begin(string? jobId, string kind)
        {
            lock (Gate)
            {
                if (_current != null)
                {
                    throw new JobBusyException(
                        $"Job '{_current.Id}' ({_current.Kind}) is still running. Wait for it, or call cancel_job first.");
                }

                _current = new JobInfo(string.IsNullOrWhiteSpace(jobId) ? Guid.NewGuid().ToString("N")[..12] : jobId, kind);
                return _current;
            }
        }

        public static void End(JobInfo job)
        {
            lock (Gate)
            {
                if (ReferenceEquals(_current, job)) _current = null;
                job.Cts.Dispose();
            }
        }

        public static object? Current()
        {
            JobInfo? job;
            lock (Gate) job = _current;
            return job?.Snapshot();
        }

        public static object Cancel(string? jobId)
        {
            lock (Gate)
            {
                var job = _current;
                if (job == null)
                {
                    return new { cancelled = false, message = "No job is running." };
                }

                if (!string.IsNullOrEmpty(jobId) && jobId != job.Id)
                {
                    return new { cancelled = false, message = $"Job '{jobId}' is not running. The running job is '{job.Id}'." };
                }

                job.CancelRequested = true;
                if (!string.IsNullOrEmpty(job.CancelFile))
                {
                    File.WriteAllText(job.CancelFile, "cancel");
                }
                if (job.CancelViaToken)
                {
                    job.Cts.Cancel();
                }

                return new { cancelled = true, job_id = job.Id, message = "Cancel requested. The job stops at its next checkpoint." };
            }
        }
    }
}
