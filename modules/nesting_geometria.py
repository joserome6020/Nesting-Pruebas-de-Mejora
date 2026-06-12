import ezdxf
from ezdxf import path
from shapely.geometry import Polygon, LineString, MultiLineString
from shapely.ops import polygonize
from shapely import affinity

class GeometriaDXF:
    def __init__(self, escala=25.4):
        self.escala_dxf = escala
        self.LAYER_OUTER = ["CUT_OUTER", "OUTER", "CORTE_EXTERNO", "IV_OUTER", "0"]
        self.LAYER_INNER = ["CUT_INNER", "INNER", "CORTE_INTERNO", "INTERIOR", "IV_INTERIOR"]
        self.LAYER_MARK = ["MARK", "MARKING", "ETCH", "TEXT", "MARCADO", "IV_MARK"]

    def _entidad_a_lineas(self, entity):
        lineas = []
        try:
            p = path.make_path(entity)
            from modules.nesting_engine.geometry_parser import DXF_FLATTEN_DISTANCE
            vertices = list(p.flattening(distance=DXF_FLATTEN_DISTANCE)) 
            if len(vertices) > 1:
                v_scaled = [(v[0] * self.escala_dxf, v[1] * self.escala_dxf) for v in vertices]
                lineas.append(LineString(v_scaled))
        except Exception: pass
        return lineas

    def recuperar_geometria_robusta(self, ruta_dxf):
        try:
            doc = ezdxf.readfile(ruta_dxf)
            msp = doc.modelspace()
            lines_outer, lines_inner, lines_mark = [], [], []

            for entity in msp:
                layer_name = str(entity.dxf.layer).upper().strip()
                geo = self._entidad_a_lineas(entity)
                if not geo: continue

                if any(x in layer_name for x in self.LAYER_MARK): lines_mark.extend(geo)
                elif any(x in layer_name for x in self.LAYER_INNER): lines_inner.extend(geo)
                elif any(x in layer_name for x in self.LAYER_OUTER): lines_outer.extend(geo)
                else: lines_outer.extend(geo) 

            candidatos_outer = list(polygonize(lines_outer))
            if not candidatos_outer:
                if not lines_outer and lines_inner: candidatos_outer = list(polygonize(lines_inner))
            
            if not candidatos_outer: return None, None
            
            shell_poly = max(candidatos_outer, key=lambda x: x.area)
            holes = []
            seen = set()
            candidatos_inner = list(polygonize(lines_inner)) if lines_inner else []

            def _add_hole(h):
                if h is None or h.is_empty or h.area >= shell_poly.area * 0.995:
                    return
                if not shell_poly.buffer(0.05).contains(h.centroid):
                    return
                c = h.centroid
                key = (round(c.x, 2), round(c.y, 2), round(h.area, 2))
                if key in seen:
                    return
                seen.add(key)
                holes.append(h)

            for h in candidatos_inner:
                _add_hole(h)
            for h in candidatos_outer:
                if h is not shell_poly:
                    _add_hole(h)
            
            pieza_final = Polygon(shell_poly.exterior.coords, [h.exterior.coords for h in holes])
            if not pieza_final.is_valid: pieza_final = pieza_final.buffer(0)
            
            marcas_final = MultiLineString(lines_mark) if lines_mark else MultiLineString()
            return pieza_final, marcas_final
        except Exception:
            return None, None