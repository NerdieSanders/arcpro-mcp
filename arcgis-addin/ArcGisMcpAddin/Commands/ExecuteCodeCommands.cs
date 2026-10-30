using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using System.Text.Json;
using System.Threading.Tasks;
using ArcGIS.Desktop.Core.Geoprocessing;

namespace ArcGisMcpAddin.Commands
{
    /// <summary>
    /// Runs Python code inside ArcGIS Pro through the bundled ArcProMcp.pyt
    /// toolbox, so arcpy.mp.ArcGISProject("CURRENT") works. Each run is a job:
    /// it can be watched with job_status and stopped with cancel_job.
    /// </summary>
    public static class ExecuteCodeCommands
    {
        private const string ToolboxResource = "ArcGisMcpAddin.Toolbox.ArcProMcp.pyt";
        private static readonly UTF8Encoding Utf8NoBom = new(false);

        private static string RootFolder =>
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "ArcProMcp");

        public static async Task<object> ExecuteCodeAsync(string code, string? jobId)
        {
            var job = JobManager.Begin(jobId, "execute_code");
            try
            {
                string toolbox = EnsureToolbox();
                RemoveOldJobFolders();

                string jobDir = Path.Combine(RootFolder, "jobs", job.Id);
                Directory.CreateDirectory(jobDir);
                string codePath = Path.Combine(jobDir, "code.py");
                string resultPath = Path.Combine(jobDir, "result.json");
                string stdoutPath = Path.Combine(jobDir, "stdout.txt");
                job.CancelFile = Path.Combine(jobDir, "cancel");
                job.CancelViaToken = false;
                job.OutputFile = stdoutPath;
                File.WriteAllText(codePath, code, Utf8NoBom);

                var values = Geoprocessing.MakeValueArray(codePath, resultPath, job.CancelFile, stdoutPath);
                var gpMessages = new List<string>();
                try
                {
                    IGPResult gp = await Geoprocessing.ExecuteToolAsync(
                        Path.Combine(toolbox, "ExecuteCode"),
                        values,
                        null,
                        job.Cts.Token,
                        job.OnGpEvent,
                        GPExecuteToolFlags.None);
                    gpMessages = gp.Messages?.Select(m => $"[{m.Type}] {m.Text}").ToList() ?? gpMessages;
                }
                catch (OperationCanceledException)
                {
                    gpMessages.Add("[Cancel] Geoprocessing reported the run as cancelled.");
                }

                if (!File.Exists(resultPath))
                {
                    // The toolbox never got to write a result: cancelled before
                    // the code started, or the tool itself failed to load.
                    return new
                    {
                        job_id = job.Id,
                        ok = false,
                        cancelled = job.CancelRequested,
                        error = job.CancelRequested
                            ? "Cancelled before the code finished."
                            : "The ExecuteCode tool did not produce a result.",
                        gp_messages = gpMessages
                    };
                }

                using var doc = JsonDocument.Parse(File.ReadAllText(resultPath, Encoding.UTF8));
                var r = doc.RootElement;
                return new
                {
                    job_id = job.Id,
                    ok = r.GetProperty("ok").GetBoolean(),
                    cancelled = r.GetProperty("cancelled").GetBoolean(),
                    error = r.GetProperty("error").ValueKind == JsonValueKind.Null ? null : r.GetProperty("error").GetString(),
                    traceback = r.GetProperty("traceback").ValueKind == JsonValueKind.Null ? null : r.GetProperty("traceback").GetString(),
                    stdout = r.GetProperty("stdout").GetString(),
                    stdout_truncated = r.GetProperty("stdout_truncated").GetBoolean(),
                    stdout_path = stdoutPath,
                    code_elapsed_s = r.GetProperty("elapsed_s").GetDouble(),
                    gp_messages = gpMessages
                };
            }
            finally
            {
                JobManager.End(job);
            }
        }

        /// <summary>
        /// Writes the embedded toolbox to disk when it is missing or out of date.
        /// Returns the .pyt path.
        /// </summary>
        private static string EnsureToolbox()
        {
            string folder = Path.Combine(RootFolder, "toolbox");
            Directory.CreateDirectory(folder);
            string path = Path.Combine(folder, "ArcProMcp.pyt");

            using var stream = Assembly.GetExecutingAssembly().GetManifestResourceStream(ToolboxResource)
                ?? throw new InvalidOperationException($"Embedded resource '{ToolboxResource}' is missing from the add-in.");
            using var reader = new StreamReader(stream, Encoding.UTF8);
            string source = reader.ReadToEnd();

            if (!File.Exists(path) || File.ReadAllText(path, Encoding.UTF8) != source)
            {
                File.WriteAllText(path, source, Utf8NoBom);
            }

            return path;
        }

        private static void RemoveOldJobFolders()
        {
            var jobs = new DirectoryInfo(Path.Combine(RootFolder, "jobs"));
            if (!jobs.Exists) return;

            foreach (var dir in jobs.GetDirectories().Where(d => d.LastWriteTimeUtc < DateTime.UtcNow.AddDays(-2)))
            {
                try
                {
                    dir.Delete(true);
                }
                catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
                {
                    System.Diagnostics.Debug.WriteLine($"Could not remove old job folder {dir.FullName}: {ex.Message}");
                }
            }
        }
    }
}
