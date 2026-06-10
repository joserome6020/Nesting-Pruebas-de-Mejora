# ==============================================================================
# MANUAL OPERATIVO DE GIT Y GITHUB - GRUPO ARGA
# ==============================================================================
# Instructivo estandarizado para la gestión de repositorios y control de versiones.
# Pensado para usuarios nuevos y avanzados en VS Code + terminal.
# Este archivo funciona como manual interno de consulta.
# ==============================================================================


def introduccion_basica():
    """
    =========================================================
    INTRODUCCIÓN: ¿QUÉ ES GIT Y QUÉ ES GITHUB?
    =========================================================
    GIT:
        Es el sistema de control de versiones que trabaja en tu computadora.
        Te permite ver cambios, guardar versiones (commits), regresar versiones,
        crear ramas, fusionar cambios, etc.

    GITHUB:
        Es la plataforma en la nube donde puedes guardar y compartir repositorios Git.
        Ahí puedes colaborar con otras personas, respaldar proyectos y tener historial.

    IDEA CLAVE:
        - Git trabaja localmente.
        - GitHub es el respaldo/remoto en internet.

    FLUJO GENERAL:
        1. Modificas archivos en tu computadora.
        2. Git detecta cambios.
        3. Tú decides qué cambios guardar.
        4. Haces un commit.
        5. Subes esos commits a GitHub.
    """
    pass


def configuracion_inicial_obligatoria():
    """
    =========================================================
    PASO 0: CONFIGURACIÓN DE IDENTIDAD (SOLO LA PRIMERA VEZ)
    =========================================================
    Si es la primera vez que usas Git en esta computadora, debes configurar
    tu nombre y correo. Si no lo haces, Git puede impedir que guardes commits.

    IMPORTANTE:
        Idealmente usa el mismo correo asociado a tu cuenta de GitHub.

    Configurar nombre:
    $ git config --global user.name "Nombre Apellido"

    Configurar correo:
    $ git config --global user.email "tu_correo@ejemplo.com"

    Verificar lo que quedó guardado:
    $ git config --global --list

    Ver solo el nombre:
    $ git config --global user.name

    Ver solo el correo:
    $ git config --global user.email

    Si te equivocaste:
    $ git config --global user.name "Nombre Correcto"
    $ git config --global user.email "correo_correcto@ejemplo.com"
    """
    pass


def diagnostico_rapido_antes_de_hacer_cualquier_cosa():
    """
    =========================================================
    PASO 1: DIAGNÓSTICO RÁPIDO DEL PROYECTO ACTUAL
    =========================================================
    Antes de subir, clonar o conectar un proyecto, revisa siempre dónde estás
    parado y si esa carpeta ya es un repositorio Git.

    Ver carpeta actual:
    $ pwd

    Ver contenido de la carpeta:
    $ ls

    Ver si ya es repositorio Git:
    $ git status

    Si aparece algo como:
        fatal: not a git repository
    entonces esa carpeta todavía NO tiene Git inicializado.

    Ver a qué repositorio remoto está conectado:
    $ git remote -v

    Ver rama actual:
    $ git branch

    Recomendación:
        Nunca hagas 'git add .' si no estás seguro de estar en la carpeta
        correcta del proyecto.
    """
    pass


def como_descargar_un_proyecto_existente():
    """
    =========================================================
    ESCENARIO 1: CLONAR UN PROYECTO EXISTENTE DESDE GITHUB
    =========================================================
    Usar esto cuando el repositorio ya existe en GitHub y quieres descargarlo
    a tu computadora para empezar a trabajar.

    Paso 1:
        Ve en terminal a la carpeta donde quieres guardar el proyecto.

    Paso 2:
        Copia la URL del repositorio desde GitHub (botón verde "Code").

    Paso 3:
        Clona el proyecto:
    $ git clone https://github.com/USUARIO_O_ORG/NOMBRE_DEL_REPO.git

    Paso 4:
        Entra a la carpeta del proyecto:
    $ cd NOMBRE_DEL_REPO

    Paso 5:
        Revisa el estado:
    $ git status

    Resultado esperado:
        Debe decir algo como "working tree clean".

    NOTA:
        Si el repositorio es privado, debes tener acceso previamente
        como colaborador o miembro autorizado.
    """
    pass


def como_crear_y_subir_un_proyecto_nuevo_a_un_repo_vacio():
    """
    =========================================================
    ESCENARIO 2: CREAR UN PROYECTO NUEVO Y SUBIRLO A UN REPO VACÍO
    =========================================================
    Este es el escenario ideal.
    Úsalo cuando en GitHub el repositorio todavía está vacío o sin contenido útil.

    Paso 1:
        Entra a la raíz del proyecto local:
    $ cd /ruta/de/tu/proyecto

    Paso 2:
        Inicializa Git:
    $ git init

    Paso 3:
        Revisa el estado:
    $ git status

    Paso 4:
        Agrega archivos:
    $ git add .

    Paso 5:
        Crea el primer commit:
    $ git commit -m "Commit inicial: estructura base del proyecto"

    Paso 6:
        Renombra la rama principal a main:
    $ git branch -M main

    Paso 7:
        Conecta con GitHub:
    $ git remote add origin https://github.com/USUARIO_O_ORG/NOMBRE_DEL_REPO.git

    Paso 8:
        Verifica remoto:
    $ git remote -v

    Paso 9:
        Sube el proyecto:
    $ git push -u origin main

    Resultado:
        Tu proyecto local quedará enlazado con el repositorio remoto.
    """
    pass


def como_subir_un_proyecto_a_un_repo_que_no_esta_vacio():
    """
    =========================================================
    ESCENARIO 3: EL REPO EN GITHUB YA EXISTE Y YA TIENE ARCHIVOS
    =========================================================
    Úsalo cuando GitHub ya tiene contenido, por ejemplo:
        - README.md
        - .gitignore
        - licencia
        - archivos previos del proyecto

    CASO MÁS SEGURO:
        Lo ideal es primero clonar ese repo y luego meter tus archivos ahí.
        Así evitas conflictos innecesarios.

    Método recomendado:
    1. Clona el repositorio:
    $ git clone https://github.com/USUARIO_O_ORG/NOMBRE_DEL_REPO.git

    2. Entra al repo:
    $ cd NOMBRE_DEL_REPO

    3. Copia dentro de esa carpeta tus archivos del proyecto.

    4. Revisa estado:
    $ git status

    5. Agrega cambios:
    $ git add .

    6. Crea commit:
    $ git commit -m "Agregar archivos base del proyecto"

    7. Sube:
    $ git push origin main

    MÉTODO ALTERNATIVO (más delicado):
        Si ya estás parado dentro de tu proyecto local y quieres conectarlo
        a un repositorio no vacío, puedes intentar:

    $ git init
    $ git remote add origin https://github.com/USUARIO_O_ORG/NOMBRE_DEL_REPO.git
    $ git branch -M main
    $ git pull origin main --allow-unrelated-histories

    Después resuelves conflictos si aparecen, y luego:
    $ git add .
    $ git commit -m "Fusionar contenido local con remoto"
    $ git push origin main

    NOTA:
        Usa el método alternativo solo si entiendes que pueden aparecer
        conflictos por historiales diferentes.
    """
    pass


def flujo_diario_de_trabajo():
    """
    =========================================================
    ESCENARIO 4: FLUJO DE TRABAJO DIARIO
    =========================================================
    Este es el proceso normal cuando ya tienes el proyecto clonado y conectado.

    Paso 1: Traer cambios recientes antes de modificar
    $ git pull origin main

    Paso 2: Revisar qué cambió
    $ git status

    Paso 3: Agregar todos los cambios
    $ git add .

    Paso 4: Crear commit
    $ git commit -m "Descripción clara del cambio"

    Paso 5: Subir cambios
    $ git push origin main

    CONSEJO:
        Haz commits pequeños y con mensajes claros.
        Ejemplos:
            - "Corregir exportación de PDF"
            - "Agregar validación de work order"
            - "Ajustar conexión a PostgreSQL"
    """
    pass


def como_subir_un_solo_archivo():
    """
    =========================================================
    ESCENARIO 5: SUBIR SOLO UN ARCHIVO ESPECÍFICO
    =========================================================
    Úsalo cuando no quieres subir todos los cambios del proyecto,
    sino únicamente uno o algunos archivos.

    Ver estado:
    $ git status

    Agregar solo un archivo:
    $ git add ruta/al/archivo.py

    Ejemplo:
    $ git add interface/tab_files.py

    Si quieres agregar varios específicos:
    $ git add main.py config.py interface/tab_nesting.py

    Crear commit:
    $ git commit -m "Actualizar únicamente archivos seleccionados"

    Subir:
    $ git push origin main
    """
    pass


def como_subir_todos_menos_algunos_archivos():
    """
    =========================================================
    ESCENARIO 6: QUIERO SUBIR TODO, MENOS ALGUNOS ARCHIVOS
    =========================================================
    Para eso se usa .gitignore.

    Crea o edita el archivo .gitignore en la raíz del proyecto.

    Ejemplo típico para Python:
        __pycache__/
        *.pyc
        .venv/
        .env
        *.log
        *.tmp

    También puedes ignorar carpetas internas del proyecto:
        build/
        dist/
        TEMP_PROCESSED/
        Contexto_Rapido/

    Si un archivo ya fue subido antes, agregarlo al .gitignore NO lo quita automáticamente.
    Para dejar de rastrearlo sin borrarlo del disco:
    $ git rm --cached nombre_archivo
    o para carpeta:
    $ git rm --cached -r nombre_carpeta

    Después:
    $ git add .gitignore
    $ git commit -m "Actualizar reglas de ignorado"
    $ git push origin main
    """
    pass


def como_compartir_un_repo_privado():
    """
    =========================================================
    ESCENARIO 7: COMPARTIR UN REPOSITORIO PRIVADO
    =========================================================
    Si el repositorio es privado, NO basta con mandar el link.
    La otra persona necesita acceso autorizado.

    Proceso general:
    1. Entrar al repositorio en GitHub.
    2. Ir a Settings.
    3. Ir a Collaborators & teams o a la sección de acceso equivalente.
    4. Elegir Add people o Add teams.
    5. Buscar al usuario por username o correo.
    6. Enviar invitación.

    IMPORTANTE:
        La otra persona debe aceptar la invitación.
        Mientras esté como "Pending invite", todavía no tendrá acceso.

    Si no conoces el correo:
        Pide el username de GitHub.

    Si no conoces el username:
        Pide el correo asociado a la cuenta o que primero te compartan
        su usuario de GitHub.

    Si la persona todavía no tiene GitHub:
        Primero debe crear cuenta.

    NOTA:
        Si el repo es privado, el link por sí solo no da acceso.
    """
    pass


def como_cambiar_el_repositorio_remoto_de_un_proyecto():
    """
    =========================================================
    ESCENARIO 8: MI PROYECTO YA APUNTA A OTRO REPO Y QUIERO CAMBIARLO
    =========================================================
    Muy útil cuando:
        - migras un proyecto a otro repositorio
        - antes apuntaba a otro repo
        - clonaste/rehiciste algo y ahora quieres enlazarlo a otro destino

    Ver remotos actuales:
    $ git remote -v

    Quitar origin actual:
    $ git remote remove origin

    Agregar nuevo origin:
    $ git remote add origin https://github.com/USUARIO_O_ORG/NUEVO_REPO.git

    Verificar:
    $ git remote -v

    Si ya tienes commits hechos localmente:
    $ git push -u origin main
    """
    pass


def como_reemplazar_con_tu_proyecto_local_un_repo_tuyo_que_ya_existe():
    """
    =========================================================
    ESCENARIO 9: QUIERO QUE EL REPO REMOTO QUEDE IGUAL A MI PROYECTO LOCAL
    =========================================================
    ESTE ESCENARIO ES DELICADO.
    Úsalo solo si:
        - el repositorio es tuyo
        - estás seguro de que quieres reemplazar el contenido remoto
        - sabes que no vas a borrar trabajo ajeno por error

    RECOMENDACIÓN:
        Haz respaldo antes. Por ejemplo, clona el repo actual en otra carpeta.

    Flujo sugerido:
    1. Revisa remoto:
    $ git remote -v

    2. Si hace falta, conecta el proyecto a ese repositorio:
    $ git remote remove origin
    $ git remote add origin https://github.com/USUARIO_O_ORG/NOMBRE_DEL_REPO.git

    3. Asegúrate de tener commit local:
    $ git add .
    $ git commit -m "Preparar reemplazo completo del repositorio"

    4. Fuerza el estado local hacia remoto:
    $ git push -u origin main --force-with-lease

    IMPORTANTE:
        'force-with-lease' es más seguro que '--force', pero sigue siendo
        una operación potencialmente destructiva.
    """
    pass


def como_corregir_si_olvide_hacer_commit():
    """
    =========================================================
    ESCENARIO 10: YA HICE git add, PERO NO HE HECHO COMMIT
    =========================================================
    Ver estado:
    $ git status

    Si quieres quitar un archivo del área de preparación:
    $ git restore --staged ruta/al/archivo.py

    Si quieres quitar todos:
    $ git restore --staged .

    Tus cambios en el archivo NO se borran.
    Solo salen de la zona lista para commit.
    """
    pass


def como_corregir_el_ultimo_commit_sin_perder_cambios():
    """
    =========================================================
    ESCENARIO 11: QUIERO DESHACER EL ÚLTIMO COMMIT PERO CONSERVAR MIS CAMBIOS
    =========================================================
    Útil si:
        - escribiste mal el mensaje
        - olvidaste incluir un archivo
        - quieres rehacer el commit

    Deshacer último commit, conservando cambios preparados:
    $ git reset --soft HEAD~1

    Después puedes:
    $ git add .
    $ git commit -m "Nuevo mensaje correcto"

    Si solo quieres cambiar el mensaje del último commit:
    $ git commit --amend -m "Mensaje corregido"
    """
    pass


def como_resolver_error_remote_origin_already_exists():
    """
    =========================================================
    ESCENARIO 12: ERROR 'remote origin already exists'
    =========================================================
    Significa que el proyecto ya tiene un origin configurado.

    Ver remotos:
    $ git remote -v

    Quitar origin:
    $ git remote remove origin

    Agregar el nuevo:
    $ git remote add origin https://github.com/USUARIO_O_ORG/NOMBRE_DEL_REPO.git
    """
    pass


def como_resolver_error_src_refspec_main_does_not_match_any():
    """
    =========================================================
    ESCENARIO 13: ERROR 'src refspec main does not match any'
    =========================================================
    Significa normalmente que:
        - todavía no has hecho ningún commit
        - o la rama principal no se llama main

    Solución típica:
    $ git add .
    $ git commit -m "Commit inicial"
    $ git branch -M main
    $ git push -u origin main
    """
    pass


def como_resolver_error_non_fast_forward():
    """
    =========================================================
    ESCENARIO 14: ERROR 'non-fast-forward'
    =========================================================
    Significa que el repositorio remoto tiene cambios que tu copia local
    todavía no tiene.

    SOLUCIÓN NORMAL:
    $ git pull origin main
    # Resolver conflictos si aparecen
    $ git add .
    $ git commit -m "Resolver conflictos"
    $ git push origin main

    Si de verdad quieres reemplazar el remoto con tu versión local y es tuyo:
    $ git push origin main --force-with-lease

    ADVERTENCIA:
        No uses force sin entender el impacto.
    """
    pass


def como_resolver_conflictos_basicos():
    """
    =========================================================
    ESCENARIO 15: APARECIERON CONFLICTOS AL HACER PULL O MERGE
    =========================================================
    Cuando Git no sabe combinar cambios automáticamente, marca conflictos.

    Proceso:
    1. Abrir los archivos marcados.
    2. Buscar bloques como:
        <<<<<<< HEAD
        tu versión
        =======
        otra versión
        >>>>>>> rama/remoto

    3. Editar manualmente dejando solo el contenido correcto.
    4. Guardar el archivo.
    5. Marcarlo como resuelto:
    $ git add archivo_conflictivo.py

    6. Crear commit:
    $ git commit -m "Resolver conflicto en archivo_conflictivo.py"

    7. Subir:
    $ git push origin main
    """
    pass


def como_ver_historial_y_cambios():
    """
    =========================================================
    ESCENARIO 16: QUIERO VER HISTORIAL O ENTENDER QUÉ CAMBIÓ
    =========================================================
    Ver historial resumido:
    $ git log --oneline

    Ver historial detallado:
    $ git log

    Ver diferencias aún no preparadas:
    $ git diff

    Ver diferencias ya preparadas para commit:
    $ git diff --staged

    Ver último commit:
    $ git show
    """
    pass


def como_trabajar_con_ramas_sin_complicar_demasiado():
    """
    =========================================================
    ESCENARIO 17: RAMAS BÁSICAS PARA TRABAJAR SIN ROMPER main
    =========================================================
    Ver ramas:
    $ git branch

    Crear nueva rama:
    $ git checkout -b nombre_de_rama

    Ejemplo:
    $ git checkout -b mejora_exportacion_pdf

    Trabajar normalmente:
    $ git add .
    $ git commit -m "Trabajar mejora exportación PDF"

    Subir rama:
    $ git push -u origin mejora_exportacion_pdf

    Volver a main:
    $ git checkout main

    Actualizar main:
    $ git pull origin main

    NOTA:
        Para usuarios nuevos, si trabajan solos, pueden usar solo main.
        Si trabajan en equipo o en cambios delicados, es mejor usar ramas.
    """
    pass


def como_bajar_cambios_sin_subir_nada():
    """
    =========================================================
    ESCENARIO 18: SOLO QUIERO ACTUALIZAR MI COPIA LOCAL
    =========================================================
    Si no vas a subir nada y solo quieres traer lo nuevo del remoto:

    $ git pull origin main

    Si quieres primero ver que existe en remoto:
    $ git fetch origin

    Luego revisar ramas:
    $ git branch -a
    """
    pass


def como_subir_cambios_despues_de_clonar_un_repo_privado():
    """
    =========================================================
    ESCENARIO 19: YA ME DIERON ACCESO A UN REPO PRIVADO
    =========================================================
    Flujo:
    1. Aceptar invitación en GitHub.
    2. Clonar repo:
    $ git clone https://github.com/USUARIO_O_ORG/NOMBRE_DEL_REPO.git

    3. Entrar:
    $ cd NOMBRE_DEL_REPO

    4. Trabajar y guardar:
    $ git add .
    $ git commit -m "Descripción del cambio"

    5. Subir:
    $ git push origin main
    """
    pass


def buenas_practicas_y_seguridad():
    """
    =========================================================
    BONUS: BUENAS PRÁCTICAS Y SEGURIDAD
    =========================================================
    1. Nunca subas:
        - contraseñas
        - tokens
        - archivos .env con secretos
        - bases de datos locales
        - archivos temporales
        - entornos virtuales
        - compilados innecesarios

    2. Usa .gitignore desde el inicio.

    3. Haz commits con mensajes claros.

    4. Antes de push en proyectos compartidos, haz:
       $ git pull origin main

    5. No uses:
       $ git push --force
       salvo que realmente entiendas lo que estás haciendo.

    6. Confirma siempre que estás en la carpeta correcta:
       $ pwd
       $ ls
       $ git status

    7. Si el repo es privado, el link no da acceso por sí solo.

    8. Si el cambio es grande, crea una rama.

    9. Si un archivo pesado o basura ya se subió, quítalo del seguimiento
       con 'git rm --cached'.

    10. En VS Code puedes usar Source Control visualmente, pero conviene
        conocer la terminal para cualquier emergencia.
    """
    pass


def plantilla_gitignore_basica_python():
    """
    =========================================================
    PLANTILLA BÁSICA DE .gitignore PARA PYTHON
    =========================================================
    __pycache__/
    *.pyc
    *.pyo
    *.pyd
    .venv/
    venv/
    env/
    .env
    *.log
    *.tmp
    .DS_Store
    Thumbs.db
    build/
    dist/
    """
    pass


def checklist_final_antes_de_subir():
    """
    =========================================================
    CHECKLIST FINAL ANTES DE HACER PUSH
    =========================================================
    [ ] ¿Estoy en la carpeta correcta?
    [ ] ¿Ya revisé git status?
    [ ] ¿No estoy subiendo basura o secretos?
    [ ] ¿Ya hice pull si trabajo en equipo?
    [ ] ¿El mensaje del commit describe bien el cambio?
    [ ] ¿Estoy empujando a la rama correcta?
    [ ] ¿El remoto origin apunta al repo correcto?

    Secuencia rápida final:
    $ git status
    $ git add .
    $ git commit -m "Mensaje claro"
    $ git push origin main
    """
    pass