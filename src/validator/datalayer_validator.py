import json
import logging
import re
import os
import time
from typing import Dict, List, Any, Tuple, Optional

from playwright.sync_api import (
    sync_playwright,
    Page,
    Browser,
    TimeoutError as PlaywrightTimeoutError,
)

logger = logging.getLogger(__name__)


class DataLayerValidator:
    """
    Clase para validar DataLayers extraídos de un sitio web contra un esquema definido.
    """

    def __init__(
        self,
        url: str,
        schema: Dict[str, Any],
        headless: bool = True,
        config: Dict[str, Any] = None,
    ):
        """
        Inicializa el validador con una URL, un esquema y configuración.

        Args:
            url: URL del sitio a validar
            schema: Esquema contra el cual validar los DataLayers
            headless: Si se debe ejecutar en modo headless (sin interfaz gráfica)
            config: Configuración del validador
        """
        self.url = url
        self.schema = schema
        self.headless = headless
        self.config = config or {}
        self.driver = None
        self.validation_results = {
            "valid": True,
            "errors": [],
            "warnings": [],
            "details": [],
            "sections": [],
            "summary": {
                "total_sections": 0,
                "valid_sections": 0,
                "invalid_sections": 0,
                "not_found_sections": 0,
            },
        }

    def setup_driver(self) -> None:
        """
        Configura el navegador de Playwright según los parámetros de configuración.
        """
        browser_config = self.config.get("browser", {})

        # Iniciar Playwright y lanzar navegador
        self.playwright = sync_playwright().start()

        # Configurar opciones del navegador
        browser_args = []
        if self.headless:
            browser_args.append("--headless")

        # Añadir argumentos adicionales para estabilidad
        browser_args.extend(
            ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
        )

        # Configurar el user agent si está especificado
        user_agent = browser_config.get("user_agent")

        # Lanzar el navegador
        self.browser = self.playwright.chromium.launch(
            headless=self.headless, args=browser_args
        )

        # Crear contexto (equivalente a una sesión de navegador)
        window_size = browser_config.get("window_size", {"width": 1920, "height": 1080})
        self.context = self.browser.new_context(
            viewport={"width": window_size["width"], "height": window_size["height"]},
            user_agent=user_agent,
        )

        # Crear página
        self.page = self.context.new_page()

        # Configurar timeouts
        self.page.set_default_timeout(
            browser_config.get("page_load_timeout", 30) * 1000
        )

        logger.info("Playwright configurado correctamente")

    def _calculate_match_score(
        self,
        datalayer: Dict[str, Any],
        expected_properties: Dict[str, Any],
        required_fields: List[str],
    ) -> Tuple[float, List[str]]:

        errors = []
        total_expected_props = len(expected_properties)
        if total_expected_props == 0:
            return 0.0, ["No hay propiedades esperadas definidas en el esquema"]

        key_fields = ["event", "event_category", "event_action", "event_label"]
        key_field_weight = 0.7  # 70% del score viene de los campos clave
        other_field_weight = 1.0 - key_field_weight  # 30% del score viene del resto

        matched_key_fields = 0
        total_key_fields_in_expected = 0
        key_field_errors = []

        matched_other_fields = 0
        total_other_fields_in_expected = 0
        other_field_errors = []

        # Iterar sobre las propiedades esperadas para clasificarlas y compararlas
        for prop, expected_value in expected_properties.items():
            is_key_field = prop in key_fields
            actual_value = datalayer.get(prop)  # Obtener valor actual o None

            is_dynamic = expected_value is None or (
                isinstance(expected_value, str)
                and "{{" in expected_value
                and "}}" in expected_value
            )

            if is_key_field:
                total_key_fields_in_expected += 1
                if prop not in datalayer:
                    if prop in required_fields:
                        key_field_errors.append(
                            f"Campo clave requerido '{prop}' no encontrado"
                        )
                elif is_dynamic:
                    matched_key_fields += 1
                else:
                    if isinstance(expected_value, str) and isinstance(
                        actual_value, str
                    ):
                        norm_expected = self._normalize_string(expected_value)
                        norm_actual = self._normalize_string(actual_value)
                        if norm_expected == norm_actual:
                            matched_key_fields += 1
                        else:
                            clean_expected = self._clean_string(expected_value)
                            clean_actual = self._clean_string(actual_value)
                            if clean_expected == clean_actual:
                                matched_key_fields += 1
                            else:
                                key_field_errors.append(
                                    f"Valor para campo clave '{prop}' no coincide: esperado '{expected_value}', encontrado '{actual_value}'"
                                )
                    elif actual_value == expected_value:
                        matched_key_fields += 1
                    else:
                        key_field_errors.append(
                            f"Valor para campo clave '{prop}' no coincide: esperado '{expected_value}', encontrado '{actual_value}'"
                        )
            else:  # Campo no clave
                total_other_fields_in_expected += 1
                if prop not in datalayer:
                    if prop in required_fields:
                        other_field_errors.append(
                            f"Campo requerido '{prop}' no encontrado"
                        )
                elif is_dynamic:
                    matched_other_fields += 1
                else:
                    if isinstance(expected_value, str) and isinstance(
                        actual_value, str
                    ):
                        norm_expected = self._normalize_string(expected_value)
                        norm_actual = self._normalize_string(actual_value)
                        if norm_expected == norm_actual:
                            matched_other_fields += 1
                        else:
                            other_field_errors.append(
                                f"Valor para '{prop}' no coincide: esperado '{expected_value}', encontrado '{actual_value}'"
                            )
                    elif actual_value == expected_value:
                        matched_other_fields += 1
                    else:
                        other_field_errors.append(
                            f"Valor para '{prop}' no coincide: esperado '{expected_value}', encontrado '{actual_value}'"
                        )

        # Calcular puntuaciones parciales
        key_score = (
            (matched_key_fields / total_key_fields_in_expected)
            if total_key_fields_in_expected > 0
            else 1.0
        )
        other_score = (
            (matched_other_fields / total_other_fields_in_expected)
            if total_other_fields_in_expected > 0
            else 1.0
        )

        # Penalización fuerte si 'event' (si se espera y no es dinámico) no coincide
        event_prop = "event"
        if event_prop in expected_properties and not (
            expected_properties[event_prop] is None
            or (
                isinstance(expected_properties[event_prop], str)
                and "{{" in expected_properties[event_prop]
            )
        ):
            # Verificar no existencia o diferencia de valor normalizado
            if (
                event_prop not in datalayer
                or (
                    isinstance(datalayer.get(event_prop), str)
                    and isinstance(expected_properties[event_prop], str)
                    and self._normalize_string(datalayer.get(event_prop, ""))
                    != self._normalize_string(expected_properties[event_prop])
                )
                or (
                    not isinstance(datalayer.get(event_prop), str)
                    and datalayer.get(event_prop) != expected_properties[event_prop]
                )
            ):

                logger.debug(
                    f"Penalizando score por no coincidencia en 'event' para {datalayer.get(event_prop)} vs {expected_properties[event_prop]}"
                )
                key_score *= 0.1  # Aplica penalización

        # Combinar puntuaciones
        final_score = (key_score * key_field_weight) + (
            other_score * other_field_weight
        )

        # Combinar errores
        errors.extend(key_field_errors)
        errors.extend(other_field_errors)

        final_score = min(final_score, 1.0)  # Asegurar que no exceda 1.0

        # Si hay errores graves en campos clave, podríamos reducir aún más o poner a 0
        if (
            key_field_errors and key_score < 0.5
        ):  # Ejemplo: si la mitad de los clave fallan
            final_score *= 0.5  # Reducir más el score final

        final_score = max(0.0, final_score)  # Asegurar que no sea negativo

        return final_score, errors

    def _sort_reference_properties(
        self, captured_datalayer: Dict[str, Any], reference_properties: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Ordena las propiedades del DataLayer de referencia en el mismo orden que
        las propiedades del DataLayer capturado para facilitar la comparación.

        Args:
           captured_datalayer: DataLayer capturado
           reference_properties: Propiedades del DataLayer de referencia

        Returns:
           Diccionario de propiedades de referencia ordenadas
        """
        # Si alguno de los argumentos no es un diccionario, devolver el de referencia sin cambios
        if not isinstance(captured_datalayer, dict) or not isinstance(
            reference_properties, dict
        ):
            return reference_properties

        # Crear un nuevo diccionario para las propiedades ordenadas
        sorted_properties = {}

        # Primero, incluir todas las propiedades que están en el DataLayer capturado
        # en el mismo orden
        for key in captured_datalayer.keys():
            if key in reference_properties:
                sorted_properties[key] = reference_properties[key]

        # Luego, incluir cualquier propiedad restante del DataLayer de referencia que no
        # esté en el capturado
        for key, value in reference_properties.items():
            if key not in sorted_properties:
                sorted_properties[key] = value

        return sorted_properties

    def _compare_with_reference(
        self, captured_datalayers: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Compara los DataLayers capturados con los DataLayers de referencia en el esquema.

        Args:
           captured_datalayers: Lista de DataLayers capturados

        Returns:
           Diccionario con información de comparación
        """
        comparison_results = {
            "reference_count": 0,  # Cantidad de DataLayers en el archivo de referencia
            "captured_count": len(
                captured_datalayers
            ),  # Cantidad de DataLayers capturados
            "matched_count": 0,  # DataLayers que coinciden entre referencia y capturados
            "missing_count": 0,  # DataLayers de referencia que no se encontraron
            "extra_count": 0,  # DataLayers capturados que no estaban en la referencia
            "match_details": [],  # Detalles de las coincidencias encontradas
            "missing_details": [],  # Detalles de DataLayers faltantes
            "extra_details": [],  # Detalles de DataLayers extra
        }

        # Obtener lista de DataLayers de referencia (propiedades de cada sección)
        reference_datalayers = []
        for section in self.schema.get("sections", []):
            datalayer_props = section.get("datalayer", {}).get("properties", {})
            if datalayer_props:
                reference_datalayers.append(
                    {
                        "properties": datalayer_props,
                        "title": section.get("title", "Unknown Section"),
                        "id": section.get("id", "unknown_id"),
                        "match_found": False,  # Flag para saber si se encontró coincidencia
                    }
                )

        comparison_results["reference_count"] = len(reference_datalayers)

        # Para cada DataLayer capturado, buscar coincidencia en los de referencia
        for i, datalayer in enumerate(captured_datalayers):
            best_match = None
            best_match_score = 0
            best_match_idx = -1

            # Buscar el mejor match en la referencia
            for j, ref_dl in enumerate(reference_datalayers):
                expected_properties = ref_dl["properties"]
                required_fields = []

                # Identificar campos requeridos (esto podría mejorarse)
                for key in expected_properties:
                    if key in [
                        "event",
                        "event_category",
                        "event_action",
                        "event_label",
                    ]:
                        required_fields.append(key)

                # Calcular puntuación de coincidencia
                score, _ = self._calculate_match_score(
                    datalayer, expected_properties, required_fields
                )

                # Si tenemos un mejor match, actualizar
                if score > best_match_score:
                    best_match_score = score
                    best_match = ref_dl
                    best_match_idx = j

            # Determinar si es una coincidencia válida
            match_threshold = self.config.get("validation", {}).get(
                "match_threshold", 0.7
            )

            # Si encontramos una coincidencia válida
            if best_match and best_match_score >= match_threshold:
                comparison_results["matched_count"] += 1

                # Marcar que se encontró coincidencia para este DataLayer de referencia
                reference_datalayers[best_match_idx]["match_found"] = True

                # Ordenar las propiedades de referencia para que se muestren en el mismo orden que el DataLayer capturado
                sorted_reference_properties = self._sort_reference_properties(
                    datalayer, best_match["properties"]
                )

                # Guardar detalles de la coincidencia
                comparison_results["match_details"].append(
                    {
                        "datalayer_index": i,
                        "reference_title": best_match["title"],
                        "reference_id": best_match["id"],
                        "match_score": best_match_score,
                        "data": datalayer,
                        "reference_data": sorted_reference_properties,  # Guardar propiedades de referencia ordenadas
                    }
                )

                # Actualizar el detalle correspondiente en validation_results con los datos de referencia
                for detail in self.validation_results["details"]:
                    if detail.get("datalayer_index") == i:
                        detail["reference_data"] = sorted_reference_properties
                        detail["matched_section"] = best_match["title"]
                        detail["match_score"] = best_match_score
                        break

            else:
                # Es un DataLayer extra (no está en la referencia)
                comparison_results["extra_count"] += 1
                comparison_results["extra_details"].append(
                    {"datalayer_index": i, "data": datalayer}
                )

        # Buscar DataLayers de referencia que no se encontraron
        for ref_dl in reference_datalayers:
            if not ref_dl["match_found"]:
                comparison_results["missing_count"] += 1
                comparison_results["missing_details"].append(
                    {
                        "reference_title": ref_dl["title"],
                        "reference_id": ref_dl["id"],
                        "properties": ref_dl["properties"],
                    }
                )

        # Calcular métricas adicionales
        if comparison_results["reference_count"] > 0:
            comparison_results["coverage_percent"] = round(
                (
                    comparison_results["matched_count"]
                    / comparison_results["reference_count"]
                )
                * 100,
                1,
            )
        else:
            comparison_results["coverage_percent"] = 0

        return comparison_results

    def _filter_datalayers(
        self, captured_datalayers: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Filtra los DataLayers capturados para eliminar los que no son relevantes para la validación.

        Args:
           captured_datalayers: Lista de DataLayers capturados

        Returns:
           Lista filtrada de DataLayers relevantes
        """
        # Lista de eventos que no son relevantes para la validación
        excluded_events = [
            "gtm.js",
            "gtm.dom",
            "gtm.load",
            "gtm.click",
            "gtm.scrollDepth",
            "gtm.historyChange",
        ]

        # Si no hay DataLayers, devolver la lista vacía
        if not captured_datalayers:
            logger.warning("No se capturó ningún DataLayer")
            return []

        # Imprimir información sobre los DataLayers capturados para depuración
        logger.info(f"Total de DataLayers capturados: {len(captured_datalayers)}")
        if captured_datalayers:
            logger.info(f"Primer DataLayer: {captured_datalayers[0]}")

        # Filtrar DataLayers que no son relevantes
        filtered_datalayers = []
        for dl in captured_datalayers:
            # Verificamos si es un diccionario
            if not isinstance(dl, dict):
                logger.warning(f"DataLayer no es un diccionario: {dl}")
                continue

            # Si el DataLayer no tiene 'event' o su evento no está en la lista de excluidos
            if "event" not in dl or dl["event"] not in excluded_events:
                # Incluimos DataLayers que tengan información relevante
                filtered_datalayers.append(dl)

        logger.info(
            f"DataLayers filtrados: {len(captured_datalayers)} capturados, {len(filtered_datalayers)} relevantes"
        )

        # Si después del filtrado no quedan DataLayers, devolver los originales
        if not filtered_datalayers and captured_datalayers:
            logger.warning(
                "El filtrado eliminó todos los DataLayers. Devolviendo los originales."
            )
            return captured_datalayers

        return filtered_datalayers

    def _validate_datalayer(
        self,
        datalayer: Dict[str, Any],
        expected_properties: Dict[str, Any],
        required_fields: List[str],
    ) -> List[str]:

        errors = []

        # Verificar campos requeridos
        for field in required_fields:
            if field not in datalayer:
                errors.append(f"Campo requerido '{field}' no encontrado")

        # Verificar tipos y valores
        for prop, expected_value in expected_properties.items():
            if prop in datalayer:
                actual_value = datalayer[prop]

                # Si es un valor dinámico (null o con {{...}}), solo verificar que no esté completamente vacío
                is_dynamic = expected_value is None or (
                    isinstance(expected_value, str)
                    and "{{" in expected_value
                    and "}}" in expected_value
                )

                if is_dynamic:
                    # Para campos dinámicos solo verificamos que no sea vacío si se espera un string
                    if (
                        expected_value is not None
                        and isinstance(expected_value, str)
                        and isinstance(actual_value, str)
                        and not actual_value.strip()
                    ):
                        errors.append(
                            f"El campo dinámico '{prop}' tiene un valor vacío"
                        )
                elif isinstance(expected_value, str) and isinstance(actual_value, str):
                    # Normalizar caracteres unicode para la comparación
                    norm_expected = self._normalize_string(expected_value)
                    norm_actual = self._normalize_string(actual_value)

                    if norm_expected != norm_actual:
                        # Intentar una comparación menos estricta para caracteres especiales
                        clean_expected = self._clean_string(expected_value)
                        clean_actual = self._clean_string(actual_value)

                        if clean_expected != clean_actual:
                            errors.append(
                                f"Valor para '{prop}' no coincide: esperado '{expected_value}', encontrado '{actual_value}'"
                            )
                elif actual_value != expected_value:
                    errors.append(
                        f"Valor para '{prop}' no coincide: esperado '{expected_value}', encontrado '{actual_value}'"
                    )
            elif prop in required_fields:
                errors.append(f"Propiedad requerida '{prop}' no encontrada")

        return errors

    def _normalize_string(self, text: str) -> str:
        """
        Normaliza un string para comparaciones consistentes.
        Maneja correctamente caracteres Unicode y secuencias de escape.

        Args:
            text: Texto a normalizar

        Returns:
            Texto normalizado
        """
        if not isinstance(text, str):
            return text

        # Decodificar secuencias de escape Unicode como \u00f3
        try:
            # Si ya contiene secuencias Unicode, decodificarlas
            if "\\u" in text:
                text = bytes(text, "utf-8").decode("unicode_escape")
        except:
            pass

        return text

    def _clean_string(self, text: str) -> str:
        """
        Limpia un string para comparaciones menos estrictas.
        Elimina espacios, puntuación y convierte a minúsculas.

        Args:
            text: Texto a limpiar

        Returns:
            Texto limpio para comparaciones
        """
        if not isinstance(text, str):
            return text

        # Normalizar primero
        cleaned = self._normalize_string(text)

        # Eliminar espacios en blanco, puntuación y convertir a minúsculas
        cleaned = cleaned.lower()
        cleaned = "".join(c for c in cleaned if c.isalnum() or c.isspace())
        cleaned = " ".join(cleaned.split())  # Normalizar espacios

        return cleaned

    def interactive_validation(self) -> Dict[str, Any]:
        try:
            original_headless = self.headless
            self.headless = False
            self.setup_driver()

            logger.info(f"Navegando a URL inicial: {self.url}")
            self.page.goto(self.url)
            try:
                # Espera a que la red esté inactiva o hasta 5 segundos
                self.page.wait_for_load_state("networkidle", timeout=5000)
            except PlaywrightTimeoutError:
                logger.warning(
                    "Timeout esperando networkidle, la página puede no estar completamente cargada."
                )
            except Exception as e:
                logger.warning(f"Error inesperado durante wait_for_load_state: {e}")

            original_url = self.page.url
            logger.info(f"URL base para la validación: {original_url}")

            monitor_script = """
            window.allDataLayers = [];
            if (typeof window.dataLayer !== 'undefined') {
                if (Array.isArray(window.dataLayer)) {
                    for (var i = 0; i < window.dataLayer.length; i++) {
                        try {
                            window.allDataLayers.push(Object.assign({}, window.dataLayer[i]));
                        } catch (e) {
                            console.error('Error al copiar dataLayer existente:', e);
                        }
                    }
                    console.log('Captured ' + window.dataLayer.length + ' existing dataLayer items');
                }
            } else {
                window.dataLayer = [];
                console.log('Created new dataLayer');
            }
            var originalPush = Array.prototype.push;
            window.dataLayer.push = function() {
                for (var i = 0; i < arguments.length; i++) {
                    var obj = arguments[i];
                    try {
                        var copy = Object.assign({}, obj);
                        window.allDataLayers.push(copy);
                        console.log('DataLayer captured:', copy);
                    } catch (e) {
                        console.error('Error capturing dataLayer:', e);
                    }
                }
                return originalPush.apply(this, arguments);
            };
            console.log('DataLayer monitoring initialized. Total captures:', window.allDataLayers.length);
            window.inspectDataLayers = function() {
                console.table(window.allDataLayers);
                return window.allDataLayers;
            };
            """
            self.page.evaluate(monitor_script)

            print("\n=== MODO INTERACTIVO DE VALIDACIÓN ===")
            print(f"Se ha abierto el navegador para validar: {original_url}")
            print("Instrucciones:")
            print("1. Navega por el sitio y realiza las acciones que desees probar.")
            print("2. Los DataLayers se capturarán automáticamente mientras navegas.")
            print(
                "3. Si deseas verificar los DataLayers capturados, ejecuta esto en la consola del navegador:"
            )
            print("   window.inspectDataLayers()")
            print(
                "4. Cuando termines, presiona ENTER en esta terminal para procesar los resultados."
            )
            input(
                "\nPresiona ENTER cuando hayas terminado de interactuar con el sitio..."
            )

            current_url = self.page.url
            logger.info(f"URL actual al finalizar interacción: {current_url}")
            if original_url != current_url:
                warning_message = f"Se detectó una navegación/redirección desde la URL base '{original_url}' a '{current_url}'. Los DataLayers finales se capturaron desde esta última URL."
                logger.warning(warning_message)
                print(f"\n⚠️ ADVERTENCIA: {warning_message}")
                if "warnings" not in self.validation_results:
                    self.validation_results["warnings"] = []
                if warning_message not in self.validation_results["warnings"]:
                    self.validation_results["warnings"].append(warning_message)

            time.sleep(1)

            captured_datalayers = []
            try:
                captured_datalayers = self.page.evaluate("window.allDataLayers || [];")
                logger.info(
                    f"Capturados {len(captured_datalayers)} DataLayers desde window.allDataLayers (URL final: {current_url})"
                )
                if not captured_datalayers or len(captured_datalayers) == 0:
                    direct_layers = self.page.evaluate(
                        """
                    if (typeof window.dataLayer !== 'undefined') {
                        return Array.isArray(window.dataLayer) ? window.dataLayer.slice(0) : [window.dataLayer];
                    } else {
                        return [];
                    }
                    """
                    )
                    if direct_layers and len(direct_layers) > 0:
                        captured_datalayers = direct_layers
                        logger.info(
                            f"Capturados {len(direct_layers)} DataLayers directamente de window.dataLayer"
                        )

            except Exception as e:
                logger.error(f"Error al capturar DataLayers desde {current_url}: {e}")
                captured_datalayers = []

            logger.info(
                f"Se han capturado {len(captured_datalayers)} DataLayers en modo interactivo."
            )

            # --- Aquí irá el código para filtrar duplicados (Punto 2) ---
            # Pendiente de implementar en el siguiente paso

            filtered_datalayers = self._filter_datalayers(captured_datalayers)
            logger.info(
                f"DataLayers relevantes para validación: {len(filtered_datalayers)}"
            )

            if not filtered_datalayers:
                self.validation_results["valid"] = False
                self.validation_results["errors"].append(
                    "No se encontraron DataLayers relevantes para validación"
                )
                print(
                    "\n⚠️ ADVERTENCIA: No se detectaron DataLayers relevantes para validación."
                )
                return self.validation_results

            print(
                f"\nSe capturaron {len(captured_datalayers)} DataLayers (incluyendo posibles duplicados y eventos GTM)."
            )
            print(
                f"De los cuales {len(filtered_datalayers)} son relevantes para validación."
            )

            captured_datalayers = (
                filtered_datalayers  # Usar la lista filtrada para la validación
            )

            if captured_datalayers and len(captured_datalayers) > 0:
                print("\nEjemplo del primer DataLayer relevante capturado:")
                try:
                    first_dl = captured_datalayers[0]
                    pretty_json = json.dumps(first_dl, indent=2, ensure_ascii=False)
                    print(pretty_json)
                except Exception as e:
                    print(f"[Error al mostrar el ejemplo: {str(e)}]")

            self.validation_results["summary"]["total_sections"] = len(
                self.schema.get("sections", [])
            )
            self.validation_results["summary"]["not_found_sections"] = (
                self.validation_results["summary"]["total_sections"]
            )

            for i, datalayer in enumerate(captured_datalayers):
                best_match_section = None
                best_match_score = 0
                matched_errors = []

                for section in self.schema.get("sections", []):
                    expected_properties = section.get("datalayer", {}).get(
                        "properties", {}
                    )
                    required_fields = section.get("datalayer", {}).get(
                        "required_fields", []
                    )
                    if not expected_properties:
                        continue

                    score, errors = self._calculate_match_score(
                        datalayer, expected_properties, required_fields
                    )
                    if score > best_match_score:
                        best_match_score = score
                        best_match_section = section
                        matched_errors = errors

                match_threshold = self.config.get("validation", {}).get(
                    "match_threshold", 0.7
                )
                is_valid_match = best_match_score >= match_threshold

                if is_valid_match and best_match_section:
                    # Esta lógica de actualizar el resumen basado en matches se moverá
                    # a la función _compare_with_reference más adelante.
                    # Por ahora la dejamos comentada para evitar doble conteo.
                    # if self.validation_results["summary"]["not_found_sections"] > 0:
                    #    self.validation_results["summary"]["not_found_sections"] -= 1
                    # if len(matched_errors) == 0:
                    #    self.validation_results["summary"]["valid_sections"] += 1
                    # else:
                    #    self.validation_results["summary"]["invalid_sections"] += 1
                    pass

                detail = {
                    "datalayer_index": i,
                    "data": datalayer,
                    "valid": is_valid_match and len(matched_errors) == 0,
                    "errors": matched_errors,
                    "source": "interactive",
                }
                if best_match_section:
                    detail["matched_section"] = best_match_section.get(
                        "title", "Unknown Section"
                    )
                    detail["match_score"] = best_match_score

                self.validation_results["details"].append(detail)

                if i % 10 == 0 and i > 0:
                    print(f"Procesados {i} de {len(captured_datalayers)} DataLayers...")

            # La validez global y el resumen final se calcularán mejor después de la comparación
            # self.validation_results["valid"] = (
            #    self.validation_results["summary"]["valid_sections"] > 0
            # )

            logger.info(
                "Comparando DataLayers relevantes capturados con la referencia..."
            )
            comparison_results = self._compare_with_reference(captured_datalayers)
            self.validation_results["comparison"] = comparison_results

            # Actualizar el resumen basado en la comparación
            self.validation_results["summary"]["matched_count"] = (
                comparison_results.get("matched_count", 0)
            )
            self.validation_results["summary"]["missing_count"] = (
                comparison_results.get("missing_count", 0)
            )
            self.validation_results["summary"]["extra_count"] = comparison_results.get(
                "extra_count", 0
            )
            # Recalcular valid/invalid/not_found basado en la comparación para mayor precisión
            valid_count_comp = 0
            invalid_count_comp = 0
            # Iterar sobre los detalles de match_details para contar válidos/inválidos
            for match_detail in comparison_results.get("match_details", []):
                detail_index = match_detail.get("datalayer_index")
                # Buscar el detalle original para ver sus errores
                original_detail = next(
                    (
                        d
                        for d in self.validation_results["details"]
                        if d.get("datalayer_index") == detail_index
                    ),
                    None,
                )
                if original_detail and not original_detail.get("errors"):
                    valid_count_comp += 1
                elif original_detail:
                    invalid_count_comp += 1

            self.validation_results["summary"]["valid_sections"] = valid_count_comp
            self.validation_results["summary"]["invalid_sections"] = invalid_count_comp
            self.validation_results["summary"]["not_found_sections"] = (
                comparison_results.get("missing_count", 0)
            )  # Los no encontrados son los faltantes
            self.validation_results["valid"] = (
                invalid_count_comp == 0
                and comparison_results.get("missing_count", 0) == 0
            )  # Válido si no hay inválidos ni faltantes

            print("\n=== Comparación con DataLayers de Referencia ===")
            print(
                f"DataLayers en archivo de referencia: {comparison_results.get('reference_count', 0)}"
            )
            print(
                f"DataLayers capturados (relevantes): {comparison_results.get('captured_count', 0)}"
            )
            print(
                f"Coincidencias encontradas: {comparison_results.get('matched_count', 0)}"
            )
            print(f" - Válidos: {valid_count_comp}")
            print(f" - Inválidos: {invalid_count_comp}")
            print(
                f"DataLayers de referencia no encontrados: {comparison_results.get('missing_count', 0)}"
            )
            print(
                f"DataLayers capturados extra: {comparison_results.get('extra_count', 0)}"
            )
            print(
                f"Cobertura (% de referencia encontrados): {comparison_results.get('coverage_percent', 0.0)}%"
            )

            return self.validation_results

        except Exception as e:
            logger.error(
                f"Error durante la validación interactiva: {str(e)}", exc_info=True
            )
            self.validation_results["valid"] = False
            self.validation_results["errors"].append(
                f"Error de validación interactiva: {str(e)}"
            )
            return self.validation_results

        finally:
            self.headless = original_headless
            if hasattr(self, "browser") and self.browser:
                try:
                    self.browser.close()
                    if hasattr(self, "playwright"):
                        self.playwright.stop()
                    logger.info("Navegador cerrado correctamente")
                except Exception as close_err:
                    logger.error(f"Error al cerrar el navegador: {close_err}")

    def validate_all_sections(self) -> Dict[str, Any]:
        """
        Valida todas las secciones del esquema.

        Returns:
           Resultados completos de la validación
        """
        try:
            # Configurar el navegador
            self.setup_driver()

            # Navegar a la URL inicial
            logger.info(f"Navegando a URL inicial: {self.url}")
            self.page.goto(self.url)

            # Esperar a que la página cargue
            timeout = self.config.get("browser", {}).get("wait_timeout", 10) * 1000
            self.page.wait_for_selector("body", timeout=timeout)

            # Extraer secciones del esquema
            sections = self.schema.get("sections", [])
            total_sections = len(sections)

            logger.info(f"Validando {total_sections} secciones")

            # Actualizar resumen
            self.validation_results["summary"]["total_sections"] = total_sections

            # Validar cada sección (método abreviado ya que usaremos principalmente el interactivo)
            self.validation_results["summary"]["not_found_sections"] = total_sections
            self.validation_results["valid"] = False

            return self.validation_results

        except Exception as e:
            logger.error(f"Error durante la validación: {str(e)}", exc_info=True)
            self.validation_results["valid"] = False
            self.validation_results["errors"].append(f"Error de validación: {str(e)}")
            return self.validation_results

        finally:
            if hasattr(self, "browser") and self.browser:
                self.browser.close()
                self.playwright.stop()
                logger.info("Navegador cerrado correctamente")

    def get_results(self) -> Dict[str, Any]:
        """
        Obtiene los resultados de la validación.

        Returns:
            Resultados de la validación
        """
        return self.validation_results
