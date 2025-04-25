# Dockerfile

# 1. Usar la imagen oficial de Playwright para Python
#    (Asegúrate que la versión v1.40.0 coincida o sea compatible con tu requirements.txt)
FROM mcr.microsoft.com/playwright/python:v1.40.0-jammy

# 2. Establecer el directorio de trabajo dentro del contenedor
WORKDIR /app

# 3. Copiar solo el archivo de requerimientos primero para aprovechar el caché de Docker
COPY requirements.txt .

# 4. Instalar las dependencias de Python
#    --no-cache-dir es opcional, pero ayuda a mantener la imagen más pequeña
RUN pip install --no-cache-dir -r requirements.txt

# 5. Copiar el resto del código de tu proyecto al directorio de trabajo en el contenedor
#    Esto incluye tu carpeta 'src', 'config', 'main.py', etc.
COPY . .

# 6. (Opcional pero recomendado) Instalar navegadores explícitamente dentro del Dockerfile
#    Aunque la imagen base los trae, esto asegura que estén presentes
#    y puede facilitar la actualización de versiones si es necesario en el futuro.
#    Comenta esta línea si quieres usar solo los que vienen por defecto en la imagen base.
RUN playwright install --with-deps

# 7. (Opcional) Exponer un puerto si fuera necesario (no parece ser tu caso ahora)
# EXPOSE 8000

# 8. Comando por defecto (puede ser sobrescrito por docker-compose o run.sh)
#    Ejecuta el script principal como punto de entrada
CMD ["python", "main.py", "--help"]
