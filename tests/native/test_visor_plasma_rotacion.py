"""Candado 2026-08-14l — rotar una pieza plasma no borra el énfasis.

Bug real: `set_plasma_contour_emphasis(True)` pintaba el OUTER en rojo y
escribía "+X"" en el panel inferior. Al pulsar ROTAR 90° la vista se
regeneraba desde cero y el énfasis desaparecía — el usuario percibía "se
quitó el offset" aunque el DXF compensado seguía intacto en disco.

El fix añade dos flags de estado (`_plasma_emphasis_on`,
`_plasma_emphasis_offset_in`) y hace que `renderizar_dxf` los honre al
recargar el modelo (siempre que la ruta no cambie).
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

VISOR = RAIZ / "interface" / "qt" / "visualizer.py"


def _tree() -> ast.AST:
    return ast.parse(VISOR.read_text(encoding="utf-8"))


def _find_method(cls_name: str, method: str) -> ast.FunctionDef:
    for node in ast.walk(_tree()):
        if isinstance(node, ast.ClassDef) and node.name == cls_name:
            for child in node.body:
                if isinstance(child, ast.FunctionDef) and child.name == method:
                    return child
    raise AssertionError(f"{cls_name}.{method} no está en visualizer.py")


def _assigns_attribute(fn: ast.FunctionDef, attr: str) -> bool:
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "self"
                    and target.attr == attr
                ):
                    return True
    return False


def _calls_self_method(fn: ast.FunctionDef, method: str) -> bool:
    for node in ast.walk(fn):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "self"
            and node.func.attr == method
        ):
            return True
    return False


_CLASE_VISOR = "VisorDXF"


def test_emphasis_persiste_estado() -> None:
    """set_plasma_contour_emphasis debe fijar los flags para que rotar los lea."""
    fn = _find_method(_CLASE_VISOR, "set_plasma_contour_emphasis")
    assert _assigns_attribute(fn, "_plasma_emphasis_on"), (
        "set_plasma_contour_emphasis debe persistir _plasma_emphasis_on"
    )
    assert _assigns_attribute(fn, "_plasma_emphasis_offset_in"), (
        "set_plasma_contour_emphasis debe persistir _plasma_emphasis_offset_in"
    )


def test_renderizar_reaplica_emphasis() -> None:
    """renderizar_dxf debe reaplicar el énfasis si estaba encendido."""
    fn = _find_method(_CLASE_VISOR, "renderizar_dxf")
    fuente = ast.unparse(fn)
    assert "_plasma_emphasis_on" in fuente, (
        "renderizar_dxf debe consultar _plasma_emphasis_on"
    )
    assert "set_plasma_contour_emphasis" in fuente or (
        _calls_self_method(fn, "set_plasma_contour_emphasis")
    ), "renderizar_dxf debe llamar a set_plasma_contour_emphasis para restaurar"


def test_cambio_de_pieza_resetea_emphasis() -> None:
    """Cambiar de DXF debe apagar el énfasis heredado del anterior."""
    fn = _find_method(_CLASE_VISOR, "renderizar_dxf")
    fuente = ast.unparse(fn)
    # El bloque "cambio de ruta" debe resetear los flags. Se detecta buscando
    # una asignación de _plasma_emphasis_on = False dentro del método.
    assert "self._plasma_emphasis_on = False" in fuente, (
        "renderizar_dxf debe resetear _plasma_emphasis_on cuando cambia la ruta"
    )


def test_rotar_reinvoca_renderizar_sin_perder_estado() -> None:
    """rotar_vista_90 llama a renderizar_dxf(self._ruta_actual) — el mismo path."""
    fn = _find_method(_CLASE_VISOR, "rotar_vista_90")
    fuente = ast.unparse(fn)
    assert "self.renderizar_dxf(self._ruta_actual)" in fuente, (
        "rotar_vista_90 debe recargar el mismo path para que el énfasis se reaplique"
    )


def _run_state_machine() -> None:
    """Simulación aislada del ciclo real: emphasis + render + rotar + render.

    No usa Qt: usa dobles minimales que registran las llamadas al ``_cad``.
    Verifica que tras rotar 90° la última operación sobre ``_cad`` sea
    ``emphasize_plasma_outers`` (con el label de compensación intacto).
    """

    class FakeCad:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple, dict]] = []

        def clear_plasma_overlay(self) -> None:
            self.calls.append(("clear_plasma_overlay", (), {}))

        def emphasize_plasma_outers(self, *, label=None) -> None:
            self.calls.append(("emphasize_plasma_outers", (), {"label": label}))

        def set_material(self, m) -> None:
            self.calls.append(("set_material", (m,), {}))

        def load_model(self, model, *, fit=True) -> None:
            self.calls.append(("load_model", (model,), {"fit": fit}))

    class FakeLabel:
        def __init__(self) -> None:
            self.text = ""

        def setText(self, t) -> None:
            self.text = t

        def setStyleSheet(self, _s) -> None:
            pass

    class FakeModel:
        factor_conversion = 25.4
        outer_rings = [[(0, 0), (10, 0), (10, 10), (0, 10)]]

    # Instancia mínima. Se copia sólo la lógica pura (sin Qt).
    from types import SimpleNamespace

    from interface.qt import visualizer as vis_mod

    original_load = vis_mod.load_dxf_part
    vis_mod.load_dxf_part = lambda ruta, rot: FakeModel()
    try:
        v = SimpleNamespace(
            _cad=FakeCad(),
            lbl_plasma=FakeLabel(),
            _ruta_actual=None,
            _rotacion_vista_deg=0,
            _plasma_offset_mm=0.0,
            _plasma_emphasis_on=False,
            _plasma_emphasis_offset_in=None,
            _material="",
            factor_conversion=25.4,
            _persist_rotation_hook=None,
            _plasma_base_metrics=None,
        )
        clase = getattr(vis_mod, _CLASE_VISOR)
        v.set_plasma_contour_emphasis = clase.set_plasma_contour_emphasis.__get__(v)
        v.renderizar_dxf = clase.renderizar_dxf.__get__(v)
        v._reaplicar_overlay_plasma = lambda: None
        v.limpiar_lienzo = lambda: v._cad.calls.append(("limpiar_lienzo", (), {}))
        v._snapshot_metricas_ui = lambda: None
        v._restaurar_metricas_ui = lambda _s: None
        v.rotar_vista_90 = clase.rotar_vista_90.__get__(v)

        v.renderizar_dxf("C:/piezas/compensada.dxf", plasma_offset_mm=0.0)
        v.set_plasma_contour_emphasis(True, offset_in=0.00747)

        emph_calls_pre = [
            c for c in v._cad.calls if c[0] == "emphasize_plasma_outers"
        ]
        assert emph_calls_pre, "el estado inicial debe pintar el OUTER rojo"
        assert v.lbl_plasma.text.startswith("+"), v.lbl_plasma.text

        # ROTAR: replica exactamente lo que hace la UI.
        v.rotar_vista_90()

        # Tras rotar, el énfasis debe estar de nuevo aplicado.
        emph_calls_post = [
            c for c in v._cad.calls if c[0] == "emphasize_plasma_outers"
        ]
        assert len(emph_calls_post) >= 2, (
            f"tras rotar debe reaparecer emphasize_plasma_outers, "
            f"calls={[c[0] for c in v._cad.calls]}"
        )
        assert v.lbl_plasma.text.startswith("+"), (
            f"tras rotar el label debe seguir '+X\"' — actual={v.lbl_plasma.text!r}"
        )
        assert v._plasma_emphasis_on is True
        assert abs(float(v._plasma_emphasis_offset_in) - 0.00747) < 1e-9
    finally:
        vis_mod.load_dxf_part = original_load


def test_rotacion_no_pierde_offset_visualmente() -> None:
    _run_state_machine()


if __name__ == "__main__":
    test_emphasis_persiste_estado()
    test_renderizar_reaplica_emphasis()
    test_cambio_de_pieza_resetea_emphasis()
    test_rotar_reinvoca_renderizar_sin_perder_estado()
    test_rotacion_no_pierde_offset_visualmente()
    print("OK visor_plasma_rotacion")
