# DataLayer Validator - Versión Dockerizada

Esta documentación explica cómo utilizar DataLayer Validator con Docker, lo que facilita su ejecución sin preocuparse por dependencias o problemas de compatibilidad.

## Requisitos

- Docker
- Docker Compose

## Configuración inicial

1. Clona este repositorio:
   ```bash
   git clone https://github.com/Qjordnvb/pythonDatalayerAutomation.git
   cd pythonDatalayerAutomation
   ```

2. Asegúrate de que el script de ejecución tenga permisos:
   ```bash
   chmod +x run.sh
   ```

3. Coloca tus archivos JSON de referencia en el directorio `docs/input/`

## Uso básico

El script `run.sh` simplifica la ejecución de la herramienta. Ejemplos de uso:

### Construir la imagen por primera vez

```bash
./run.sh --build
```

### Ejecutar en modo interactivo

```bash
./run.sh --url "https://sitio-a-validar.com" --json "docs/input/datalayers.json" --interactive
```


### Especificar directorio de salida personalizado

```bash
./run.sh --url "https://sitio-a-validar.com" --json "docs/input/datalayers.json" --output "mis-reportes"
```

## Interfaz gráfica en modo interactivo

Para utilizar el modo interactivo con visualización del navegador en sistemas basados en Linux:

1. Permite conexiones al servidor X desde Docker:
   ```bash
   xhost +local:docker
   ```

2. Descomenta las líneas de volumen X11 en `docker-compose.yml`:
   ```yaml
   volumes:
     - /tmp/.X11-unix:/tmp/.X11-unix
   ```

3. Ejecuta el script con la opción interactiva.

## Estructura de archivos

- `docs/input/`: Coloca aquí tus archivos JSON de referencia
- `docs/output/`: Los reportes generados se guardarán aquí
- `logs/`: Archivos de log generados durante la ejecución

## Solución de problemas

### El navegador no se muestra en modo interactivo

Verifica que:
1. Has permitido conexiones X11 con `xhost +local:docker`
2. Las líneas correspondientes están descomentadas en `docker-compose.yml`
3. La variable `DISPLAY` se está pasando correctamente

### Permisos de archivos

Si encuentras errores de permisos al escribir reportes o logs:

```bash
docker-compose run --rm --user $(id -u):$(id -g) datalayer-validator [opciones]
```

## Personalización avanzada

Para personalizar la configuración del contenedor, puedes editar `docker-compose.yml` y agregar variables de entorno o volúmenes adicionales según tus necesidades.
