# src/reporter/report_generator.py

import json
import os
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional
import csv
import jinja2  # Usar import directo
import re

logger = logging.getLogger(__name__)


# --- INICIO: Definición del filtro fuera de la clase (o como método estático) ---
def format_datetime_filter(value, format="%Y-%m-%d %H:%M:%S"):
    """Filtro Jinja2 para formatear fechas/horas."""
    try:
        if isinstance(value, str):
            # Intentar parsear varios formatos ISO comunes
            try:
                dt_obj = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                # Intentar sin microsegundos si falla
                dt_obj = datetime.fromisoformat(
                    value.split(".")[0].replace("Z", "+00:00")
                )
            return dt_obj.strftime(format)
        elif isinstance(value, datetime):
            return value.strftime(format)
    except Exception as e:
        logger.warning(
            f"Error formateando fecha '{value}' con filtro format_datetime: {e}"
        )
        return value  # Devolver original si falla
    return value


def tojson_filter(value, indent=None, ensure_ascii=False):
    """Filtro Jinja2 para convertir a JSON sin escapar unicode."""
    try:
        return json.dumps(value, indent=indent, ensure_ascii=ensure_ascii)
    except Exception as e:
        logger.warning(f"Error en filtro tojson: {e}")
        return "{}"  # Devolver objeto vacío o string de error


# --- FIN: Definición del filtro ---


class ReportGenerator:
    """
    Clase para generar reportes de validación de DataLayers.
    """

    def __init__(self, config: Dict[str, Any]):
        """
        Inicializa el generador de reportes.
        """
        self.config = config
        self.output_dir = config.get("paths", {}).get("output", "docs/output")
        self.ensure_output_dir()

        template_dir = config.get("paths", {}).get(
            "templates", os.path.join(os.path.dirname(__file__), "templates")
        )
        logger.info(f"Directorio de plantillas configurado en: {template_dir}")

        try:
            # Configurar Jinja2
            self.jinja_env = jinja2.Environment(
                loader=jinja2.FileSystemLoader(template_dir),
                autoescape=jinja2.select_autoescape(["html", "xml"]),
            )
            # --- INICIO: Registrar filtros personalizados en __init__ ---
            self.jinja_env.filters["format_datetime"] = format_datetime_filter
            self.jinja_env.filters["tojson"] = tojson_filter
            # --- FIN: Registrar filtros ---
            logger.info("Entorno Jinja2 configurado correctamente con filtros.")
        except Exception as e:
            logger.error(
                f"Error al configurar Jinja2. Verifica la ruta del directorio de plantillas: {template_dir}",
                exc_info=True,
            )
            raise e

    def ensure_output_dir(self) -> None:
        """
        Asegura que el directorio de salida exista.
        """
        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir)
            logger.info(f"Directorio de salida creado: {self.output_dir}")

    def _sanitize_filename(self, url: str) -> str:
        """Limpia una URL para usarla como parte de un nombre de archivo."""
        name = re.sub(r"^https?://", "", url)
        name = re.sub(r"[^a-zA-Z0-9._-]", "-", name)
        name = re.sub(r"-+", "-", name).strip("-")
        return name[:100]

    def generate_filename(self, url: str, extension: str) -> str:
        """
        Genera un nombre de archivo único para el reporte.
        """
        sanitized_url = self._sanitize_filename(url)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        return f"validation_{sanitized_url}_{timestamp}.{extension}"

    def generate_json_report(
        self,
        validation_results: Dict[str, Any],
        url: str,
        schema: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Genera un reporte en formato JSON.
        """
        filename = self.generate_filename(url, "json")
        filepath = os.path.join(self.output_dir, filename)
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(validation_results, f, indent=2, ensure_ascii=False)
            logger.info(f"Reporte JSON generado: {filepath}")
        except IOError as e:
            logger.error(f"Error al guardar el reporte JSON en {filepath}: {e}")
            filepath += " (ERROR)"
        except TypeError as e:
            logger.error(f"Error de tipo al serializar a JSON: {e}")
            filepath += " (ERROR)"
        return filepath

    def generate_csv_report(self, validation_results: Dict[str, Any], url: str) -> str:
        """
        Genera un reporte en formato CSV con errores.
        """
        filename = self.generate_filename(url, "csv")
        filepath = os.path.join(self.output_dir, filename)

        errors_found = []
        try:
            for i, detail in enumerate(validation_results.get("details", [])):
                if detail.get("errors"):
                    for error in detail.get("errors", []):
                        errors_found.append(
                            {
                                "datalayer_index": i + 1,
                                "error_message": error,
                                "matched_section": detail.get("matched_section", "N/A"),
                                # Añadir más campos del 'detail' si es necesario
                            }
                        )

            if not errors_found:
                logger.info(
                    "No se encontraron errores específicos para generar reporte CSV."
                )
                # Crear archivo vacío con cabeceras para consistencia
                with open(filepath, "w", newline="", encoding="utf-8") as f:
                    fieldnames = ["datalayer_index", "matched_section", "error_message"]
                    writer = csv.DictWriter(f, fieldnames=fieldnames)
                    writer.writeheader()
                return filepath + " (Vacío, sin errores)"

            with open(filepath, "w", newline="", encoding="utf-8") as f:
                fieldnames = ["datalayer_index", "matched_section", "error_message"]
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(errors_found)

            logger.info(f"Reporte CSV generado: {filepath}")

        except IOError as e:
            logger.error(f"Error al escribir el archivo CSV en {filepath}: {e}")
            filepath += " (ERROR)"
        except Exception as e:
            logger.error(f"Error inesperado al generar reporte CSV: {e}", exc_info=True)
            filepath += " (ERROR)"
        return filepath

    def generate_html_report(
        self,
        validation_results: Dict[str, Any],
        url: str,
        schema: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Genera un reporte en formato HTML usando los recuentos únicos del resumen.
        """
        filename = self.generate_filename(url, "html")
        filepath = os.path.join(self.output_dir, filename)

        try:
            template = self.jinja_env.get_template("report_template.html")

            all_details = validation_results.get("details", [])
            summary_data = validation_results.get("summary", {})

            # --- Usar los contadores únicos del summary ---
            unique_valid_count = summary_data.get("unique_valid_matches", 0)
            unique_invalid_count = summary_data.get("unique_invalid_matches", 0)
            unique_warning_count = summary_data.get(
                "unique_datalayers_with_warnings", 0
            )
            # No necesitamos contar los 'unmatched' para el % de éxito de esta forma
            total_unique_matches = unique_valid_count + unique_invalid_count

            # Calcular % de éxito basado en matches únicos válidos vs total de matches únicos
            success_percent = (
                (unique_valid_count / total_unique_matches * 100)
                if total_unique_matches > 0
                else 0
            )
            logger.info(
                f"Calculando success_percent: {unique_valid_count} / {total_unique_matches} = {success_percent}"
            )

            # Filtrar detalles con warnings (para la lista detallada de warnings)
            details_with_warnings = [
                detail
                for detail in all_details
                if detail.get("warnings") and len(detail["warnings"]) > 0
            ]
            # El conteo total de items únicos con warnings ya está en unique_warning_count

            comparison_data = validation_results.get("comparison", {})
            # Asegurar valores por defecto para comparison
            comparison_data.setdefault("reference_count", 0)
            comparison_data.setdefault(
                "captured_count", 0
            )  # Total capturados relevantes
            comparison_data.setdefault(
                "matched_count", 0
            )  # Referencias únicas encontradas
            comparison_data.setdefault(
                "missing_count", 0
            )  # Referencias únicas no encontradas

            comparison_data.setdefault("coverage_percent", 0.0)
            comparison_data.setdefault("missing_details", [])

            report_timestamp = validation_results.get(
                "timestamp", datetime.now().isoformat()
            )
            report_url = validation_results.get("url", url)

            # --- Construir el contexto con los valores únicos para el resumen ---
            context = {
                "timestamp": report_timestamp,
                "url": report_url,
                "is_valid": validation_results.get("valid", False),
                "details": all_details,  # Pasar todos los detalles para la sección detallada
                "comparison": comparison_data,
                "summary": summary_data,  # Pasar el summary completo
                # --- Usar los contadores únicos para la sección de resumen general ---
                "valid_count": unique_valid_count,
                "invalid_count": unique_invalid_count,
                "warning_count": unique_warning_count,
                "unmatched_count": summary_data.get(
                    "unique_unmatched_datalayers", 0
                ),  # Para posible uso en plantilla
                "total_unique_relevant": summary_data.get(
                    "total_unique_captured_relevant", 0
                ),  # Para posible uso en plantilla
                # --- Fin contadores únicos ---
                "success_percent": round(success_percent, 1),
                "details_with_warnings": details_with_warnings,  # Para la lista desplegable de warnings
                "general_warnings": validation_results.get("warnings", []),
            }

            html_content = template.render(**context)

            with open(filepath, "w", encoding="utf-8") as f:
                f.write(html_content)
            logger.info(f"Reporte HTML generado: {filepath}")

        except jinja2.exceptions.TemplateNotFound:
            # Usar self.jinja_env.loader.searchpath para obtener la ruta buscada
            searchpath = getattr(self.jinja_env.loader, "searchpath", ["Desconocido"])
            logger.error(
                f"Error Crítico: No se encontró la plantilla 'report_template.html' en '{searchpath[0]}'. Verifica la ruta."
            )
            filepath += " (ERROR: Plantilla no encontrada)"
        except Exception as e:
            logger.error(
                f"Error al generar el reporte HTML en {filepath}: {e}", exc_info=True
            )
            filepath += f" (ERROR: {type(e).__name__})"
            try:
                with open(
                    filepath.replace(".html", ".error.html"), "w", encoding="utf-8"
                ) as f:
                    f.write(
                        f"<html><head><title>Error Reporte</title></head><body><h1>Error al generar reporte</h1><p>URL: {url}</p><p>Error: {str(e)}</p><pre>{json.dumps(validation_results, indent=2, ensure_ascii=False)}</pre></body></html>"
                    )
            except Exception as write_err:
                logger.error(
                    f"No se pudo escribir el archivo HTML de error: {write_err}"
                )

        return filepath

    def generate_summary(self, reports: List[str]) -> None:
        """
        Genera un resumen de todos los reportes generados.
        """
        summary_file = os.path.join(self.output_dir, "summary.txt")
        try:
            with open(summary_file, "w", encoding="utf-8") as f:
                f.write(
                    f"Resumen de validación - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                )
                f.write(
                    f"Total de reportes generados en esta ejecución: {len(reports)}\n\n"
                )
                for i, report in enumerate(reports):
                    # Mostrar solo el nombre base del archivo
                    f.write(f"{i+1}. {os.path.basename(report)}\n")
            logger.info(f"Resumen de archivos generado: {summary_file}")
        except IOError as e:
            logger.error(f"Error al escribir el archivo de resumen summary.txt: {e}")
        except Exception as e:
            logger.error(f"Error inesperado al generar el resumen: {e}", exc_info=True)

    def generate_report(
        self,
        validation_results: Dict[str, Any],
        url: str,
        schema: Optional[Dict[str, Any]] = None,
        formats: Optional[List[str]] = None,
    ) -> Dict[str, str]:
        """
        Genera reportes en los formatos especificados. (Método original restaurado)
        """
        if formats is None:
            formats = self.config.get("report_formats", ["json", "html"])

        reports = {}
        generated_files_list = []

        if "json" in formats:
            try:
                report_path = self.generate_json_report(validation_results, url, schema)
                if "(ERROR)" not in report_path:  # Solo añadir si no hubo error
                    reports["json"] = report_path
                    generated_files_list.append(report_path)
            except Exception as e:
                logger.error(f"Fallo al generar reporte JSON: {e}", exc_info=True)
                reports["json"] = "ERROR"

        if "csv" in formats:
            try:
                report_path = self.generate_csv_report(validation_results, url)
                if (
                    "(ERROR)" not in report_path and "(Vacío" not in report_path
                ):  # No añadir si vacío o error
                    reports["csv"] = report_path
                    generated_files_list.append(report_path)
                elif "(Vacío" in report_path:
                    reports["csv"] = report_path  # Mantener info de vacío
            except Exception as e:
                logger.error(f"Fallo al generar reporte CSV: {e}", exc_info=True)
                reports["csv"] = "ERROR"

        if "html" in formats:
            try:
                report_path = self.generate_html_report(validation_results, url, schema)
                if "(ERROR)" not in report_path:  # Solo añadir si no hubo error
                    reports["html"] = report_path
                    generated_files_list.append(report_path)
            except Exception as e:
                logger.error(f"Fallo al generar reporte HTML: {e}", exc_info=True)
                reports["html"] = "ERROR"

        # Generar resumen con la lista de archivos generados exitosamente
        try:
            self.generate_summary(generated_files_list)
        except Exception as e:
            logger.error(f"Fallo al generar el archivo summary.txt: {e}", exc_info=True)

        return reports
