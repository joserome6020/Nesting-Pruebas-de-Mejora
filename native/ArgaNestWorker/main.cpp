/**
 * ArgaNestWorker — proceso aislado con IPC JSON por líneas (stdin/stdout).
 *
 * Protocolo:
 *   → {"cmd":"ping"}
 *   ← {"ok":true,"pong":true}
 *   → {"cmd":"version"}
 *   ← {"ok":true,"version":"..."}
 *   → {"cmd":"pack_sheet","request":{...}}
 *   ← {"ok":true|false,"result":{...}}
 *   → {"cmd":"pack_job","request":{...}}
 *   → {"cmd":"pack_cu","request":{...}}
 *   → {"cmd":"export_dxf","request":{...}}
 *   → {"cmd":"export_step","request":{...}}
 *   → {"cmd":"shutdown"}
 *
 * También: --version / --ping / --pack-file <path.json>
 */
#include "arga_nest/abi.h"

#include <nlohmann/json.hpp>

#include <cstdio>
#include <fstream>
#include <iostream>
#include <string>

#ifdef _WIN32
#  include <windows.h>
#endif

namespace {

using json = nlohmann::json;

std::string call_api(int (*fn)(const char*, char**), const std::string& req) {
    char* out = nullptr;
    const int rc = fn(req.c_str(), &out);
    std::string body = out ? std::string(out) : std::string();
    if (out) {
        arga_nest_free(out);
    }
    if (rc != ARGA_NEST_OK && rc != ARGA_NEST_E_CERTIFY) {
        throw std::runtime_error(
            std::string(arga_nest_last_error() ? arga_nest_last_error() : "error") +
            " code=" + std::to_string(rc));
    }
    return body;
}

json handle(const json& msg) {
    const std::string cmd = msg.value("cmd", "");
    if (cmd == "ping") {
        return {{"ok", true}, {"pong", true}};
    }
    if (cmd == "version") {
        char* s = nullptr;
        arga_nest_version_string(&s);
        json out = {{"ok", true}, {"version", s ? s : ""}};
        arga_nest_free(s);
        return out;
    }
    if (cmd == "shutdown") {
        return {{"ok", true}, {"shutdown", true}};
    }
    if (cmd == "pack_sheet") {
        const std::string body = call_api(&arga_nest_pack_sheet_json, msg.at("request").dump());
        return {{"ok", true}, {"result", json::parse(body)}};
    }
    if (cmd == "pack_job") {
        const std::string body = call_api(&arga_nest_pack_job_json, msg.at("request").dump());
        return {{"ok", true}, {"result", json::parse(body)}};
    }
    if (cmd == "pack_cu") {
        const std::string body = call_api(&arga_nest_pack_cu_strip_json, msg.at("request").dump());
        return {{"ok", true}, {"result", json::parse(body)}};
    }
    if (cmd == "export_dxf") {
        const std::string body = call_api(&arga_nest_export_dxf_json, msg.at("request").dump());
        return {{"ok", true}, {"dxf", body}};
    }
    if (cmd == "export_step") {
        const std::string body = call_api(&arga_nest_export_step_json, msg.at("request").dump());
        return {{"ok", true}, {"step", body}};
    }
    return {{"ok", false}, {"error", "unknown cmd"}};
}

#ifdef _WIN32
LONG WINAPI crash_filter(EXCEPTION_POINTERS*) {
    std::fprintf(stderr, "ArgaNestWorker CRASH (SEH)\n");
    return EXCEPTION_EXECUTE_HANDLER;
}
#endif

}  // namespace

int main(int argc, char** argv) {
#ifdef _WIN32
    SetUnhandledExceptionFilter(crash_filter);
#endif

    if (argc >= 2 && std::string(argv[1]) == "--version") {
        char* s = nullptr;
        arga_nest_version_string(&s);
        std::printf("%s\n", s ? s : "ArgaNestWorker");
        arga_nest_free(s);
        return 0;
    }
    if (argc >= 2 && std::string(argv[1]) == "--ping") {
        std::printf("PONG\n");
        return 0;
    }
    if (argc >= 3 && std::string(argv[1]) == "--pack-file") {
        std::ifstream in(argv[2]);
        std::string content((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
        try {
            const std::string body = call_api(&arga_nest_pack_sheet_json, content);
            std::cout << body << "\n";
            return 0;
        } catch (const std::exception& ex) {
            std::fprintf(stderr, "%s\n", ex.what());
            return 1;
        }
    }

    // IPC loop
    std::string line;
    while (std::getline(std::cin, line)) {
        if (line.empty()) {
            continue;
        }
        try {
            json msg = json::parse(line);
            json resp = handle(msg);
            std::cout << resp.dump() << "\n" << std::flush;
            if (msg.value("cmd", "") == "shutdown") {
                break;
            }
        } catch (const std::exception& ex) {
            json err = {{"ok", false}, {"error", ex.what()}};
            std::cout << err.dump() << "\n" << std::flush;
        }
    }
    return 0;
}
