import pathlib

def listar_archivos(ruta_carpeta, archivo_salida="lista_archivos.txt"):
    # Convertimos la ruta en un objeto Path
    carpeta = pathlib.Path(ruta_carpeta)

    # Verificamos si la ruta realmente existe y es una carpeta
    if not carpeta.exists() or not carpeta.is_dir():
        print(f"Error: La ruta '{ruta_carpeta}' no existe o no es una carpeta.")
        return

    # Abrimos (o creamos) el archivo de texto en modo escritura ('w')
    with open(archivo_salida, 'w', encoding='utf-8') as archivo:
        archivo.write(f"Directorio examinado: {carpeta.resolve()}\n")
        archivo.write("-" * 50 + "\n")
        
        contador = 0
        # Iteramos sobre los elementos dentro de la carpeta
        for elemento in carpeta.iterdir():
            # Nos aseguramos de que sea un archivo y no una subcarpeta
            if elemento.is_file():
                nombre = elemento.stem      # Obtiene el nombre sin el punto y la extensión
                extension = elemento.suffix # Obtiene la extensión (ej: '.pdf', '.jpg')
                
                # Escribimos la información en el txt
                archivo.write(f"Nombre: {nombre} | Extensión: {extension}\n")
                contador += 1
                
        archivo.write("-" * 50 + "\n")
        archivo.write(f"Total de archivos encontrados: {contador}\n")

    print(f"¡Listo! Se han listado {contador} archivos en el documento '{archivo_salida}'.")

# --- Configuración del Script ---
# Reemplaza la ruta entre comillas con la ruta de tu carpeta. 
# La 'r' antes de las comillas ayuda a que Windows lea bien las barras invertidas (\).
mi_ruta = r"X:\ARGA METALS CORPORATE SYSTEM\PRODUCTO_TEST\CLIENTE_TEST\SIEMENS 25097-01\MODEL CORE FILES\AutoDXF" 

# Ejecutamos la función
listar_archivos(mi_ruta)