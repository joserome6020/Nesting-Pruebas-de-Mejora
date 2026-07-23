# -*- coding: utf-8 -*-
from pathlib import Path

p = Path(r"c:\Proyectos\New Arga Nesting Suite\modules\nesting_engine\cpp\packer_base.cpp")
text = p.read_text(encoding="utf-8")

start = text.find("    std::vector<PieceIn> estructurales;")
# Prefer the occurrence inside empaquetar_una_hoja_base (placa madre completa)
marker = "    // Placa madre completa:"
mi = text.find(marker)
if mi < 0:
    raise SystemExit("marker not found")
start = text.find("    std::vector<PieceIn> estructurales;", mi)
end = text.find("    // Fase 2a: SOLO cavidades abiertas", start)
if start < 0 or end < 0:
    raise SystemExit(f"bounds not found start={start} end={end}")

new = r'''    // Hosts (barreno anidable grande) primero; el resto puede ir a part-in-part.
    std::vector<PieceIn> hosts;
    std::vector<PieceIn> no_hosts;
    hosts.reserve(piezas.size());
    no_hosts.reserve(piezas.size());
    for (const auto& p : piezas) {
        if (pieza_es_anfitriona_huecos(p)) {
            hosts.push_back(p);
        } else {
            no_hosts.push_back(p);
        }
    }

    PlacementState state;
    std::vector<PieceIn> restos;

    if (!hosts.empty()) {
        auto orden_hosts = orden_pizarron(std::move(hosts));
        auto [st, r] = colocar_en_orden(
            orden_hosts,
            w_placa,
            h_placa,
            kerf_override,
            margin_override,
            std::nullopt);
        state = std::move(st);
        no_hosts.insert(no_hosts.end(), r.begin(), r.end());
    }

    // Part-in-part ANTES del patio: bridas/medianas entran a barrenos host.
    if (!no_hosts.empty()) {
        for (int pass = 0; pass < 12 && !no_hosts.empty(); ++pass) {
            const size_t antes = no_hosts.size();
            rellenar_orificios_directo(state, no_hosts, w_placa, h_placa, kerf_override);
            if (no_hosts.size() >= antes) {
                break;
            }
        }
    }

    std::vector<PieceIn> estructurales;
    std::vector<PieceIn> pool_peq;
    for (auto& p : no_hosts) {
        if (pieza_va_en_fase_estructural(p)) {
            estructurales.push_back(std::move(p));
        } else {
            pool_peq.push_back(std::move(p));
        }
    }
    if (!estructurales.empty()) {
        auto orden_est = orden_pizarron(std::move(estructurales));
        const LimitContext limit = make_limit_context(std::nullopt, margin_px);
        std::vector<PieceIn> cola = std::move(orden_est);
        while (!cola.empty()) {
            PieceIn p_data = std::move(cola.front());
            cola.erase(cola.begin());
            if (!colocar_pieza(p_data, state, w_placa, h_placa, kerf_radio, margin_px, limit)) {
                std::vector<PieceIn> one{std::move(p_data)};
                rellenar_orificios_directo(state, one, w_placa, h_placa, kerf_override);
                if (!one.empty()) {
                    restos.push_back(std::move(one.front()));
                }
            } else {
                rellenar_orificios_directo(state, pool_peq, w_placa, h_placa, kerf_override);
            }
        }
    }

    // Fase 1.5a: canales abiertos C/VFM (AABB-metal).
    if (!pool_peq.empty()) {
        for (int pass = 0; pass < 32 && !pool_peq.empty(); ++pass) {
            const size_t antes = pool_peq.size();
            rellenar_cavidades_abiertas_directo(state, pool_peq, w_placa, h_placa, kerf_override);
            if (pool_peq.size() >= antes) {
                break;
            }
        }
    }
    // Fase 1.5b: orificios otra pasada.
    if (!pool_peq.empty()) {
        for (int pass = 0; pass < 8 && !pool_peq.empty(); ++pass) {
            const size_t antes = pool_peq.size();
            rellenar_orificios_directo(state, pool_peq, w_placa, h_placa, kerf_override);
            if (pool_peq.size() >= antes) {
                break;
            }
        }
    }

'''

p.write_text(text[:start] + new + text[end:], encoding="utf-8")
print("ok", start, end)
