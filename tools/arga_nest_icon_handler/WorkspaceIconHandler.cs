using System;
using System.Collections.Generic;
using System.Drawing;
using System.IO;
using System.Runtime.InteropServices;
using System.Text.RegularExpressions;
using Microsoft.Win32;
using SharpShell.Attributes;
using SharpShell.SharpIconHandler;

namespace ArgaNestIconHandler
{
    /// <summary>
    /// Icon Handler: mismo .arganest, icono según material interno (acero/cobre/mixto).
    /// </summary>
    [ComVisible(true)]
    [Guid("B6E2C9A1-4D7F-4E8A-9C31-7A2F0D91E5B4")]
    [COMServerAssociation(AssociationType.ClassOfExtension, ".arganest")]
    [COMServerAssociation(AssociationType.ClassOfExtension, ".navanest")]
    public class WorkspaceIconHandler : SharpIconHandler
    {
        private static readonly object CacheLock = new object();
        private static readonly Dictionary<string, CacheEntry> Cache =
            new Dictionary<string, CacheEntry>(StringComparer.OrdinalIgnoreCase);

        private sealed class CacheEntry
        {
            public string Kind;
            public long Ticks;
            public long Length;
        }

        protected override Icon GetIcon(bool smallIcon, uint iconSize)
        {
            try
            {
                string path = SelectedItemPath;
                string kind = ClassifyCached(path);
                string icoPath = ResolveIconPath(kind);
                if (string.IsNullOrEmpty(icoPath) || !File.Exists(icoPath))
                    icoPath = ResolveIconPath("steel");
                if (string.IsNullOrEmpty(icoPath) || !File.Exists(icoPath))
                    return SystemIcons.Application;

                int size = iconSize > 0 ? (int)iconSize : (smallIcon ? 16 : 32);
                using (var baseIcon = new Icon(icoPath))
                {
                    return new Icon(baseIcon, size, size);
                }
            }
            catch
            {
                return SystemIcons.Application;
            }
        }

        private static string ClassifyCached(string path)
        {
            try
            {
                var fi = new FileInfo(path);
                long ticks = fi.LastWriteTimeUtc.Ticks;
                long len = fi.Length;
                string key = path;
                lock (CacheLock)
                {
                    CacheEntry hit;
                    if (Cache.TryGetValue(key, out hit) && hit.Ticks == ticks && hit.Length == len)
                        return hit.Kind;
                }

                string kind = WorkspaceClassifier.Classify(path);
                lock (CacheLock)
                {
                    Cache[key] = new CacheEntry { Kind = kind, Ticks = ticks, Length = len };
                    if (Cache.Count > 512)
                        Cache.Clear();
                }
                return kind;
            }
            catch
            {
                return "steel";
            }
        }

        private static string ResolveIconPath(string kind)
        {
            string name;
            switch ((kind ?? "").ToLowerInvariant())
            {
                case "cu":
                    name = "arga_archivo_nesteo_cu.ico";
                    break;
                case "mix":
                    name = "arga_archivo_nesteo_mix.ico";
                    break;
                default:
                    name = "arga_archivo_nesteo.ico";
                    break;
            }

            // 1) Registro: HKCU\Software\ArgaNesting\IconDir
            try
            {
                using (var k = Registry.CurrentUser.OpenSubKey(@"Software\ArgaNesting"))
                {
                    var dir = k != null ? k.GetValue("IconDir") as string : null;
                    if (!string.IsNullOrEmpty(dir))
                    {
                        var p = Path.Combine(dir, name);
                        if (File.Exists(p)) return p;
                    }
                }
            }
            catch { }

            // 2) Junto a la DLL del handler
            try
            {
                var asm = Path.GetDirectoryName(typeof(WorkspaceIconHandler).Assembly.Location);
                if (!string.IsNullOrEmpty(asm))
                {
                    var p = Path.Combine(asm, name);
                    if (File.Exists(p)) return p;
                    var p2 = Path.Combine(asm, "..", name);
                    if (File.Exists(Path.GetFullPath(p2))) return Path.GetFullPath(p2);
                }
            }
            catch { }

            return null;
        }
    }
}
