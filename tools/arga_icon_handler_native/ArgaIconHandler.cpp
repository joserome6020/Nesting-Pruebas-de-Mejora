// Arga Nesting Suite — native Icon Handler for .arganest / .navanest
// CLSID: {A31F8C2E-9B74-4D6A-8E15-2C70F4A9D813}
#ifndef UNICODE
#define UNICODE
#endif
#ifndef _UNICODE
#define _UNICODE
#endif

#include <windows.h>
#include <shlobj.h>
#include <shlwapi.h>
#include <new>
#include <string>

#include "classify.h"

#pragma comment(lib, "shlwapi.lib")
#pragma comment(lib, "ole32.lib")
#pragma comment(lib, "shell32.lib")
#pragma comment(lib, "user32.lib")
#pragma comment(lib, "advapi32.lib")

// {A31F8C2E-9B74-4D6A-8E15-2C70F4A9D813}
static const CLSID CLSID_ArgaIconHandler =
{ 0xa31f8c2e, 0x9b74, 0x4d6a, { 0x8e, 0x15, 0x2c, 0x70, 0xf4, 0xa9, 0xd8, 0x13 } };

static LONG g_locks = 0;
static HINSTANCE g_hInst = nullptr;

static void GetIconDir(wchar_t* buf, size_t cch) {
    buf[0] = 0;
    // Prefer LocalAppData\ArgaNesting (no spaces)
    wchar_t local[MAX_PATH];
    if (SUCCEEDED(SHGetFolderPathW(nullptr, CSIDL_LOCAL_APPDATA, nullptr, SHGFP_TYPE_CURRENT, local))) {
        wchar_t dir[MAX_PATH];
        PathCombineW(dir, local, L"ArgaNesting");
        wcsncpy_s(buf, cch, dir, _TRUNCATE);
        return;
    }
}

static void IconPathForKind(const char* kind, wchar_t* out, size_t cch) {
    wchar_t dir[MAX_PATH];
    GetIconDir(dir, MAX_PATH);
    const wchar_t* name = L"arga_archivo_nesteo.ico";
    if (kind && _stricmp(kind, "cu") == 0) name = L"arga_archivo_nesteo_cu.ico";
    else if (kind && _stricmp(kind, "mix") == 0) name = L"arga_archivo_nesteo_mix.ico";
    wchar_t path[MAX_PATH];
    PathCombineW(path, dir, name);
    if (!PathFileExistsW(path)) {
        PathCombineW(path, dir, L"arga_archivo_nesteo.ico");
    }
    wcsncpy_s(out, cch, path, _TRUNCATE);
}

// ---------------- IconHandler object ----------------

class ArgaIconHandler : public IExtractIconW, public IPersistFile {
public:
    ArgaIconHandler() : m_ref(1) { InterlockedIncrement(&g_locks); }
    ~ArgaIconHandler() { InterlockedDecrement(&g_locks); }

    // IUnknown
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID riid, void** ppv) override {
        if (!ppv) return E_POINTER;
        *ppv = nullptr;
        if (IsEqualIID(riid, IID_IUnknown) || IsEqualIID(riid, IID_IExtractIconW)) {
            *ppv = static_cast<IExtractIconW*>(this);
        } else if (IsEqualIID(riid, IID_IPersistFile) || IsEqualIID(riid, IID_IPersist)) {
            *ppv = static_cast<IPersistFile*>(this);
        } else {
            return E_NOINTERFACE;
        }
        AddRef();
        return S_OK;
    }
    ULONG STDMETHODCALLTYPE AddRef() override { return (ULONG)InterlockedIncrement(&m_ref); }
    ULONG STDMETHODCALLTYPE Release() override {
        LONG r = InterlockedDecrement(&m_ref);
        if (r == 0) delete this;
        return (ULONG)r;
    }

    // IPersist
    HRESULT STDMETHODCALLTYPE GetClassID(CLSID* pClassID) override {
        if (!pClassID) return E_POINTER;
        *pClassID = CLSID_ArgaIconHandler;
        return S_OK;
    }

    // IPersistFile
    HRESULT STDMETHODCALLTYPE IsDirty() override { return S_FALSE; }
    HRESULT STDMETHODCALLTYPE Load(LPCOLESTR pszFileName, DWORD /*dwMode*/) override {
        if (!pszFileName) return E_INVALIDARG;
        m_path = pszFileName;
        return S_OK;
    }
    HRESULT STDMETHODCALLTYPE Save(LPCOLESTR, BOOL) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE SaveCompleted(LPCOLESTR) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE GetCurFile(LPOLESTR* ppszFileName) override {
        if (!ppszFileName) return E_POINTER;
        *ppszFileName = nullptr;
        if (m_path.empty()) return S_FALSE;
        size_t bytes = (m_path.size() + 1) * sizeof(wchar_t);
        *ppszFileName = (LPOLESTR)CoTaskMemAlloc(bytes);
        if (!*ppszFileName) return E_OUTOFMEMORY;
        memcpy(*ppszFileName, m_path.c_str(), bytes);
        return S_OK;
    }

    // IExtractIconW
    HRESULT STDMETHODCALLTYPE GetIconLocation(UINT uFlags, LPWSTR pszIconFile, UINT cchMax,
                                              int* piIndex, UINT* pwFlags) override {
        if (!pszIconFile || !piIndex || !pwFlags) return E_POINTER;
        pszIconFile[0] = 0;
        *piIndex = 0;
        *pwFlags = GIL_PERINSTANCE; // icon may differ per file

        const char* kind = "steel";
        if (!m_path.empty()) {
            kind = ArgaClassifyWorkspaceFile(m_path.c_str());
            if (!kind) kind = "steel";
        }
        wchar_t ico[MAX_PATH];
        IconPathForKind(kind, ico, MAX_PATH);
        if (!PathFileExistsW(ico)) {
            // Absolute fallback: leave empty → Explorer uses DefaultIcon
            *pwFlags = GIL_DEFAULTICON;
            return S_FALSE;
        }
        wcsncpy_s(pszIconFile, cchMax, ico, _TRUNCATE);
        *piIndex = 0;
        if (uFlags & GIL_FORSHELL) {
            // ok
        }
        return S_OK;
    }

    HRESULT STDMETHODCALLTYPE Extract(LPCWSTR pszFile, UINT nIconIndex, HICON* phiconLarge,
                                      HICON* phiconSmall, UINT nIconSize) override {
        // Let shell extract from the .ico path we provided.
        return S_FALSE;
    }

private:
    LONG m_ref;
    std::wstring m_path;
};

// ---------------- Class factory ----------------

class ArgaClassFactory : public IClassFactory {
public:
    ArgaClassFactory() : m_ref(1) { InterlockedIncrement(&g_locks); }
    ~ArgaClassFactory() { InterlockedDecrement(&g_locks); }

    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID riid, void** ppv) override {
        if (!ppv) return E_POINTER;
        *ppv = nullptr;
        if (IsEqualIID(riid, IID_IUnknown) || IsEqualIID(riid, IID_IClassFactory)) {
            *ppv = static_cast<IClassFactory*>(this);
            AddRef();
            return S_OK;
        }
        return E_NOINTERFACE;
    }
    ULONG STDMETHODCALLTYPE AddRef() override { return (ULONG)InterlockedIncrement(&m_ref); }
    ULONG STDMETHODCALLTYPE Release() override {
        LONG r = InterlockedDecrement(&m_ref);
        if (r == 0) delete this;
        return (ULONG)r;
    }
    HRESULT STDMETHODCALLTYPE CreateInstance(IUnknown* pUnkOuter, REFIID riid, void** ppv) override {
        if (pUnkOuter) return CLASS_E_NOAGGREGATION;
        if (!ppv) return E_POINTER;
        *ppv = nullptr;
        ArgaIconHandler* obj = new (std::nothrow) ArgaIconHandler();
        if (!obj) return E_OUTOFMEMORY;
        HRESULT hr = obj->QueryInterface(riid, ppv);
        obj->Release();
        return hr;
    }
    HRESULT STDMETHODCALLTYPE LockServer(BOOL fLock) override {
        if (fLock) InterlockedIncrement(&g_locks);
        else InterlockedDecrement(&g_locks);
        return S_OK;
    }

private:
    LONG m_ref;
};

// ---------------- DLL exports ----------------

BOOL APIENTRY DllMain(HINSTANCE hModule, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        g_hInst = hModule;
        DisableThreadLibraryCalls(hModule);
    }
    return TRUE;
}

STDAPI DllCanUnloadNow() {
    return g_locks == 0 ? S_OK : S_FALSE;
}

STDAPI DllGetClassObject(REFCLSID rclsid, REFIID riid, void** ppv) {
    if (!IsEqualCLSID(rclsid, CLSID_ArgaIconHandler)) return CLASS_E_CLASSNOTAVAILABLE;
    ArgaClassFactory* f = new (std::nothrow) ArgaClassFactory();
    if (!f) return E_OUTOFMEMORY;
    HRESULT hr = f->QueryInterface(riid, ppv);
    f->Release();
    return hr;
}

static HRESULT SetRegSZ(HKEY root, const wchar_t* sub, const wchar_t* name, const wchar_t* value) {
    HKEY k = nullptr;
    LONG st = RegCreateKeyExW(root, sub, 0, nullptr, 0, KEY_SET_VALUE | KEY_WOW64_64KEY, nullptr, &k, nullptr);
    if (st != ERROR_SUCCESS) return HRESULT_FROM_WIN32(st);
    st = RegSetValueExW(k, name, 0, REG_SZ, (const BYTE*)value, (DWORD)((wcslen(value) + 1) * sizeof(wchar_t)));
    RegCloseKey(k);
    return HRESULT_FROM_WIN32(st);
}

static HRESULT DeleteRegTree(HKEY root, const wchar_t* sub) {
    // RegDeleteTree available Vista+
    LONG st = RegDeleteTreeW(root, sub);
    if (st == ERROR_FILE_NOT_FOUND) return S_OK;
    return HRESULT_FROM_WIN32(st);
}

STDAPI DllRegisterServer() {
    wchar_t dllPath[MAX_PATH];
    if (!GetModuleFileNameW(g_hInst, dllPath, MAX_PATH)) return E_FAIL;

    const wchar_t* clsid = L"{A31F8C2E-9B74-4D6A-8E15-2C70F4A9D813}";
    wchar_t key[256];

    swprintf_s(key, L"Software\\Classes\\CLSID\\%s", clsid);
    SetRegSZ(HKEY_CURRENT_USER, key, nullptr, L"Arga Nest Workspace Icon Handler");
    swprintf_s(key, L"Software\\Classes\\CLSID\\%s\\InProcServer32", clsid);
    SetRegSZ(HKEY_CURRENT_USER, key, nullptr, dllPath);
    SetRegSZ(HKEY_CURRENT_USER, key, L"ThreadingModel", L"Apartment");

    // Also HKLM if we have rights (Approved + machine-wide)
    SetRegSZ(HKEY_LOCAL_MACHINE, L"Software\\Classes\\CLSID\\{A31F8C2E-9B74-4D6A-8E15-2C70F4A9D813}",
             nullptr, L"Arga Nest Workspace Icon Handler");
    SetRegSZ(HKEY_LOCAL_MACHINE, L"Software\\Classes\\CLSID\\{A31F8C2E-9B74-4D6A-8E15-2C70F4A9D813}\\InProcServer32",
             nullptr, dllPath);
    SetRegSZ(HKEY_LOCAL_MACHINE, L"Software\\Classes\\CLSID\\{A31F8C2E-9B74-4D6A-8E15-2C70F4A9D813}\\InProcServer32",
             L"ThreadingModel", L"Apartment");
    SetRegSZ(HKEY_LOCAL_MACHINE,
             L"Software\\Microsoft\\Windows\\CurrentVersion\\Shell Extensions\\Approved",
             clsid, L"Arga Nest Workspace Icon Handler");

    // ProgID + extensions IconHandler
    SetRegSZ(HKEY_CURRENT_USER, L"Software\\Classes\\ArgaNesting.Workspace\\ShellEx\\IconHandler",
             nullptr, clsid);
    SetRegSZ(HKEY_CURRENT_USER, L"Software\\Classes\\.arganest\\ShellEx\\IconHandler", nullptr, clsid);
    SetRegSZ(HKEY_CURRENT_USER, L"Software\\Classes\\.navanest\\ShellEx\\IconHandler", nullptr, clsid);

    SHChangeNotify(SHCNE_ASSOCCHANGED, SHCNF_IDLIST, nullptr, nullptr);
    return S_OK;
}

STDAPI DllUnregisterServer() {
    const wchar_t* clsid = L"{A31F8C2E-9B74-4D6A-8E15-2C70F4A9D813}";
    DeleteRegTree(HKEY_CURRENT_USER, L"Software\\Classes\\CLSID\\{A31F8C2E-9B74-4D6A-8E15-2C70F4A9D813}");
    DeleteRegTree(HKEY_CURRENT_USER, L"Software\\Classes\\ArgaNesting.Workspace\\ShellEx");
    DeleteRegTree(HKEY_CURRENT_USER, L"Software\\Classes\\.arganest\\ShellEx");
    DeleteRegTree(HKEY_CURRENT_USER, L"Software\\Classes\\.navanest\\ShellEx");
    DeleteRegTree(HKEY_LOCAL_MACHINE, L"Software\\Classes\\CLSID\\{A31F8C2E-9B74-4D6A-8E15-2C70F4A9D813}");
    RegDeleteKeyValueW(HKEY_LOCAL_MACHINE,
                       L"Software\\Microsoft\\Windows\\CurrentVersion\\Shell Extensions\\Approved", clsid);
    SHChangeNotify(SHCNE_ASSOCCHANGED, SHCNF_IDLIST, nullptr, nullptr);
    return S_OK;
}
