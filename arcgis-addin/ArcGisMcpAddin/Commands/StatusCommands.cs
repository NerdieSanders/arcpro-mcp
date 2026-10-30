using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using System.Threading.Tasks;
using ArcGIS.Desktop.Core;
using ArcGIS.Desktop.Framework.Threading.Tasks;
using ArcGIS.Desktop.Mapping;

namespace ArcGisMcpAddin.Commands
{
    /// <summary>
    /// One call that replaces ping, project info and the layer list. It never
    /// waits on a running job: if Pro's main thread is busy, it returns the job
    /// state and says the project details were skipped.
    /// </summary>
    public static class StatusCommands
    {
        private static readonly TimeSpan ProjectTimeout = TimeSpan.FromSeconds(5);

        public static async Task<object> StatusAsync()
        {
            object? project = null;
            string? projectNote = null;

            var projectTask = QueuedTask.Run<object>(() => ReadProject());
            var finished = await Task.WhenAny(projectTask, Task.Delay(ProjectTimeout));
            if (finished == projectTask)
            {
                project = await projectTask;
            }
            else
            {
                projectNote = $"Pro's main thread did not answer within {ProjectTimeout.TotalSeconds:0} s, so project details were skipped.";
                _ = projectTask.ContinueWith(
                    t => System.Diagnostics.Debug.WriteLine($"Late status read failed: {t.Exception?.GetBaseException().Message}"),
                    TaskContinuationOptions.OnlyOnFaulted);
            }

            return new
            {
                addin_version = CoreCommands.AddinVersion,
                arcgis_pro_version = Assembly.GetEntryAssembly()?.GetName().Version?.ToString() ?? "unknown",
                running_job = JobManager.Current(),
                project,
                project_note = projectNote
            };
        }

        private static object ReadProject()
        {
            var current = Project.Current;
            var map = MapView.Active?.Map;

            var layers = new List<object>();
            if (map != null)
            {
                foreach (var layer in map.GetLayersAsFlattenedList())
                {
                    layers.Add(new
                    {
                        name = layer.Name,
                        type = layer.GetType().Name,
                        visible = layer.IsVisible,
                        parent = (layer.Parent as Layer)?.Name
                    });
                }
            }

            return new
            {
                name = current?.Name ?? "",
                path = current?.Path ?? "",
                maps = current?.GetItems<MapProjectItem>().Select(m => m.Name).ToList() ?? new List<string>(),
                active_map = map?.Name ?? "",
                layers,
                tables = map?.StandaloneTables.Select(t => t.Name).ToList() ?? new List<string>()
            };
        }
    }
}
