# Permite que las pruebas importen "nucleo" corriendo pytest desde la raíz del proyecto.
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
