import json
import os
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional
import csv
import jinja2

logger = logging.getLogger(__name__)


class ReportGenerator:
    """
    Clase para generar reportes de validación de DataLayers.
    """

    def __init__(self, config: Dict[str, Any]):
        """
        Inicializa el generador de reportes.

        Args:
            config: Configuración del generador de reportes
        """
        self.config = config
        self.output_dir = config.get("output_dir", "docs/output")
        self.ensure_output_dir()

        # Configurar Jinja2 para plantillas HTML
        template_dir = config.get("template_dir", "src/reporter/templates")
        self.jinja_env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(template_dir),
            autoescape=jinja2.select_autoescape(["html", "xml"]),
        )

        # Configurar el filtro tojson para que no escape caracteres Unicode
        def tojson_filter(value, indent=None, ensure_ascii=False):
            return json.dumps(value, indent=indent, ensure_ascii=ensure_ascii)

        self.jinja_env.filters['tojson'] = tojson_filter

    def ensure_output_dir(self) -> None:
        """
        Asegura que el directorio de salida exista.
        """
        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir)
            logger.info(f"Directorio de salida creado: {self.output_dir}")

    def generate_filename(self, url: str, extension: str) -> str:
        """
        Genera un nombre de archivo único para el reporte.

        Args:
            url: URL del sitio validado
            extension: Extensión del archivo de reporte

        Returns:
            Nombre de archivo para el reporte
        """
        # Limpiar URL para usar como parte del nombre de archivo
        clean_url = url.replace("http://", "").replace("https://", "")
        clean_url = clean_url.replace("/", "_").replace(".", "-")

        # Generar timestamp
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")

        return f"validation_{clean_url}_{timestamp}.{extension}"

    def generate_json_report(
        self, validation_results: Dict[str, Any], url: str, schema: Dict[str, Any]
    ) -> str:
        """
        Genera un reporte en formato JSON.

        Args:
            validation_results: Resultados de la validación
            url: URL del sitio validado
            schema: Esquema utilizado para la validación

        Returns:
            Ruta del archivo de reporte generado
        """
        filename = self.generate_filename(url, "json")
        filepath = os.path.join(self.output_dir, filename)

        report_data = {
            "timestamp": datetime.now().isoformat(),
            "url": url,
            "schema": schema,
            "validation_results": validation_results,
        }

        with open(filepath, "w", encoding="utf-8") as f:
            # Usamos ensure_ascii=False para asegurar que los caracteres Unicode
            # se escriban como están, no como secuencias de escape \uXXXX
            json.dump(report_data, f, indent=2, ensure_ascii=False)

        logger.info(f"Reporte JSON generado: {filepath}")
        return filepath

    def generate_csv_report(self, validation_results: Dict[str, Any], url: str) -> str:
        """
        Genera un reporte en formato CSV.

        Args:
            validation_results: Resultados de la validación
            url: URL del sitio validado

        Returns:
            Ruta del archivo de reporte generado
        """
        filename = self.generate_filename(url, "csv")
        filepath = os.path.join(self.output_dir, filename)

        # Extraer errores para el CSV
        errors = []
        for detail in validation_results.get("details", []):
            datalayer_index = detail.get("datalayer_index", 0)
            for error in detail.get("errors", []):
                errors.append({"datalayer_index": datalayer_index, "error": error})

        # Crear el archivo CSV
        with open(filepath, "w", newline="", encoding="utf-8") as f:
            fieldnames = ["datalayer_index", "error"]
            writer = csv.DictWriter(f, fieldnames=fieldnames)

            writer.writeheader()
            for error in errors:
                writer.writerow(error)

        logger.info(f"Reporte CSV generado: {filepath}")
        return filepath

    def generate_html_report(
        self, validation_results: Dict[str, Any], url: str, schema: Dict[str, Any]
    ) -> str:
        """
        Genera un reporte en formato HTML.

        Args:
            validation_results: Resultados de la validación
            url: URL del sitio validado
            schema: Esquema utilizado para la validación

        Returns:
            Ruta del archivo de reporte generado
        """
        filename = self.generate_filename(url, "html")
        filepath = os.path.join(self.output_dir, filename)

        try:
            # Cargar la plantilla
            template = self.jinja_env.get_template("report_template.html")

            # Calcular estadísticas adicionales para el reporte
            details = validation_results.get("details", [])
            valid_count = sum(1 for detail in details if detail.get("valid", False))
            invalid_count = len(details) - valid_count

            success_percent = 0
            if details:
                success_percent = round((valid_count / len(details)) * 100, 1)

            # Obtener datos de comparación si existen
            comparison = validation_results.get("comparison", {})
            if not comparison:
                # Valores por defecto si no hay comparación
                comparison = {
                    "reference_count": 0,
                    "captured_count": 0,
                    "matched_count": 0,
                    "missing_count": 0,
                    "extra_count": 0,
                    "coverage_percent": 0,
                    "match_details": [],
                    "missing_details": [],
                    "extra_details": []
                }

            # Preparar los datos para la plantilla
            template_data = {
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "url": url,
                "is_valid": validation_results.get("valid", False),
                "errors_count": len(validation_results.get("errors", [])),
                "warnings_count": len(validation_results.get("warnings", [])),
                "details": details,
                "schema": schema,
                # Estadísticas adicionales
                "valid_count": valid_count,
                "invalid_count": invalid_count,
                "success_percent": success_percent,
                # Datos de comparación
                "comparison": comparison
            }

            # Renderizar la plantilla
            html_content = template.render(**template_data)

            # Guardar el archivo HTML
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(html_content)

            logger.info(f"Reporte HTML generado: {filepath}")
            return filepath

        except Exception as e:
            logger.error(f"Error al generar reporte HTML: {str(e)}", exc_info=True)
            # Crear un archivo HTML básico en caso de error
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(
                    f"""
                <!DOCTYPE html>
                <html>
                <head>
                    <title>Reporte de validación - Error</title>
                    <meta charset="UTF-8">
                </head>
                <body>
                    <h1>Error al generar reporte detallado</h1>
                    <p>URL: {url}</p>
                    <p>Error: {str(e)}</p>
                    <pre>{json.dumps(validation_results, indent=2, ensure_ascii=False)}</pre>
                </body>
                </html>
                """
                )
            return filepath

    def generate_summary(self, reports: List[str]) -> None:
        """
        Genera un resumen de todos los reportes generados.

        Args:
            reports: Lista de rutas a los reportes generados
        """
        summary_file = os.path.join(self.output_dir, "summary.txt")

        with open(summary_file, "w", encoding="utf-8") as f:
            f.write(
                f"Resumen de validación - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            )
            f.write(f"Total de reportes generados: {len(reports)}\n\n")

            for i, report in enumerate(reports):
                f.write(f"{i+1}. {os.path.basename(report)}\n")

        logger.info(f"Resumen generado: {summary_file}")

    def generate_report(
        self,
        validation_results: Dict[str, Any],
        url: str,
        schema: Dict[str, Any],
        formats: Optional[List[str]] = None,
    ) -> Dict[str, str]:
        """
        Genera reportes en los formatos especificados.

        Args:
            validation_results: Resultados de la validación
            url: URL del sitio validado
            schema: Esquema utilizado para la validación
            formats: Formatos de reporte a generar (json, csv, html)

        Returns:
            Diccionario con las rutas de los reportes generados
        """
        if formats is None:
            formats = self.config.get("report_formats", ["json", "html"])

        reports = {}

        if "json" in formats:
            reports["json"] = self.generate_json_report(validation_results, url, schema)

        if "csv" in formats:
            reports["csv"] = self.generate_csv_report(validation_results, url)

        if "html" in formats:
            reports["html"] = self.generate_html_report(validation_results, url, schema)

        # Generar resumen
        self.generate_summary(list(reports.values()))

        return reports
