import os
import psycopg2
from dotenv import load_dotenv

# Cargar variables desde el .env
load_dotenv()

DB_CONFIG = {
    "host": os.getenv("NESTING_DB_HOST", "192.168.2.80"),
    "database": os.getenv("NESTING_DB_NAME", "nestingpro_db"),
    "user": os.getenv("NESTING_DB_USER", "postgres"),
    "password": os.getenv("NESTING_DB_PASSWORD", "nesting123"),
    "port": os.getenv("NESTING_DB_PORT", "5433"),
}

DB_TIMEZONE = os.getenv("DB_TIMEZONE", "America/Chihuahua")

def db_connect():
    conexion = psycopg2.connect(**DB_CONFIG)
    cursor = conexion.cursor()
    cursor.execute(f"SET TIME ZONE '{DB_TIMEZONE}';")
    cursor.close()
    return conexion

# DXF Rutas globales 
DXF_ROOT_DIR = os.getenv("DXF_ROOT_DIR", r"C:\NESTING_DXFS")
DXF_MAP_FILE = os.getenv("DXF_MAP_FILE", r"C:\NESTING_DXFS\dxf_map.json")
