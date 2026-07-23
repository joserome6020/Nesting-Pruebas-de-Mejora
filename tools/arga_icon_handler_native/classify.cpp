#include "miniz.h"
#include "miniz_tinfl.h"

#include "classify.h"

#include <windows.h>
#include <string.h>
#include <stdlib.h>
#include <ctype.h>
#include <vector>
#include <string>
#include <mutex>
#include <unordered_map>

namespace {

struct CacheEntry {
    FILETIME ft{};
    ULONGLONG size = 0;
    std::string kind;
};

std::mutex g_cacheMu;
std::unordered_map<std::wstring, CacheEntry> g_cache;
thread_local std::string g_tlsKind;

bool EqualsFt(const FILETIME& a, const FILETIME& b) {
    return a.dwLowDateTime == b.dwLowDateTime && a.dwHighDateTime == b.dwHighDateTime;
}

bool ReadFileAll(const wchar_t* path, std::vector<unsigned char>& out) {
    HANDLE h = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                           nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (h == INVALID_HANDLE_VALUE) return false;
    LARGE_INTEGER li{};
    if (!GetFileSizeEx(h, &li) || li.QuadPart < 0 || li.QuadPart > (LONGLONG)64 * 1024 * 1024) {
        CloseHandle(h);
        return false;
    }
    out.resize(static_cast<size_t>(li.QuadPart));
    DWORD got = 0;
    BOOL ok = ReadFile(h, out.data(), (DWORD)out.size(), &got, nullptr);
    CloseHandle(h);
    if (!ok || got != out.size()) {
        out.clear();
        return false;
    }
    return true;
}

bool IsGzip(const std::vector<unsigned char>& raw) {
    return raw.size() >= 2 && raw[0] == 0x1F && raw[1] == 0x8B;
}

bool GunzipToText(const std::vector<unsigned char>& raw, std::string& text) {
    text.clear();
    if (raw.size() < 18) return false;
    size_t i = 10;
    const unsigned char flg = raw[3];
    if (flg & 4) {
        if (i + 2 > raw.size()) return false;
        unsigned len = (unsigned)raw[i] | ((unsigned)raw[i + 1] << 8);
        i += 2 + len;
    }
    if (flg & 8) {
        while (i < raw.size() && raw[i]) ++i;
        ++i;
    }
    if (flg & 16) {
        while (i < raw.size() && raw[i]) ++i;
        ++i;
    }
    if (flg & 2) i += 2;
    if (i >= raw.size() || raw.size() < 8) return false;

    const size_t in_end = raw.size() - 8;
    std::vector<unsigned char> out(512 * 1024);
    size_t out_len = 0;
    tinfl_decompressor decomp;
    tinfl_init(&decomp);
    size_t in_pos = i;

    while (in_pos < in_end) {
        if (out.size() - out_len < 64 * 1024) out.resize(out.size() * 2);
        size_t in_sz = in_end - in_pos;
        size_t out_sz = out.size() - out_len;
        int flags = TINFL_FLAG_USING_NON_WRAPPING_OUTPUT_BUF;
        if (in_pos + in_sz < in_end) flags |= TINFL_FLAG_HAS_MORE_INPUT;
        tinfl_status st = tinfl_decompress(&decomp, raw.data() + in_pos, &in_sz, out.data(),
                                           out.data() + out_len, &out_sz, flags);
        in_pos += in_sz;
        out_len += out_sz;
        if (st == TINFL_STATUS_DONE) break;
        if (st < TINFL_STATUS_DONE) return false;
        if (in_sz == 0 && out_sz == 0) return false;
    }
    text.assign(reinterpret_cast<const char*>(out.data()), out_len);
    return !text.empty();
}

bool ToText(const std::vector<unsigned char>& raw, std::string& text) {
    if (raw.empty()) return false;
    if (!IsGzip(raw)) {
        text.assign(reinterpret_cast<const char*>(raw.data()), raw.size());
        return true;
    }
    return GunzipToText(raw, text);
}

bool IcuMaterial(const char* s, size_t n) {
    auto up = [](char c) -> char {
        return (c >= 'a' && c <= 'z') ? (char)(c - 'a' + 'A') : c;
    };
    if (n == 2 && up(s[0]) == 'C' && up(s[1]) == 'U') return true;
    if (n >= 5) {
        std::string u;
        u.reserve(n);
        for (size_t i = 0; i < n; ++i) u.push_back(up(s[i]));
        if (u.find("COBRE") != std::string::npos) return true;
        if (u.find("COPPER") != std::string::npos) return true;
    }
    return false;
}

bool IsCopperKey(const std::string& key) {
    if (key.empty() || key[0] == '_') return false;
    std::string u;
    u.reserve(key.size());
    for (char c : key) u.push_back((char)toupper((unsigned char)c));
    if (u.size() >= 3 && (u.compare(u.size() - 3, 3, "_CU") == 0 || u.compare(u.size() - 3, 3, "|CU") == 0))
        return true;
    if (u.find("| CU") != std::string::npos) return true;
    auto us = u.find('_');
    if (us != std::string::npos && us + 1 < u.size())
        return IcuMaterial(u.c_str() + us + 1, u.size() - us - 1);
    return IcuMaterial(u.c_str(), u.size());
}

bool IsMetaKey(const std::string& key) {
    static const char* meta[] = {
        "schema", "saved_at", "workspace_type", "job_activo", "lote_actual_idx",
        "resultados_multilote", "datos_partes_actuales", "editable_inputs_by_lote",
        "editable_inputs_actuales", "source_dxf_paths", "source_dxf_paths_by_lote",
        "meta_pdf_por_ruta", "orientacion_cobre_por_ruta", "wo_reales_por_lote",
        "ultimos_escenarios", "dxf_export_cache", "ui_state", "vista_actual",
        "workspace_material_kind", "data", "hojas", "error", "export_meta",
        "lote_editado_dirty", nullptr};
    for (int i = 0; meta[i]; ++i)
        if (key == meta[i]) return true;
    return false;
}

bool LooksLikeNestKey(const std::string& key) {
    if (key.find('_') == std::string::npos) return false;
    return (key[0] >= '0' && key[0] <= '9') ||
           (key.size() >= 2 && (key[0] == 'C' || key[0] == 'c') && (key[1] == 'U' || key[1] == 'u'));
}

std::string ClassifyText(const std::string& text) {
    {
        const char* tag = "\"workspace_material_kind\"";
        auto pos = text.find(tag);
        if (pos != std::string::npos) {
            auto colon = text.find(':', pos);
            if (colon != std::string::npos) {
                auto q1 = text.find('"', colon + 1);
                if (q1 != std::string::npos) {
                    auto q2 = text.find('"', q1 + 1);
                    if (q2 != std::string::npos && q2 > q1 + 1) {
                        std::string v = text.substr(q1 + 1, q2 - q1 - 1);
                        for (char& c : v) c = (char)tolower((unsigned char)c);
                        if (v == "steel" || v == "cu" || v == "mix") return v;
                    }
                }
            }
        }
    }

    bool hasCu = false, hasSteel = false;
    size_t i = 0;
    while (i < text.size()) {
        if (text[i] != '"') { ++i; continue; }
        size_t j = i + 1;
        while (j < text.size() && text[j] != '"' && (j - i) < 120) ++j;
        if (j >= text.size() || text[j] != '"') { ++i; continue; }
        std::string key = text.substr(i + 1, j - i - 1);
        size_t k = j + 1;
        while (k < text.size() && isspace((unsigned char)text[k])) ++k;
        if (k < text.size() && text[k] == ':') {
            size_t k2 = k + 1;
            while (k2 < text.size() && isspace((unsigned char)text[k2])) ++k2;
            if (k2 < text.size() && text[k2] == '{') {
                if (!IsMetaKey(key) && LooksLikeNestKey(key)) {
                    if (IsCopperKey(key)) hasCu = true;
                    else hasSteel = true;
                    if (hasCu && hasSteel) return "mix";
                }
            }
        }
        i = j + 1;
    }

    if (!hasCu && !hasSteel) {
        const char* tag = "\"material\"";
        size_t pos = 0;
        while ((pos = text.find(tag, pos)) != std::string::npos) {
            auto colon = text.find(':', pos);
            if (colon == std::string::npos) break;
            auto q1 = text.find('"', colon + 1);
            if (q1 == std::string::npos) break;
            auto q2 = text.find('"', q1 + 1);
            if (q2 == std::string::npos) break;
            std::string mat = text.substr(q1 + 1, q2 - q1 - 1);
            if (!mat.empty()) {
                if (IcuMaterial(mat.c_str(), mat.size())) hasCu = true;
                else hasSteel = true;
                if (hasCu && hasSteel) return "mix";
            }
            pos = q2 + 1;
        }
    }

    if (hasCu && hasSteel) return "mix";
    if (hasCu) return "cu";
    return "steel";
}

} // namespace

const char* ArgaClassifyWorkspaceFile(const wchar_t* pathW) {
    g_tlsKind = "steel";
    if (!pathW || !pathW[0]) return g_tlsKind.c_str();

    WIN32_FILE_ATTRIBUTE_DATA fad{};
    if (!GetFileAttributesExW(pathW, GetFileExInfoStandard, &fad)) return g_tlsKind.c_str();

    {
        std::lock_guard<std::mutex> lock(g_cacheMu);
        auto it = g_cache.find(pathW);
        if (it != g_cache.end() && EqualsFt(it->second.ft, fad.ftLastWriteTime) &&
            it->second.size == ((ULONGLONG)fad.nFileSizeHigh << 32) + fad.nFileSizeLow) {
            g_tlsKind = it->second.kind;
            return g_tlsKind.c_str();
        }
    }

    std::vector<unsigned char> raw;
    if (!ReadFileAll(pathW, raw)) return g_tlsKind.c_str();
    std::string text;
    if (!ToText(raw, text)) return g_tlsKind.c_str();
    g_tlsKind = ClassifyText(text);

    {
        std::lock_guard<std::mutex> lock(g_cacheMu);
        CacheEntry e;
        e.ft = fad.ftLastWriteTime;
        e.size = ((ULONGLONG)fad.nFileSizeHigh << 32) + fad.nFileSizeLow;
        e.kind = g_tlsKind;
        g_cache[pathW] = std::move(e);
        if (g_cache.size() > 512) g_cache.clear();
    }
    return g_tlsKind.c_str();
}
