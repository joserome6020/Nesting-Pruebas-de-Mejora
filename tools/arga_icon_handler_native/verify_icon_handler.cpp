// Quick COM smoke-test for ArgaIconHandler GetIconLocation
#define UNICODE
#define _UNICODE
#include <windows.h>
#include <shlobj.h>
#include <shlwapi.h>
#include <stdio.h>

#pragma comment(lib, "ole32.lib")
#pragma comment(lib, "shell32.lib")
#pragma comment(lib, "shlwapi.lib")

// {A31F8C2E-9B74-4D6A-8E15-2C70F4A9D813}
static const CLSID CLSID_Arga =
{ 0xa31f8c2e, 0x9b74, 0x4d6a, { 0x8e, 0x15, 0x2c, 0x70, 0xf4, 0xa9, 0xd8, 0x13 } };

int wmain(int argc, wchar_t** argv) {
    if (argc < 2) {
        wprintf(L"Usage: verify_icon_handler <file.arganest> [...]\n");
        return 1;
    }
    HRESULT hr = CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
    if (FAILED(hr)) return 2;

    for (int a = 1; a < argc; ++a) {
        IUnknown* unk = nullptr;
        hr = CoCreateInstance(CLSID_Arga, nullptr, CLSCTX_INPROC_SERVER, IID_IUnknown, (void**)&unk);
        if (FAILED(hr) || !unk) {
            wprintf(L"FAIL CoCreateInstance 0x%08X for %s\n", (unsigned)hr, argv[a]);
            continue;
        }
        IPersistFile* pf = nullptr;
        hr = unk->QueryInterface(IID_IPersistFile, (void**)&pf);
        if (FAILED(hr) || !pf) {
            wprintf(L"FAIL IPersistFile 0x%08X\n", (unsigned)hr);
            unk->Release();
            continue;
        }
        hr = pf->Load(argv[a], STGM_READ);
        IExtractIconW* ei = nullptr;
        hr = unk->QueryInterface(IID_IExtractIconW, (void**)&ei);
        wchar_t loc[MAX_PATH] = {};
        int idx = 0;
        UINT flags = 0;
        if (ei) {
            hr = ei->GetIconLocation(0, loc, MAX_PATH, &idx, &flags);
            const wchar_t* base = PathFindFileNameW(loc);
            wprintf(L"%s -> %s (hr=0x%08X flags=0x%X)\n", PathFindFileNameW(argv[a]), base, (unsigned)hr, flags);
            ei->Release();
        } else {
            wprintf(L"FAIL IExtractIconW\n");
        }
        pf->Release();
        unk->Release();
    }
    CoUninitialize();
    return 0;
}
