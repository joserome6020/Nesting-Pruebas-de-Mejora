import psycopg2

def audit_and_clean_nesting(host, port):
    print(f"\n{'='*60}\nNESTING DB ({host}:{port}/nestingpro_db)\n{'='*60}")
    conn = psycopg2.connect(host=host, port=port, dbname="nestingpro_db", user="postgres", password="nesting123")
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name")
    tables = [r[0] for r in cur.fetchall()]
    print("Antes:")
    for t in tables:
        cur.execute(f'SELECT COUNT(*) FROM public."{t}"')
        n = cur.fetchone()[0]
        if n:
            print(f"  {t}: {n}")

    # Orden: hijos primero, respetando FKs
    truncate_order = [
        "lista_largos_cortes",
        "lista_largos_eventos_pieza",
        "lista_largos_eventos_sobrante",
        "lista_largos_remanentes",
        "lista_largos_sobrantes",
        "lista_largos_sesiones",
        "lista_largos_turnos",
        "lista_largos_planes",
        "material_requerido_ldg",
        "lista_largos_swo",
        "lista_largos_job",
        "reporte_cortes",
        "pqart_wo",
        "pqart_swo",
        "material_actual",
        "reportes_dinamicos",
        "diccionario_swo",
        "components",
        "costos_prorrateo",
        "sheets",
        "erp_piezas_tracking",
        "erp_placas_tracking",
        "erp_work_orders",
        "erp_super_work_orders",
        "erp_jobs",
        "jobs",
    ]
    remaining = [t for t in tables if t not in truncate_order]
    for t in truncate_order + remaining:
        if t not in tables:
            continue
        try:
            cur.execute(f'TRUNCATE TABLE public."{t}" RESTART IDENTITY CASCADE')
            print(f"  [OK] {t}")
        except Exception as e:
            print(f"  [SKIP] {t}: {e}")

    print("Después:")
    for t in tables:
        cur.execute(f'SELECT COUNT(*) FROM public."{t}"')
        n = cur.fetchone()[0]
        if n:
            print(f"  {t}: {n}")
    cur.close()
    conn.close()


def audit_and_clean_vsm(host, port, user, password):
    print(f"\n{'='*60}\nVSM / CentralizedSystem ({host}:{port}/foldertree)\n{'='*60}")
    conn = psycopg2.connect(host=host, port=port, dbname="foldertree", user=user, password=password)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name")
    tables = [r[0] for r in cur.fetchall()]
    print("Antes:")
    for t in ("jobs", "job_history", "work_orders", "dossiers", "dossier_files", "defect_records"):
        if t in tables:
            cur.execute(f'SELECT COUNT(*) FROM public."{t}"')
            print(f"  {t}: {cur.fetchone()[0]}")

    vsm_tables = [
        "defect_images",
        "defect_records",
        "dossier_files",
        "dossiers",
        "job_history",
        "work_orders",
        "jobs",
    ]
    for t in vsm_tables:
        if t not in tables:
            continue
        try:
            cur.execute(f'TRUNCATE TABLE public."{t}" RESTART IDENTITY CASCADE')
            print(f"  [OK] {t}")
        except Exception as e:
            print(f"  [SKIP] {t}: {e}")

    print("Después:")
    for t in ("jobs", "job_history", "work_orders"):
        if t in tables:
            cur.execute(f'SELECT COUNT(*) FROM public."{t}"')
            print(f"  {t}: {cur.fetchone()[0]}")
    cur.close()
    conn.close()


if __name__ == "__main__":
    audit_and_clean_nesting("192.168.2.80", 5433)
    try:
        audit_and_clean_vsm("192.168.2.80", 5437, "user", "password")
    except Exception as e:
        print(f"[WARN] foldertree 5437: {e}")
        try:
            audit_and_clean_vsm("192.168.2.80", 5434, "user", "password")
        except Exception as e2:
            print(f"[WARN] foldertree 5434: {e2}")
    print("\nLIMPIEZA COMPLETA")
