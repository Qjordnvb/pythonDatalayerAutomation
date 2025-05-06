#!/bin/bash

# Script para facilitar la ejecución de DataLayer Validator con Docker
# MODIFICADO: Detecta automáticamente 'docker compose' o 'docker-compose'

# --- INICIO: Detección del comando Compose ---
# Intentar encontrar 'docker compose' (V2 plugin)
if command -v docker compose &> /dev/null; then
    COMPOSE_CMD="docker compose"
# Si no se encuentra V2, intentar encontrar 'docker-compose' (V1 standalone)
elif command -v docker-compose &> /dev/null; then
    COMPOSE_CMD="docker-compose"
# Si no se encuentra ninguno, mostrar error y salir
else
    echo "Error: No se encontró ni 'docker compose' (V2) ni 'docker-compose' (V1)." >&2
    echo "Por favor, asegúrate de que Docker y Docker Compose estén instalados correctamente." >&2
    exit 1
fi
echo "Usando comando Compose: '$COMPOSE_CMD'"
# --- FIN: Detección del comando Compose ---


# Función para mostrar ayuda (sin cambios)
usage() {
  echo "Uso: $0 [--build] [OPCIONES_DE_MAIN.PY]"
  echo "  --build      Construye o reconstruye la imagen Docker antes de ejecutar."
  echo "  Las demás opciones se pasan directamente a main.py (ej. --url, --json, --interactive)."
  echo ""
  echo "Ejemplos:"
  echo "  $0 --build # Construir o reconstruir imagen"
  echo "  $0 --url <url> --json <archivo.json> # Ejecutar validación (modo no interactivo)"
  echo "  $0 --url <url> --json <archivo.json> --interactive # Ejecutar modo interactivo"
  echo "  $0 --build --url <url> --json <archivo.json> --interactive # Construir y ejecutar modo interactivo"
  exit 1
}

# --- Manejo de X11 para GUI en Linux (Modo Interactivo) ---
# (Lógica sin cambios, solo ajustada para claridad)
NEEDS_GUI=0
ARGS_PASSTHROUGH=() # Array para guardar argumentos para main.py
BUILD_IMAGE=0

# Parsear argumentos para --build y separar los de main.py
while [[ $# -gt 0 ]]; do
  key="$1"
  case $key in
    --build)
      BUILD_IMAGE=1
      shift # quitar argumento
      ;;
    --interactive)
      NEEDS_GUI=1 # El modo interactivo necesita GUI
      ARGS_PASSTHROUGH+=("$1") # Guardar --interactive para main.py
      shift # quitar argumento
      ;;
    *) # otros argumentos son para main.py
      # Asegurarse de pasar argumentos con espacios correctamente
      ARGS_PASSTHROUGH+=("$1")
      shift # quitar argumento
      ;;
  esac
done

# Si se necesita GUI y es Linux, preparar X11 (lógica sin cambios)
if [[ "$NEEDS_GUI" -eq 1 ]] && [[ "$(uname -s)" == "Linux" ]]; then
  echo "Modo interactivo detectado en Linux. Configurando acceso a X11..."
  if ! xhost | grep -q "LOCAL:"; then
      echo "Permitiendo conexiones locales al servidor X..."
      xhost +local:docker
  else
      echo "Acceso local a X11 ya parece estar habilitado."
  fi
  if [ -z "$DISPLAY" ]; then
      echo "¡Advertencia! La variable DISPLAY no está seteada. La GUI podría no funcionar."
  fi
  echo "X11 preparado (asegúrate que las opciones de DISPLAY y volumen X11 estén activas en docker-compose.yml si es necesario)."
fi

# Construir la imagen si se pasó --build
if [[ "$BUILD_IMAGE" -eq 1 ]]; then
  echo "Construyendo la imagen Docker usando '$COMPOSE_CMD'..."
  # --- MODIFICADO: Usar la variable $COMPOSE_CMD ---
  $COMPOSE_CMD build
  if [ $? -ne 0 ]; then
    echo "Error al construir la imagen Docker."
    exit 1
  fi
  # Si solo se pasó --build, no ejecutar nada más
  # (Solo ejecutar si hay más argumentos además de --build)
   if [[ ${#ARGS_PASSTHROUGH[@]} -eq 0 ]]; then
     echo "Imagen construida/actualizada exitosamente."
     exit 0
  fi
fi

# Verificar si hay argumentos para main.py antes de intentar ejecutar
if [[ ${#ARGS_PASSTHROUGH[@]} -eq 0 ]]; then
    echo "No se especificaron argumentos para la ejecución (ej. --url, --json)."
    usage # Mostrar ayuda si no hay nada que ejecutar
fi


# --- Ejecutar el comando ---
echo "Ejecutando DataLayer Validator en Docker usando '$COMPOSE_CMD'..."
# Usar --user para que los archivos generados tengan tu permiso
# Pasar los argumentos recolectados a main.py dentro del contenedor

# --- MODIFICADO: Usar la variable $COMPOSE_CMD ---
$COMPOSE_CMD run --rm --service-ports --user "$(id -u):$(id -g)" datalayer-validator python main.py "${ARGS_PASSTHROUGH[@]}"

# Capturar código de salida
EXIT_CODE=$?

echo "Ejecución finalizada con código: $EXIT_CODE"
exit $EXIT_CODE
