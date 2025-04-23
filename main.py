#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import json
import argparse
import logging
from datetime import datetime

# Configuración de logging
from config.logging_config import configure_logging

# Importaciones de los módulos principales
from src.parser.schema_builder import SchemaBuilder
from src.validator.datalayer_validator import DataLayerValidator
from src.reporter.report_generator import ReportGenerator


def load_config(config_path):
    """Carga la configuración desde un archivo JSON"""
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logging.error(f"Error al cargar la configuración: {e}")
        sys.exit(1)


def load_datalayers_json(json_path):
    """Carga los DataLayers de referencia desde un archivo JSON"""
    # Calcular la ruta absoluta basada en el CWD
    abs_path = os.path.abspath(json_path)
    logging.info(f"Ruta relativa recibida: {json_path}")
    logging.info(f"Directorio de trabajo actual (CWD): {os.getcwd()}")
    logging.info(f"Ruta absoluta calculada: {abs_path}")

    # --- INTENTAR ABRIR CON RUTA ABSOLUTA ---
    logging.info(f"Intentando ABRIR usando RUTA ABSOLUTA: {abs_path}")
    try:
        # --- Usar abs_path aquí ---
        with open(abs_path, "r", encoding="utf-8") as f:
            logging.info(f"¡Éxito! Abierto usando ruta absoluta: {abs_path}")
            return json.load(f)
    except FileNotFoundError:
        # Si falla incluso con la absoluta, el problema es más grave
        logging.error(
            f"FileNotFoundError incluso usando RUTA ABSOLUTA: {abs_path}", exc_info=True
        )
        sys.exit(1)
        # ------ (Podrías añadir aquí el reintento con la relativa si quieres comparar,
        #          pero si falla la absoluta, la relativa seguramente también) ------
    except Exception as e:
        # Otro error al intentar con la ruta absoluta
        logging.error(
            f"Error cargando JSON desde RUTA ABSOLUTA '{abs_path}': {e}", exc_info=True
        )
        sys.exit(1)


def main():
    """Función principal del validador de datalayers"""

    # Configurar el parser de argumentos
    parser = argparse.ArgumentParser(description="Validador automático de DataLayers")
    parser.add_argument("--url", required=True, help="URL del sitio a validar")
    parser.add_argument(
        "--json", required=True, help="Ruta al archivo JSON de referencia de DataLayers"
    )
    parser.add_argument(
        "--config",
        default="config/default_config.json",
        help="Ruta al archivo de configuración (opcional)",
    )
    parser.add_argument(
        "--output", help="Directorio para guardar el reporte (opcional)"
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Ejecutar en modo headless (sin interfaz gráfica)",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Ejecutar en modo interactivo (navegación manual)",
    )

    args = parser.parse_args()

    # Cargar configuración
    config = load_config(args.config)

    # Configurar logging
    log_dir = config.get("paths", {}).get("logs_dir", "logs")
    log_path = configure_logging(log_dir)

    # Establecer directorio de salida
    output_dir = (
        args.output
        if args.output
        else config.get("paths", {}).get("output_dir", "docs/output")
    )
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    try:
        # Registrar inicio de la validación
        logging.info(f"Iniciando validación de DataLayers para URL: {args.url}")
        logging.info(f"Archivo JSON de referencia: {args.json}")

        # 1. Cargar los DataLayers de referencia y construir el esquema
        logging.info("Paso 1: Cargando DataLayers de referencia...")
        reference_datalayers = load_datalayers_json(args.json)

        schema_builder = SchemaBuilder(reference_datalayers)
        validation_schema = schema_builder.build_schema()

        # Guardar el esquema para referencia
        schema_path = os.path.join(output_dir, "validation_schema.json")
        with open(schema_path, "w", encoding="utf-8") as f:
            json.dump(validation_schema, f, indent=2, ensure_ascii=False)
        logging.info(f"Esquema de validación guardado en: {schema_path}")

        # 2. Validar los datalayers en el sitio
        logging.info("Paso 2: Validando DataLayers en el sitio web...")
        validator = DataLayerValidator(
            url=args.url,
            schema=validation_schema,
            headless=not args.interactive,  # Usar modo visible si es interactivo
            config=config,
        )

        # Usar modo interactivo o automático según la opción
        if args.interactive:
            logging.info("Iniciando validación en modo interactivo...")
            validation_results = validator.interactive_validation()
        else:
            validation_results = validator.validate_all_sections()

        # Guardar resultados crudos para referencia
        results_path = os.path.join(output_dir, "validation_results.json")
        with open(results_path, "w", encoding="utf-8") as f:
            json.dump(validation_results, f, indent=2, ensure_ascii=False)
        logging.info(f"Resultados de validación guardados en: {results_path}")

        # 3. Generar reporte
        logging.info("Paso 3: Generando reporte...")

        # Preparar configuración para el generador de reportes
        report_config = config.get("reporter", {})
        report_config["output_dir"] = output_dir

        # Inicializar el generador de reportes y generar reportes
        report_generator = ReportGenerator(report_config)
        report_paths = report_generator.generate_report(
            validation_results=validation_results,
            url=args.url,
            schema=validation_schema,
        )

        # Obtener la ruta del reporte HTML para mostrar en resumen
        report_path = report_paths.get("html", "")

        logging.info(f"Validación completada. Reporte HTML guardado en: {report_path}")
        logging.info(f"Archivo de log: {log_path}")

        # Mostrar estadísticas básicas
        stats = validation_results["summary"]

        # Calcular porcentajes
        total = (
            stats["total_sections"] if stats["total_sections"] > 0 else 1
        )  # Evitar división por cero
        passed_percent = round((stats["unique_valid_matches"] / total) * 100, 1)
        failed_percent = round((stats["unique_invalid_matches"] / total) * 100, 1)
        not_found_percent = round((stats["not_found_sections"] / total) * 100, 1)

        print("\n=== Resumen de Validación ===")
        print(f"Total de datalayers a validar: {stats['total_sections']}")
        print(
            f"Secciones con DataLayers correctos: {stats['unique_valid_matches']} ({passed_percent}%)"
        )
        print(
            f"Secciones con problemas: {stats['unique_invalid_matches']} ({failed_percent}%)"
        )
        print(
            f"Secciones no encontradas: {stats['not_found_sections']} ({not_found_percent}%)"
        )
        print(f"\nReporte detallado: {report_path}")

    except Exception as e:
        logging.error(f"Error en la ejecución del validador: {e}", exc_info=True)
        print(f"\nError: {str(e)}")
        print(f"Consulte el log para más detalles: {log_path}")
        sys.exit(1)


if __name__ == "__main__":
    main()
