def _fill_sheet_free_pockets(
    hoja: dict,
    entries: list[dict],
    hosts: list[dict],
    used_guest_idx: set[int],
    kerf_half: float,
    t0: float,
) -> int:
    """
    Rellena SOLO huecos internos / adyacentes al nest.

    No mueve piezas al remanente lejano (esquinas derechas): eso explotaba el bbox
    (BKT-304 en esquinas). Solo coloca si queda pegado al bloque ocupado.
    """
    from shapely.ops import unary_union

    placa_w = float(hoja.get("placa_w", 0) or 0)
    placa_h = float(hoja.get("placa_h", 0) or 0)
    if placa_w <= 0 or placa_h <= 0:
        return 0

    host_idx = {h["idx"] for h in hosts}
    host_cavs: list[Polygon] = []
    for h in hosts:
        host_cavs.extend(
            list_host_cavities(h["poly"], open_profile=_host_open_profile(h["poly"]))
        )

    movable = []
    for e in entries:
        if e["idx"] in host_idx or e["idx"] in used_guest_idx:
            continue
        if host_cavs and _guest_already_in_cavity(e["poly"], host_cavs):
            continue
        movable.append(e)
    if not movable:
        return 0
    movable.sort(key=lambda e: float(e["poly"].area))

    attach_max_mm = max(25.0, kerf_half * 8.0)
    plate_area = max(placa_w * placa_h, 1.0)
    moved = 0
    max_moves = min(16, len(movable))

    for _ in range(max_moves):
        if time.perf_counter() - t0 > MAX_FILL_SECONDS:
            break

        raw_polys = [e["poly"] for e in entries if e["poly"] is not None]
        if not raw_polys:
            break
        try:
            occ_raw = unary_union(raw_polys)
            buffered = []
            for e in entries:
                try:
                    buffered.append(e["poly"].buffer(kerf_half, resolution=2, join_style=2))
                except Exception:
                    buffered.append(e["poly"])
            occ = unary_union(buffered)
            free = box(kerf_half, kerf_half, placa_w - kerf_half, placa_h - kerf_half).difference(occ)
        except Exception:
            break
        if free is None or free.is_empty or occ_raw is None or occ_raw.is_empty:
            break

        nest_minx, nest_miny, nest_maxx, nest_maxy = occ_raw.bounds
        try:
            nest_zone = box(nest_minx, nest_miny, nest_maxx, nest_maxy).buffer(
                attach_max_mm, join_style=2
            )
        except Exception:
            nest_zone = box(
                nest_minx - attach_max_mm,
                nest_miny - attach_max_mm,
                nest_maxx + attach_max_mm,
                nest_maxy + attach_max_mm,
            )

        pockets = []
        geoms = [free] if free.geom_type == "Polygon" else list(getattr(free, "geoms", []) or [])
        for g in geoms:
            if g.geom_type != "Polygon" or g.is_empty:
                continue
            a = float(g.area)
            if a < MIN_SHEET_POCKET_MM2:
                continue
            # Remanente exterior grande (lado derecho): no tocar.
            if a > 0.12 * plate_area:
                gb = g.bounds
                if gb[2] >= (placa_w - kerf_half) - 2.0 or gb[0] > nest_maxx + attach_max_mm:
                    continue
            try:
                if not g.intersects(nest_zone):
                    continue
            except Exception:
                continue
            pockets.append(g)
        pockets.sort(key=lambda g: g.area, reverse=True)
        if not pockets:
            break

        placed_any = False
        for pocket in pockets[:8]:
            if time.perf_counter() - t0 > MAX_FILL_SECONDS:
                break
            for guest_e in movable:
                if guest_e["idx"] in used_guest_idx:
                    continue
                try:
                    inter = guest_e["poly"].intersection(pocket)
                    if getattr(inter, "area", 0) > float(guest_e["poly"].area) * 0.85:
                        continue
                except Exception:
                    pass

                gpoly = guest_e["poly"]
                if float(gpoly.area) > float(pocket.area) * 0.95:
                    continue

                others = [e["poly"] for e in entries if e["idx"] != guest_e["idx"]]
                try:
                    others_u = unary_union(others) if others else occ_raw
                except Exception:
                    others_u = occ_raw

                best = None
                for angle_deg, centered in _guest_variants(gpoly):
                    cands = _candidate_translations(centered, pocket, kerf_half)
                    if not cands:
                        continue
                    for cx, cy in cands:
                        test = affinity.translate(centered, cx, cy)
                        if not _place_ok(test, pocket, others, None, kerf_half):
                            continue
                        try:
                            dist = float(test.distance(others_u))
                        except Exception:
                            continue
                        if dist > attach_max_mm:
                            continue
                        if test.bounds[0] > nest_maxx + attach_max_mm:
                            continue
                        ok_hosts = True
                        for h in hosts:
                            try:
                                if (
                                    getattr(test.intersection(h["poly"]), "area", 0)
                                    > METAL_OVERLAP_EPS_MM2
                                ):
                                    ok_hosts = False
                                    break
                            except Exception:
                                ok_hosts = False
                                break
                        if not ok_hosts:
                            continue
                        if best is None or dist < best[0]:
                            best = (dist, angle_deg, test)

                if best is None:
                    continue
                dist, angle_deg, test = best
                old_poly = guest_e["poly"]
                try:
                    old_dist = float(old_poly.distance(others_u))
                    if old_dist <= attach_max_mm and dist > old_dist + 1.0:
                        continue
                except Exception:
                    pass

                _apply_rigid_pose(guest_e["p"], old_poly, test, angle_deg)
                guest_e["poly"] = test
                for e in entries:
                    if e["idx"] == guest_e["idx"]:
                        e["poly"] = test
                        break
                used_guest_idx.add(guest_e["idx"])
                moved += 1
                placed_any = True
                break
            if placed_any:
                break
        if not placed_any:
            break

    return moved
