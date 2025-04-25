#!/bin/bash

# Script para facilitar la ejecución de DataLayer Validator con Docker Compose

# Función para mostrar ayuda
usage() {
  echo "Uso: $0 [--build] [OPCIONES_DE_MAIN.PY]"
  echo "  --build      Construye o reconstruye la imagen Docker antes de ejecutar."
  echo "  Las demás opciones se pasan directamente a main.py (ej. --url, --json, --interactive)."
  echo ""
  echo "Ejemplos:"
  echo "  $0 --build # Solo construir"
  echo "  $0 --url <url> --json <archivo.json> # Ejecutar validación"
  echo "  $0 --build --url <url> --json <archivo.json> --interactive # Construir y ejecutar modo interactivo"
  exit 1
}

# --- Manejo de X11 para GUI en Linux (Modo Interactivo) ---
# Intenta detectar si se necesita GUI y si estamos en Linux
NEEDS_GUI=0
INTERACTIVE_FLAG=""
ARGS_PASSTHROUGH=()

# Parsear argumentos para --build y --interactive, y separar los de main.py
BUILD_IMAGE=0
while [[ $# -gt 0 ]]; do
  key="$1"
  case $key in
    --build)
      BUILD_IMAGE=1
      shift # past argument
      ;;
    --interactive)
      NEEDS_GUI=1
      INTERACTIVE_FLAG="--interactive" # Guardar el flag interactivo
      ARGS_PASSTHROUGH+=("$1") # Añadir --interactive a los args a pasar
      shift # past argument
      ;;
    *) # otros argumentos son para main.py
      ARGS_PASSTHROUGH+=("$1") # save it in an array for later
      shift # past argument
      ;;
  esac
done

# Si se necesita GUI y es Linux, preparar X11
if [[ "$NEEDS_GUI" -eq 1 ]] && [[ "$(uname -s)" == "Linux" ]]; then
  echo "Modo interactivo detectado en Linux. Configurando acceso a X11..."
  # Comprobar si ya está permitido
  if ! xhost | grep -q "LOCAL:"; then
      echo "Permitiendo conexiones locales al servidor X..."
      xhost +local:docker
      echo "Si tienes problemas visualizando el navegador, asegúrate que tu servidor X esté corriendo y configurado."
  else
      echo "Acceso local a X11 ya parece estar habilitado."
  fi
  # Asegurarse que DISPLAY esté seteado (usualmente ya lo está)
  if [ -z "$DISPLAY" ]; then
      echo "¡Advertencia! La variable DISPLAY no está seteada. La GUI podría no funcionar."
      # Puedes intentar setearla a :0 por defecto si no existe
      # export DISPLAY=:0
  fi
  echo "X11 preparado."
fi

# Construir la imagen si se pasó --build
if [[ "$BUILD_IMAGE" -eq 1 ]]; then
  echo "Construyendo la imagen Docker..."
  docker-compose build
  if [ $? -ne 0 ]; then
    echo "Error al construir la imagen Docker."
    exit 1
  fi
  # Si solo se pasó --build, no ejecutar nada más
  if [[ ${#ARGS_PASSTHROUGH[@]} -eq 0 ]]; then
     echo "Imagen construida exitosamente."
     exit 0
  fi
fi

# --- Ejecutar el comando ---
echo "Ejecutando DataLayer Validator en Docker..."
# Usar --user para que los archivos generados tengan tu permiso
# Pasar los argumentos recolectados a main.py dentro del contenedor
docker-compose run --rm --service-ports --user "$(id -u):$(id -g)" datalayer-validator python main.py "${ARGS_PASSTHROUGH[@]}"

# Capturar código de salida
EXIT_CODE=$?

echo "Ejecución finalizada con código: $EXIT_CODE"
exit $EXIT_CODE
