using System;
using System.IO;
using System.IO.Compression;
using System.Text;
using System.Text.RegularExpressions;

namespace ArgaNestIconHandler
{
    /// <summary>
    /// Clasifica un .arganest/.navanest como steel | cu | mix leyendo el JSON interno.
    /// Preferencia: campo workspace_material_kind al inicio del JSON (rápido).
    /// </summary>
    internal static class WorkspaceClassifier
    {
        private static readonly Regex KindField = new Regex(
            @"""workspace_material_kind""\s*:\s*""(steel|cu|mix)""",
            RegexOptions.IgnoreCase | RegexOptions.Compiled);

        private static readonly Regex KeyPattern = new Regex(
            @"""([^""]{1,120})""\s*:\s*\{",
            RegexOptions.Compiled);

        private static readonly Regex PartMaterial = new Regex(
            @"""material""\s*:\s*""([^""]*)""",
            RegexOptions.IgnoreCase | RegexOptions.Compiled);

        public static string Classify(string filePath)
        {
            try
            {
                if (string.IsNullOrWhiteSpace(filePath) || !File.Exists(filePath))
                    return "steel";

                string text = ReadWorkspaceText(filePath);
                if (string.IsNullOrEmpty(text))
                    return "steel";

                var kindMatch = KindField.Match(text);
                if (kindMatch.Success)
                    return kindMatch.Groups[1].Value.ToLowerInvariant();

                bool hasCu = false;
                bool hasSteel = false;

                // Claves de resultados (aprox): "0.25_A36": { ... } / "10_CU": {
                foreach (Match m in KeyPattern.Matches(text))
                {
                    string key = m.Groups[1].Value;
                    if (string.IsNullOrEmpty(key) || key.StartsWith("_"))
                        continue;
                    // Filtrar claves de esquema que no son grupos de nest
                    if (IsMetaKey(key))
                        continue;
                    if (!LooksLikeNestKey(key))
                        continue;
                    if (IsCopperKey(key)) hasCu = true;
                    else hasSteel = true;
                    if (hasCu && hasSteel) return "mix";
                }

                if (!hasCu && !hasSteel)
                {
                    foreach (Match m in PartMaterial.Matches(text))
                    {
                        var mat = m.Groups[1].Value;
                        if (string.IsNullOrWhiteSpace(mat)) continue;
                        if (IsCopperMaterial(mat)) hasCu = true;
                        else hasSteel = true;
                        if (hasCu && hasSteel) return "mix";
                    }
                }

                if (hasCu && hasSteel) return "mix";
                if (hasCu) return "cu";
                return "steel";
            }
            catch
            {
                return "steel";
            }
        }

        private static bool IsMetaKey(string key)
        {
            switch (key)
            {
                case "schema":
                case "saved_at":
                case "workspace_type":
                case "job_activo":
                case "lote_actual_idx":
                case "resultados_multilote":
                case "datos_partes_actuales":
                case "editable_inputs_by_lote":
                case "editable_inputs_actuales":
                case "source_dxf_paths":
                case "source_dxf_paths_by_lote":
                case "meta_pdf_por_ruta":
                case "orientacion_cobre_por_ruta":
                case "wo_reales_por_lote":
                case "ultimos_escenarios":
                case "dxf_export_cache":
                case "ui_state":
                case "vista_actual":
                case "workspace_material_kind":
                case "data":
                case "hojas":
                case "error":
                    return true;
                default:
                    return false;
            }
        }

        private static bool LooksLikeNestKey(string key)
        {
            // típico: 0.25_A36 / 10_CU / 0.1196_A 36 GALV
            if (key.IndexOf('_') < 0) return false;
            char c0 = key[0];
            return char.IsDigit(c0) || key.StartsWith("CU", StringComparison.OrdinalIgnoreCase);
        }

        private static string ReadWorkspaceText(string filePath)
        {
            byte[] raw = File.ReadAllBytes(filePath);
            if (raw.Length >= 2 && raw[0] == 0x1F && raw[1] == 0x8B)
            {
                using (var ms = new MemoryStream(raw))
                using (var gz = new GZipStream(ms, CompressionMode.Decompress))
                using (var outMs = new MemoryStream())
                {
                    gz.CopyTo(outMs);
                    return Encoding.UTF8.GetString(outMs.ToArray());
                }
            }
            return Encoding.UTF8.GetString(raw);
        }

        private static bool IsCopperKey(string clave)
        {
            var s = (clave ?? "").Trim().ToUpperInvariant();
            if (string.IsNullOrEmpty(s) || s.StartsWith("_")) return false;
            if (s.EndsWith("_CU") || s.EndsWith("|CU") || s.Contains("| CU")) return true;
            var idx = s.IndexOf('_');
            if (idx > 0 && idx < s.Length - 1)
                return IsCopperMaterial(s.Substring(idx + 1));
            return IsCopperMaterial(s);
        }

        private static bool IsCopperMaterial(string material)
        {
            var m = (material ?? "").Trim().ToUpperInvariant();
            return m == "CU" || m == "COBRE" || m == "COPPER"
                   || m.Contains("COBRE") || m.Contains("COPPER");
        }
    }
}
