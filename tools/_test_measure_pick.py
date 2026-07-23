"""Validación automática del picking de medición (estilo Inventor).

Sin GUI: carga STEP real, indexa aristas B-Rep y comprueba que un punto
cerca de una línea/círculo recupera esa arista (raycast→nearest edge).
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_CAD = _ROOT / "CAD (OCCT)"
sys.path.insert(0, str(_CAD))
sys.path.insert(0, str(_ROOT))

from engine.step_io import (  # noqa: E402
    edge_min_distance,
    extract_measurable_edges,
    load_step_display,
    read_step_shape,
)


def dist2_point_segment(px, py, pz, ax, ay, az, bx, by, bz) -> float:
    abx, aby, abz = bx - ax, by - ay, bz - az
    apx, apy, apz = px - ax, py - ay, pz - az
    ab2 = abx * abx + aby * aby + abz * abz
    if ab2 < 1e-18:
        return apx * apx + apy * apy + apz * apz
    t = max(0.0, min(1.0, (apx * abx + apy * aby + apz * abz) / ab2))
    dx = ax + t * abx - px
    dy = ay + t * aby - py
    dz = az + t * abz - pz
    return dx * dx + dy * dy + dz * dz


def nearest_edge_to_world(edges, hit, max_dist: float):
    hx, hy, hz = hit
    best_id = None
    best_d2 = float(max_dist) ** 2
    for me in edges:
        pl = me.polyline or []
        if len(pl) < 2:
            continue
        xs = [p[0] for p in pl]
        ys = [p[1] for p in pl]
        zs = [p[2] for p in pl]
        pad = max_dist
        if (
            hx < min(xs) - pad
            or hx > max(xs) + pad
            or hy < min(ys) - pad
            or hy > max(ys) + pad
            or hz < min(zs) - pad
            or hz > max(zs) + pad
        ):
            continue
        if me.center and me.radius_in:
            cx, cy, cz = me.center
            vx, vy, vz = hx - cx, hy - cy, hz - cz
            vlen = math.sqrt(vx * vx + vy * vy + vz * vz)
            d_ring = abs(vlen - float(me.radius_in))
            d2 = d_ring * d_ring
            if d2 < best_d2:
                best_d2 = d2
                best_id = int(me.id)
        for i in range(len(pl) - 1):
            a, b = pl[i], pl[i + 1]
            d2 = dist2_point_segment(
                hx, hy, hz, a[0], a[1], a[2], b[0], b[1], b[2]
            )
            if d2 < best_d2:
                best_d2 = d2
                best_id = int(me.id)
    return best_id, math.sqrt(best_d2) if best_id is not None else 1e9


def point_on_polyline(pl, t: float = 0.5):
    if len(pl) == 1:
        return pl[0]
    # punto a mitad de longitud acumulada
    seglens = []
    total = 0.0
    for i in range(len(pl) - 1):
        a, b = pl[i], pl[i + 1]
        L = math.sqrt(sum((b[k] - a[k]) ** 2 for k in range(3)))
        seglens.append(L)
        total += L
    if total < 1e-12:
        return pl[len(pl) // 2]
    target = t * total
    acc = 0.0
    for i, L in enumerate(seglens):
        if acc + L >= target and L > 1e-12:
            u = (target - acc) / L
            a, b = pl[i], pl[i + 1]
            return tuple(a[k] + u * (b[k] - a[k]) for k in range(3))
        acc += L
    return pl[-1]


def main() -> int:
    step = Path(
        r"C:\Users\jose_rosales\OneDrive - grupoarga.com\Escritorio\Nesteos Locales"
        r"\GIGA\GIGA FLUIDSTACK\MODEL CORE FILES\W.O. 30 X6\ARGA MODEL CORE"
        r"\NESTING\NESTEOS DE COBRE\STEP\NESTING_0.25_RTZCU1-H1.step"
    )
    if len(sys.argv) > 1:
        step = Path(sys.argv[1])
    if not step.is_file():
        print(f"FAIL: STEP no encontrado: {step}")
        return 2

    print(f"STEP: {step.name} ({step.stat().st_size} bytes)")
    shape = read_step_shape(step)
    edges = extract_measurable_edges(shape, deflection=0.04)
    print(f"aristas medibles: {len(edges)}")
    kinds: dict[str, int] = {}
    for e in edges:
        kinds[e.kind] = kinds.get(e.kind, 0) + 1
    print(f"kinds: {kinds}")

    fails = 0
    lines = [e for e in edges if e.kind == "line" and e.length_in > 0.5]
    circles = [e for e in edges if e.kind == "circle" and e.is_full_circle and e.radius_in]
    arcs = [e for e in edges if e.kind == "arc" and e.radius_in]

    # Tolerancia tipo visor
    data = load_step_display(step, deflection=0.15, edge_deflection=0.06, include_measure=False)
    xs = [v[0] for v in data.mesh.vertices]
    ys = [v[1] for v in data.mesh.vertices]
    zs = [v[2] for v in data.mesh.vertices]
    diag = math.sqrt(
        (max(xs) - min(xs)) ** 2 + (max(ys) - min(ys)) ** 2 + (max(zs) - min(zs)) ** 2
    )
    tol = max(0.05, 0.04 * diag)
    print(f"mesh tris={data.n_tris} diag={diag:.3f} tol3d={tol:.4f}")

    # --- Test líneas ---
    n_line_ok = 0
    for me in lines[:8]:
        hit = point_on_polyline(me.polyline, 0.5)
        # ruido pequeño (como imprecisión de raycast)
        hit2 = (hit[0] + 0.001, hit[1] - 0.001, hit[2] + 0.001)
        eid, dist = nearest_edge_to_world(edges, hit2, max_dist=tol)
        ok = eid == me.id
        n_line_ok += int(ok)
        if not ok:
            fails += 1
            print(f"  LINE FAIL id={me.id} L={me.length_in:.4f} got={eid} dist={dist:.4f}")
    print(f"líneas: {n_line_ok}/{min(8, len(lines))} OK (candidatas>{0.5}in: {len(lines)})")
    if not lines:
        print("  WARN: no hay líneas > 0.5 in")
        fails += 1

    # --- Test círculos ---
    n_circ_ok = 0
    for me in circles[:6]:
        # punto en el anillo
        c = me.center
        assert c is not None and me.radius_in
        hit = (c[0] + me.radius_in, c[1], c[2])
        eid, dist = nearest_edge_to_world(edges, hit, max_dist=tol)
        ok = eid == me.id
        # si hay dos semi-arcos, aceptar mismo radio/centro
        if not ok and eid is not None:
            other = edges[eid]
            if (
                other.center
                and me.center
                and abs((other.radius_in or 0) - me.radius_in) < 1e-3
                and math.sqrt(sum((other.center[i] - me.center[i]) ** 2 for i in range(3))) < 1e-3
            ):
                ok = True
        n_circ_ok += int(ok)
        if not ok:
            fails += 1
            print(
                f"  CIRCLE FAIL id={me.id} R={me.radius_in:.4f} got={eid} dist={dist:.4f}"
            )
    print(f"círculos: {n_circ_ok}/{min(6, len(circles))} OK (full: {len(circles)})")
    if not circles and not arcs:
        print("  WARN: no hay círculos/arcos")
        fails += 1

    # --- Test arcos / semicírculos ---
    n_arc_ok = 0
    for me in arcs[:6]:
        hit = point_on_polyline(me.polyline, 0.5)
        hit2 = (hit[0] + 0.002, hit[1], hit[2] - 0.001)
        eid, dist = nearest_edge_to_world(edges, hit2, max_dist=tol)
        ok = eid == me.id
        if not ok and eid is not None and me.center and me.radius_in:
            other = edges[eid]
            if (
                other.center
                and abs((other.radius_in or -1) - me.radius_in) < 1e-3
                and math.sqrt(sum((other.center[i] - me.center[i]) ** 2 for i in range(3)))
                < 1e-3
            ):
                ok = True
        n_arc_ok += int(ok)
        if not ok:
            fails += 1
            print(f"  ARC FAIL id={me.id} R={me.radius_in} got={eid} dist={dist:.4f}")
    print(f"arcos: {n_arc_ok}/{min(6, len(arcs))} OK (arcs: {len(arcs)})")

    # --- Distancia entre dos aristas ---
    if len(lines) >= 2:
        # Buscar par con distancia > 0 (no coincidentes)
        paired = None
        for i in range(min(20, len(lines))):
            for j in range(i + 1, min(20, len(lines))):
                info = edge_min_distance(lines[i].edge, lines[j].edge)
                if info and info[0] > 1e-4:
                    paired = (lines[i], lines[j], info)
                    break
            if paired:
                break
        if paired is None:
            a, b = lines[0], lines[1]
            info = edge_min_distance(a.edge, b.edge)
            print(f"distancia linea-linea (puede ser 0 si comparten vertice): {None if info is None else round(info[0], 4)}")
            if info is None:
                fails += 1
        else:
            a, b, info = paired
            d, p0, p1 = info
            print(f"distancia linea-linea: {d:.4f} in entre id={a.id} y id={b.id}")
    elif circles and lines:
        info = edge_min_distance(lines[0].edge, circles[0].edge)
        print(f"distancia linea-circulo: {None if info is None else round(info[0], 4)} in")
        if info is None:
            fails += 1

    # --- VTK offscreen pick (si esta disponible) ---
    vtk_ok = False
    try:
        import vtk

        me = lines[0] if lines else (arcs[0] if arcs else None)
        if me is not None:
            pts = vtk.vtkPoints()
            cells = vtk.vtkCellArray()
            for v in data.mesh.vertices:
                pts.InsertNextPoint(*v)
            for a, b, c in data.mesh.triangles:
                cells.InsertNextCell(3)
                cells.InsertCellPoint(a)
                cells.InsertCellPoint(b)
                cells.InsertCellPoint(c)
            poly = vtk.vtkPolyData()
            poly.SetPoints(pts)
            poly.SetPolys(cells)
            mapper = vtk.vtkPolyDataMapper()
            mapper.SetInputData(poly)
            actor = vtk.vtkActor()
            actor.SetMapper(mapper)

            ren = vtk.vtkRenderer()
            ren.AddActor(actor)
            renWin = vtk.vtkRenderWindow()
            renWin.SetOffScreenRendering(1)
            renWin.AddRenderer(ren)
            renWin.SetSize(800, 600)
            cam = ren.GetActiveCamera()
            cam.ParallelProjectionOn()
            ren.ResetCamera()
            renWin.Render()

            hit_w = point_on_polyline(me.polyline, 0.5)
            ren.SetWorldPoint(hit_w[0], hit_w[1], hit_w[2], 1.0)
            ren.WorldToDisplay()
            dx, dy, _dz = ren.GetDisplayPoint()
            picker = vtk.vtkCellPicker()
            picker.SetTolerance(0.02)
            picker.AddPickList(actor)
            picker.PickFromListOn()
            ok = picker.Pick(float(dx), float(dy), 0.0, ren)
            if ok and picker.GetCellId() >= 0:
                hit = tuple(float(v) for v in picker.GetPickPosition())
                eid, dist = nearest_edge_to_world(edges, hit, max_dist=tol)
                vtk_ok = eid == me.id or (
                    eid is not None and abs(edges[eid].length_in - me.length_in) < 1e-3
                )
                print(
                    f"VTK raycast->edge: pick_ok={bool(ok)} eid={eid} expect={me.id} "
                    f"dist={dist:.4f} PASS={vtk_ok}"
                )
                if not vtk_ok:
                    fails += 1
            else:
                print("VTK raycast: no hit en malla (vista de canto / proyección)")
    except Exception as exc:
        print(f"VTK offscreen skipped/error: {exc}")

    if fails:
        print(f"RESULTADO: FAIL ({fails} fallos)")
        return 1
    extra = " + VTK pick OK" if vtk_ok else ""
    print(f"RESULTADO: PASS - extraccion + nearest-edge 3D OK{extra}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
