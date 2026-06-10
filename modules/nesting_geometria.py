import ezdxf
from ezdxf import path
from shapely.geometry import Polygon, LineString, MultiLineString
from shapely.ops import polygonize
from shapely import affinity

class GeometriaDXF:
    def __init__(self, escala=25.4):
        self.escala_dxf = escala
        self.LAYER_OUTER = ["CUT_OUTER", "OUTER", "CORTE_EXTERNO", "0"] 
        self.LAYER_INNER = ["CUT_INNER", "INNER", "CORTE_INTERNO"]
        self.LAYER_MARK = ["MARK", "MARKING", "ETCH", "TEXT", "MARCADO"]

    def _entidad_a_lineas(self, entity):
        lineas = []
        try:
            p = path.make_path(entity)
            vertices = list(p.flattening(distance=0.5)) 
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
            if lines_inner:
                candidatos_inner = list(polygonize(lines_inner))
                for h in candidatos_inner:
                    if shell_poly.buffer(0.1).contains(h.centroid): holes.append(h)
            
            pieza_final = Polygon(shell_poly.exterior.coords, [h.exterior.coords for h in holes])
            if not pieza_final.is_valid: pieza_final = pieza_final.buffer(0)
            
            marcas_final = MultiLineString(lines_mark) if lines_mark else MultiLineString()
            return pieza_final, marcas_final
        except Exception:
            return None, None