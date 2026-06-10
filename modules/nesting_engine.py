# Este archivo actúa como puente para mantener la compatibilidad 
# con el resto de la aplicación Arga Nesting Suite.
from .nesting_engine.manager import MotorNesting

# Ahora, cualquier archivo que haga 'import nesting_engine' 
# seguirá funcionando pero usando el código nuevo y seccionado.