# src/validator/datalayer_validator.py

import hashlib
import json
import logging
import re
import os
import time
from typing import Dict, List, Any, Tuple, Optional
from urllib.parse import urlparse
from playwright.sync_api import (
    sync_playwright,
    Page,
    Browser,
    Frame,
    TimeoutError as PlaywrightTimeoutError,
)

logger = logging.getLogger(__name__)
LOCAL_STORAGE_KEY = "capturedDataLayersLs"


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
            "warnings": [],  # Lista para warnings generales a nivel de ejecución
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
     En modo interactivo (PWDEBUG) fuerza Chromium headful y limpia cookies/storage.
     """
     browser_config = self.config.get("browser", {})
     self.playwright = sync_playwright().start()

     # Decide si entramos en modo depuración interactiva
     interactive = bool(os.getenv("PWDEBUG")) or getattr(self, "interactive", False)

     # Selecciona navegador: Chromium para interactivo, Firefox para resto
     browser_type = self.playwright.chromium if interactive else self.playwright.firefox

     # Forzamos headful en modo interactivo para ver la UI y el inspector
     headless = False if interactive else self.headless

     # Argumentos comunes
     browser_args = ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
     if self.headless and not interactive:
        browser_args.append("--headless")

     # Lanzamos el navegador
     self.browser = browser_type.launch(headless=headless, args=browser_args)

     # Cargo tamaño de ventana
     window_size = browser_config.get("window_size", {"width": 1920, "height": 1080})

     # Preparo los kwargs de contexto, sólo incluyo user_agent si está definido
     context_kwargs = {
        "viewport": {"width": window_size["width"], "height": window_size["height"]},
        "ignore_https_errors": True
     }
     user_agent = browser_config.get("user_agent")
     if user_agent:
        context_kwargs["user_agent"] = user_agent

     # Creamos un contexto completamente limpio
     self.context = self.browser.new_context(**context_kwargs)

     # <-- Limpieza explícita antes de navegar -->
     # Borra todo rastro de cookies y permisos previos
     self.context.clear_cookies()
     self.context.clear_permissions()

     # Nueva página y timeout
     self.page = self.context.new_page()
     self.page.set_default_timeout(
        browser_config.get("page_load_timeout", 30) * 1000
     )

     logger.info(
        f"Playwright configurado en {'modo interactivo Chromium' if interactive else 'modo normal Firefox'}"
     )

    def _calculate_match_score(
        self,
        datalayer: Dict[str, Any],
        expected_properties: Dict[str, Any],
        required_fields: List[str],
    ) -> Tuple[float, List[str], List[str]]:
        """
        Calcula un score de coincidencia ponderado para un DataLayer capturado
        contra las propiedades esperadas de una referencia.
        También identifica errores específicos (valores, campos faltantes, campos extra) y
        warnings (ej. diferencias solo de mayúsculas/acentos).

        Implementa:
        - Opción B para warnings: El warning por mayúsculas/acentos se añade
          siempre que aplique a un campo, incluso si hay otros errores.
        - NUEVO: Verificación de campos extra: Añade un error si el DataLayer
          capturado tiene campos no definidos en la referencia.
        """
        errors = []  # Lista para acumular todos los errores de esta comparación
        warnings_list = []  # Lista para acumular todos los warnings de esta comparación
        total_expected_props = len(expected_properties)
        if total_expected_props == 0:
            return (
                0.0,
                [
                    "No hay propiedades esperadas definidas en el esquema de referencia para esta sección"
                ],
                [],
            )

        # Definición de campos clave y pesos
        key_fields_primary = ["event", "event_category", "event_action", "event_label"]
        key_fields_secondary = ["component_name"]
        primary_weight = 0.60
        secondary_weight = 0.20
        other_weight = 0.20

        # Contadores para cálculo de score ponderado
        matched_primary, total_primary_in_expected = 0, 0
        matched_secondary, total_secondary_in_expected = 0, 0
        matched_other, total_other_in_expected = 0, 0

        # Listas temporales para agrupar mensajes de error por tipo
        primary_errors, secondary_errors, other_errors = [], [], []

        # --- INICIO BUCLE PRINCIPAL DE COMPARACIÓN POR CAMPO (Referencia vs Capturado) ---
        for prop, expected_value in expected_properties.items():
            actual_value = datalayer.get(prop)
            is_dynamic = expected_value is None or (
             isinstance(expected_value, str)
             and (("{" in expected_value and "}" in expected_value) or # <-- NUEVO: Chequea llaves simples {}
             ("{{") in expected_value and "}}" in expected_value) # <-- MANTIENE: Chequeo original {{}}
            )
            is_primary = prop in key_fields_primary
            is_secondary = prop in key_fields_secondary
            field_type_log = "otro"
            if is_primary:
                field_type_log = "clave primario"
            elif is_secondary:
                field_type_log = "clave secundario"

            prop_matched = False
            prop_error = False
            prop_warning = False

            if prop in datalayer:
                if not is_dynamic:
                    if isinstance(expected_value, str) and isinstance(
                        actual_value, str
                    ):
                        norm_expected = self._normalize_string(expected_value)
                        norm_actual = self._normalize_string(actual_value)
                        if norm_expected == norm_actual:
                            prop_matched = True
                        else:
                            clean_expected = self._clean_string(expected_value)
                            clean_actual = self._clean_string(actual_value)
                            if clean_expected == clean_actual:
                                prop_matched = True
                                prop_warning = True  # Marcar para añadir warning
                            else:
                                prop_error = True  # Error de valor fundamental
                                current_error_msg = f"Valor para '{field_type_log} {prop}' no coincide: esperado '{expected_value}', encontrado '{actual_value}'"
                                if is_primary:
                                    primary_errors.append(current_error_msg)
                                elif is_secondary:
                                    secondary_errors.append(current_error_msg)
                                else:
                                    other_errors.append(current_error_msg)
                    elif actual_value == expected_value:
                        prop_matched = True
                    else:
                        prop_error = (
                            True  # Error de valor (tipos no string o diferentes)
                        )
                        current_error_msg = f"Valor para '{field_type_log} {prop}' no coincide: esperado '{expected_value}', encontrado '{actual_value}'"
                        if is_primary:
                            primary_errors.append(current_error_msg)
                        elif is_secondary:
                            secondary_errors.append(current_error_msg)
                        else:
                            other_errors.append(current_error_msg)
                else:  # Campo dinámico existe
                    prop_matched = True

                if prop_matched:  # Contar para score
                    if is_primary:
                        matched_primary += 1
                    elif is_secondary:
                        matched_secondary += 1
                    else:
                        matched_other += 1

            # Añadir WARNING si aplica (independiente de otros errores)
            if prop_warning:
                current_warning_msg = f"Coincidencia sensible a mayúsculas/acentos para '{prop}': esperado '{expected_value}', encontrado '{actual_value}'"
                warnings_list.append(current_warning_msg)

            # Contar totales esperados para el cálculo del score
            if is_primary:
                total_primary_in_expected += 1
            elif is_secondary:
                total_secondary_in_expected += 1
            else:
                total_other_in_expected += 1
        # --- FIN DEL BUCLE DE COMPARACIÓN POR CAMPO ---

        # -- Preparar conjuntos de claves para verificar faltantes y extras --
        captured_keys = set(datalayer.keys())
        expected_keys = set(expected_properties.keys())

        # 2. Verificar CAMPOS FALTANTES (Error Crítico)
        missing_keys = expected_keys - captured_keys
        missing_field_errors = []
        if missing_keys:
            for missing_key in missing_keys:
                missing_field_errors.append(
                    f"Campo '{missing_key}' presente en la referencia pero AUSENTE en el DataLayer capturado"
                )

        # 3. NUEVO: Verificar CAMPOS EXTRA (Error Crítico)
        extra_keys = captured_keys - expected_keys
        extra_field_errors = []
        if extra_keys:
            # Crear mensaje de error listando los campos extra
            extra_field_errors.append(
                f"Campo(s) extra encontrados en DataLayer capturado no definidos en la referencia: {sorted(list(extra_keys))}"
            )
            logger.debug(
                f"Campos extra detectados: {sorted(list(extra_keys))} en DL: {datalayer}"
            )

        # 4. Combinar todos los errores encontrados
        # (Errores de valor + Errores de campos faltantes + NUEVO: Errores de campos extra)
        errors.extend(primary_errors)
        errors.extend(secondary_errors)
        errors.extend(other_errors)
        errors.extend(missing_field_errors)
        errors.extend(
            extra_field_errors
        )  # Añadir errores de campos extra a la lista final

        # 5. Calcular Puntuación Final (basada SOLO en coincidencias de valor de campos esperados)
        # La presencia de errores (faltantes o extra) determinará la validez, no directamente el score.
        primary_score = (
            (matched_primary / total_primary_in_expected)
            if total_primary_in_expected > 0
            else 1.0
        )
        secondary_score = (
            (matched_secondary / total_secondary_in_expected)
            if total_secondary_in_expected > 0
            else 1.0
        )
        other_score = (
            (matched_other / total_other_in_expected)
            if total_other_in_expected > 0
            else 1.0
        )

        # Penalización fuerte al score si el campo 'event' estático no coincide exactamente
        event_prop = "event"
        if event_prop in expected_properties and not (
            expected_properties[event_prop] is None
            or (
                isinstance(expected_properties[event_prop], str)
                and "{{" in expected_properties[event_prop]
            )
        ):
            norm_event_expected = self._normalize_string(
                expected_properties[event_prop]
            )
            norm_event_actual = self._normalize_string(datalayer.get(event_prop, None))
            if norm_event_expected != norm_event_actual:
                logger.debug(
                    f"Penalizando score (primario) por no coincidencia exacta en 'event': esperado '{norm_event_expected}', encontrado '{norm_event_actual}'"
                )
                primary_score *= 0.1

        final_score = (
            (primary_score * primary_weight)
            + (secondary_score * secondary_weight)
            + (other_score * other_weight)
        )
        final_score = min(max(final_score, 0.0), 1.0)  # Asegurar rango [0, 1]
        if primary_errors and primary_score < 0.5:
            final_score *= 0.5  # Penalización adicional

        # Devolver score, lista COMPLETA de errores, y lista COMPLETA de warnings
        return final_score, errors, warnings_list

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

    def _filter_datalayers(
        self, captured_datalayers: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Filtra los DataLayers capturados para mantener ÚNICAMENTE aquellos
        que son diccionarios y tienen event: "GAEvent".

        Args:
           captured_datalayers: Lista de DataLayers capturados (ya únicos)

        Returns:
           Lista filtrada solo con DataLayers GAEvent relevantes.
        """
        if not captured_datalayers:
            logger.warning("No se recibieron DataLayers para filtrar")
            return []

        logger.info(f"Filtrando {len(captured_datalayers)} DataLayers únicos para mantener solo GAEvent...")

        # Filtrar DataLayers
        filtered_datalayers = []
        excluded_count = 0
        for dl in captured_datalayers:
            # Verificar si es un diccionario y tiene la clave 'event' con valor 'GAEvent'
            if (
                isinstance(dl, dict)
                and dl.get("event") == "GAEvent"
            ):
                filtered_datalayers.append(dl)
            else:
                # Loguear qué se excluye (opcional, útil para depurar)
                # logger.debug(f"Excluyendo DataLayer por no ser GAEvent: {dl}")
                excluded_count += 1

        logger.info(
             f"Filtrado GAEvent completado: {len(filtered_datalayers)} relevantes restantes. ({excluded_count} excluidos)"
        )

        # Si después del filtrado no quedan DataLayers, devolver lista vacía
        if not filtered_datalayers:
            logger.warning(
                "El filtrado GAEvent eliminó todos los DataLayers. Devolviendo lista vacía."
            )
            return []

        return filtered_datalayers

    def _validate_datalayer(
        self,
        datalayer: Dict[str, Any],
        expected_properties: Dict[str, Any],
        required_fields: List[str],
    ) -> List[str]:
        """
        (Función original, no modificada por los requerimientos de warnings/extras,
         ya que _calculate_match_score ahora maneja la lógica principal de comparación)
        Valida un datalayer específico contra las propiedades y campos requeridos.
        """
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
                # Este caso ya debería estar cubierto por la verificación de campos requeridos,
                # pero lo dejamos por redundancia.
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

    def _handle_navigation(self, frame: Frame):
        try:
            if frame.parent_frame:
                return
            new_url = frame.url
            if (
                not new_url
                or new_url.startswith("about:")
                or new_url.startswith("chrome-error://")
            ):
                return
            if not hasattr(self, "original_interactive_url"):
                return

            original_domain = urlparse(self.original_interactive_url).netloc
            new_domain = urlparse(new_url).netloc
            if new_domain == original_domain:
                return

            if not self.external_navigation_detected:
                warning_message = (
                    f"⚠️ ¡ADVERTENCIA DE NAVEGACIÓN! Se detectó salida a un dominio externo.\n"
                    f"   - Dominio Original: {original_domain}\n"
                    f"   - Nuevo Dominio:    {new_domain} ({new_url})\n"
                    f"   - (La captura continuará si vuelves al dominio original gracias a localStorage)."
                )
                print(f"\n{warning_message}\n")
                logger.warning(
                    f"Navegación externa detectada de {original_domain} a {new_domain}"
                )
                self.external_navigation_detected = True
        except Exception as e:
            logger.error(f"Error en _handle_navigation: {e}", exc_info=False)

    # --- Función _compare_with_reference MODIFICADA ---
    def _compare_with_reference(
        self, captured_datalayers: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Compara la lista de DataLayers capturados con las referencias del esquema.
        Calcula coincidencias, referencias faltantes.
        """
        comparison_results = {
            "reference_count": 0,
            "captured_count": len(captured_datalayers),
            "matched_count": 0,
            "missing_count": 0,
            "missing_details": [],
            "coverage_percent": 0.0,
        }
        reference_datalayers = []
        if self.schema and "sections" in self.schema:
            for idx, section in enumerate(self.schema["sections"]):
                datalayer_section = section.get("datalayer", {})
                properties = datalayer_section.get("properties")
                if properties:
                    reference_datalayers.append(
                        {
                            "properties": properties,
                            "title": section.get("title", f"Sección sin título {idx}"),
                            "id": section.get("id", f"no_id_{idx}"),
                            "required_fields": datalayer_section.get(
                                "required_fields", []
                            ),
                            "match_found": False,  # Flag para rastrear si esta referencia fue encontrada
                        }
                    )
        comparison_results["reference_count"] = len(reference_datalayers)
        match_threshold = self.config.get("validation", {}).get("match_threshold", 0.7)

        # Iterar sobre los capturados para marcar las referencias encontradas
        for i, captured_dl in enumerate(captured_datalayers):
            best_match_score = -1.0
            best_match_ref_idx = -1
            # No necesitamos warnings aquí, solo el score para marcar el match
            for j, ref_dl in enumerate(reference_datalayers):
                # Usamos la nueva firma de _calculate_match_score, pero ignoramos errors/warnings aquí
                score, _, _ = self._calculate_match_score(
                    captured_dl, ref_dl["properties"], ref_dl.get("required_fields", [])
                )
                if score > best_match_score:
                    best_match_score = score
                    best_match_ref_idx = j

            # Si se encontró un match válido para este capturado, marcar la referencia correspondiente
            if best_match_ref_idx != -1 and best_match_score >= match_threshold:
                # Marcar la referencia como encontrada (solo la primera vez que se encuentra)
                if not reference_datalayers[best_match_ref_idx]["match_found"]:
                    reference_datalayers[best_match_ref_idx]["match_found"] = True

        # Contar referencias encontradas y faltantes
        final_missing_count = 0
        final_matched_count = 0  # Contaremos las referencias que sí se encontraron
        comparison_results["missing_details"] = []
        for idx, ref_dl in enumerate(reference_datalayers):
            if ref_dl["match_found"]:
                final_matched_count += 1
            else:
                final_missing_count += 1
                comparison_results["missing_details"].append(
                    {
                        "reference_title": ref_dl["title"],
                        "reference_id": ref_dl["id"],
                        "properties": ref_dl["properties"],
                    }
                )
        comparison_results["matched_count"] = (
            final_matched_count  # Basado en referencias únicas encontradas
        )
        comparison_results["missing_count"] = final_missing_count

        # Calcular cobertura
        if comparison_results["reference_count"] > 0:
            # La cobertura se basa en cuántas referencias únicas se encontraron
            comparison_results["coverage_percent"] = round(
                (final_matched_count / comparison_results["reference_count"]) * 100,
                1,
            )
        else:
            comparison_results["coverage_percent"] = 0.0

        # logger.debug(f"Resultados comparación final: {comparison_results}") # Log quitado
        return comparison_results

    def interactive_validation(self) -> Dict[str, Any]:
        """
        Realiza la validación en modo interactivo, capturando DataLayers
        desde localStorage y comparándolos con el esquema.
        Incluye warnings por tiempo, por coincidencias sensibles a mayúsculas/acentos,
        y marca los DataLayers sin coincidencia clara con un warning.
        Calcula el resumen basado en DataLayers únicos.
        """
        self.external_navigation_detected = False
        self.original_interactive_url = self.url
        original_headless = self.headless  # Guardar estado original

        try:
            self.headless = False  # Forzar modo visible para interacción
            self.setup_driver()  # Llama a setup_driver aquí

            # --- Script de inicialización para captura en localStorage (sin cambios) ---
            init_script = (
                """
                (() => {
                    const LS_KEY = '"""
                + LOCAL_STORAGE_KEY
                + """'; let capturedList = [];
                    try { const existingData = localStorage.getItem(LS_KEY); if (existingData) { capturedList = JSON.parse(existingData); if (!Array.isArray(capturedList)) capturedList = []; } } catch (e) { console.error('Error reading initial LS:', e); capturedList = []; }
                    window.dataLayer = window.dataLayer || []; const originalPush = window.dataLayer.push; let initialItemsProcessed = false;
                    // Procesar items iniciales si existen y no tienen timestamp
                    if (Array.isArray(window.dataLayer) && window.dataLayer.length > 0) { const initialTimestamp = Date.now(); let addedFromInitial = 0; for (const obj of window.dataLayer) { if (typeof obj === 'object' && obj !== null && typeof obj._captureTimestamp === 'undefined') { try { capturedList.push({ ...JSON.parse(JSON.stringify(obj)), _captureTimestamp: initialTimestamp }); addedFromInitial++; } catch (e) { console.error('Error cloning initial DL:', e, obj); } } else if ((typeof obj !== 'object' || obj === null) && typeof obj?._captureTimestamp === 'undefined') { capturedList.push({ nonObjectData: obj, _captureTimestamp: initialTimestamp }); addedFromInitial++; } } if(addedFromInitial > 0) { console.log('Processed ' + addedFromInitial + ' initial items.'); initialItemsProcessed = true; } }
                    // Guardar si se procesaron items iniciales
                    if(initialItemsProcessed) { try { localStorage.setItem(LS_KEY, JSON.stringify(capturedList)); } catch (e) { console.error('Error saving initial DLs to LS:', e); } }
                    // Sobreescribir dataLayer.push
                    window.dataLayer.push = function(...args) {
                        const timestamp = Date.now(); let currentCapturedList = [];
                        // Recargar desde LS por si se navegó externamente y se volvió
                        try { currentCapturedList = JSON.parse(localStorage.getItem(LS_KEY) || '[]'); if (!Array.isArray(currentCapturedList)) currentCapturedList = []; } catch(e) { console.error('Error reloading LS:', e); currentCapturedList = []; }
                        let itemsPushedCount = 0;
                        for (const obj of args) { if (typeof obj === 'object' && obj !== null) { try { currentCapturedList.push({ ...JSON.parse(JSON.stringify(obj)), _captureTimestamp: timestamp }); itemsPushedCount++; } catch (e) { console.error('Error cloning/pushing DL:', e, obj); } } else { currentCapturedList.push({ nonObjectData: obj, _captureTimestamp: timestamp }); itemsPushedCount++; } }
                        if (itemsPushedCount > 0) { try { localStorage.setItem(LS_KEY, JSON.stringify(currentCapturedList)); } catch (e) { console.error('Error saving DLs to LS:', e); } }
                        return originalPush.apply(window.dataLayer, args); // Llamar al push original
                    }; console.log('DataLayer LS capture init. Key: ' + LS_KEY + '. Items in LS: ' + capturedList.length);
                })();
                """
            )
            self.page.add_init_script(init_script)

            logger.info(f"Navegando a URL inicial: {self.url}")
            self.page.goto(self.url)
            try:
                self.page.wait_for_load_state("networkidle", timeout=10000)
            except Exception as e:
                logger.warning(f"Timeout/error en networkidle inicial: {e}")

            self.original_interactive_url = self.page.url
            logger.info(f"URL base establecida: {self.original_interactive_url}")

            self.page.on("framenavigated", self._handle_navigation)

            print("\n=== MODO INTERACTIVO DE VALIDACIÓN ===")
            print(f"Navegador abierto para: {self.original_interactive_url}")
            print("Instrucciones:")
            print("1. Interactúa con el sitio.")
            print("2. Los DataLayers se guardarán en localStorage.")
            print(
                f"3. Verifica en consola JS: console.log(JSON.parse(localStorage.getItem('{LOCAL_STORAGE_KEY}')))"
            )
            print("4. Presiona ENTER aquí para finalizar y procesar.")

            input("\nPresiona ENTER para finalizar...")

            captured_datalayers_raw = []
            try:
                logger.info(
                    f"Recuperando DataLayers desde localStorage (key: {LOCAL_STORAGE_KEY})..."
                )
                self.page.wait_for_timeout(250)  # Pequeña espera por si acaso
                ls_data_str = self.page.evaluate(
                    f"localStorage.getItem('{LOCAL_STORAGE_KEY}')"
                )
                if ls_data_str:
                    captured_datalayers_raw = json.loads(ls_data_str)
                    if not isinstance(captured_datalayers_raw, list):
                        captured_datalayers_raw = []
                    logger.info(
                        f"Éxito: {len(captured_datalayers_raw)} DLs recuperados de localStorage."
                    )
                else:
                    logger.warning("No se encontraron datos en localStorage.")
                    captured_datalayers_raw = []
            except Exception as e:
                logger.error(
                    f"Fallo al recuperar/parsear DLs de localStorage: {e}",
                    exc_info=True,
                )
                captured_datalayers_raw = []

            try:
                self.page.remove_listener("framenavigated", self._handle_navigation)
            except Exception as e:
                logger.warning(f"No se pudo remover listener 'framenavigated': {e}")

            logger.info(f"Procesando {len(captured_datalayers_raw)} DLs obtenidos.")

            # 1. Deduplicación
            processed_datalayers_unique = []
            seen_datalayers_repr = set()
            original_count = len(captured_datalayers_raw)
            logger.info(f"Eliminando duplicados de {original_count} DLs...")
            for dl in captured_datalayers_raw:
                dl_copy_for_dedup = (
                    {k: v for k, v in dl.items() if k != "_captureTimestamp"}
                    if isinstance(dl, dict)
                    else dl
                )
                try:
                    dl_representation = json.dumps(
                        dl_copy_for_dedup, sort_keys=True, ensure_ascii=False
                    )
                    if dl_representation not in seen_datalayers_repr:
                        seen_datalayers_repr.add(dl_representation)
                        processed_datalayers_unique.append(dl)
                except TypeError as e:
                    logger.warning(
                        f"No se pudo serializar DL para deduplicación: {dl} - Error: {e}. Se incluirá."
                    )
                    processed_datalayers_unique.append(dl)

            unique_count = len(processed_datalayers_unique)
            logger.info(
                f"Deduplicación completa. Originales: {original_count}, Únicos: {unique_count}"
            )

            # 2. Cálculo de Warnings de Tiempo (SOBRE LISTA ÚNICA)
            logger.info(
                f"Calculando warnings de tiempo para {unique_count} DLs únicos..."
            )
            time_threshold = self.config.get("validation", {}).get(
                "warning_time_threshold_ms", 500
            )
            previous_timestamp = None
            time_warnings_map = {}
            for i, datalayer_with_ts in enumerate(processed_datalayers_unique):
                current_timestamp = datalayer_with_ts.get("_captureTimestamp")
                time_warnings_for_this_dl = []
                if i > 0 and previous_timestamp and current_timestamp:
                    time_diff = current_timestamp - previous_timestamp
                    if time_diff < time_threshold:
                        warning_msg = f"Evento rápido: Ocurrió {time_diff} ms después del DataLayer anterior (umbral: {time_threshold} ms)."
                        time_warnings_for_this_dl.append(warning_msg)
                if time_warnings_for_this_dl:
                    time_warnings_map[i] = time_warnings_for_this_dl
                previous_timestamp = current_timestamp
            logger.info(
                f"Se encontraron warnings de tiempo para {len(time_warnings_map)} DLs."
            )

            # 3. Filtrado de Eventos GTM (SOBRE LISTA ÚNICA)
            captured_datalayers_final = self._filter_datalayers(
                processed_datalayers_unique
            )
            relevant_count = len(captured_datalayers_final)
            logger.info(f"DLs relevantes (únicos y sin GTM): {relevant_count}")
            original_indices_map = {
                id(dl): idx for idx, dl in enumerate(processed_datalayers_unique)
            }

            if not captured_datalayers_final:
                self.validation_results["valid"] = False
                error_msg = "No se encontraron DataLayers relevantes para validar después del filtrado."
                self.validation_results["errors"].append(error_msg)
                self.validation_results["warnings"].append(error_msg)
                logger.error(error_msg)
                return self.validation_results

            print(
                f"\nCapturados (brutos): {original_count}. Únicos: {unique_count}. Relevantes (sin GTM): {relevant_count}."
            )
            if captured_datalayers_final:
                print("\nPrimer DL relevante:")
                try:
                    first_dl_display = {
                        k: v
                        for k, v in captured_datalayers_final[0].items()
                        if k != "_captureTimestamp"
                    }
                    print(json.dumps(first_dl_display, indent=2, ensure_ascii=False))
                except Exception as e:
                    print(f"[Error al mostrar ejemplo: {str(e)}]")

            # 4. Validación y Combinación de Warnings (Iterando sobre lista final filtrada)
            self.validation_results["summary"]["total_sections"] = len(
                self.schema.get("sections", [])
            )
            self.validation_results["details"] = []
            # QUITAR contadores inmediatos: valid_count_details = 0
            # QUITAR contadores inmediatos: invalid_count_details = 0
            match_threshold = self.config.get("validation", {}).get(
                "match_threshold", 0.7
            )

            logger.info(
                f"Iniciando validación final para {relevant_count} DLs relevantes..."
            )

            for i, datalayer_with_ts in enumerate(captured_datalayers_final):
                original_index = original_indices_map.get(id(datalayer_with_ts))
                time_warnings = (
                    time_warnings_map.get(original_index, [])
                    if original_index is not None
                    else []
                )
                datalayer = {
                    k: v
                    for k, v in datalayer_with_ts.items()
                    if k != "_captureTimestamp"
                }
                current_timestamp = datalayer_with_ts.get("_captureTimestamp")
                combined_warnings = list(time_warnings)
                match_warnings = []
                best_match_section_info = None
                best_match_score = -1.0
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
                    score, errors_for_this_match, warnings_for_this_match = (
                        self._calculate_match_score(
                            datalayer, expected_properties, required_fields
                        )
                    )
                    if score > best_match_score:
                        best_match_score = score
                        best_match_section_info = {
                            "title": section.get("title", "Unknown Section"),
                            "properties": expected_properties,
                            "id": section.get("id"),
                        }
                        matched_errors = errors_for_this_match
                        match_warnings = warnings_for_this_match

                combined_warnings.extend(match_warnings)

                detail_is_valid = None
                if best_match_score >= match_threshold:
                    if not matched_errors:
                        detail_is_valid = True
                        # QUITAR: valid_count_details += 1
                    else:
                        detail_is_valid = False
                        # QUITAR: invalid_count_details += 1
                else:
                    warning_msg = f"DataLayer no coincide con ninguna referencia conocida (Mejor score: {best_match_score*100:.1f}%)"
                    combined_warnings.append(warning_msg)
                    logger.debug(
                        f"DL {i} marcado como no coincidente (warning añadido)."
                    )

                detail = {
                    "datalayer_index": i,
                    "data": datalayer,
                    "valid": detail_is_valid,
                    "errors": matched_errors if detail_is_valid is False else [],
                    "warnings": combined_warnings,
                    "source": "interactive",
                    "matched_section_id": (
                        best_match_section_info["id"]
                        if best_match_section_info
                        and best_match_score >= match_threshold
                        else None
                    ),
                    "matched_section": (
                        best_match_section_info["title"]
                        if best_match_section_info
                        and best_match_score >= match_threshold
                        else None
                    ),
                    "match_score": (
                        best_match_score if best_match_section_info else None
                    ),
                    "reference_data": (
                        self._sort_reference_properties(
                            datalayer, best_match_section_info["properties"]
                        )
                        if best_match_section_info
                        and best_match_score >= match_threshold
                        else None
                    ),
                    "_captureTimestamp": current_timestamp,
                }
                self.validation_results["details"].append(detail)

                if i > 0 and (i + 1) % 10 == 0:
                    print(f"Procesados {i + 1}/{relevant_count} DLs...")

            # 5. NUEVO: Calcular Resumen de Únicos
            logger.info("Calculando resumen de DataLayers únicos...")
            unique_valid_matches_set = set()
            unique_invalid_matches_set = set()
            unique_warning_items_set = set()
            unique_unmatched_set = set()
            # Almacenar representaciones para depuración si es necesario
            # debug_identifiers = {}

            for detail in self.validation_results["details"]:
                unique_identifier = None
                # Usar ID de sección como identificador si hubo match válido o inválido
                if detail["matched_section_id"] and detail["valid"] is not None:
                    unique_identifier = f"ref_{detail['matched_section_id']}"
                else:  # Si no hubo match claro (valid es None)
                    # Usar hash del contenido del datalayer como identificador
                    try:
                        # Ordenar claves para consistencia del hash
                        dl_string = json.dumps(
                            detail["data"], sort_keys=True, ensure_ascii=False
                        )
                        unique_identifier = f"dl_{hashlib.sha1(dl_string.encode('utf-8')).hexdigest()[:16]}"  # Hash más largo
                    except Exception as hash_err:
                        logger.error(
                            f"Error generando hash para DL {detail['datalayer_index']}: {hash_err}"
                        )
                        unique_identifier = (
                            f"dl_error_{detail['datalayer_index']}"  # Fallback
                        )

                # debug_identifiers[detail['datalayer_index']] = unique_identifier # Para depuración

                # Contar categorías únicas
                if detail["valid"] is True:
                    unique_valid_matches_set.add(unique_identifier)
                elif detail["valid"] is False:
                    unique_invalid_matches_set.add(unique_identifier)
                # El caso detail["valid"] is None (no match claro) se cuenta indirectamente
                # al comparar el total con válidos+inválidos, o podemos contarlo explícitamente:
                elif detail["valid"] is None:
                    unique_unmatched_set.add(unique_identifier)

                # Contar items únicos CON warnings (independiente de validez)
                if detail["warnings"]:
                    unique_warning_items_set.add(unique_identifier)

            unique_valid_count = len(unique_valid_matches_set)
            unique_invalid_count = len(unique_invalid_matches_set)
            unique_warning_count = len(unique_warning_items_set)
            unique_unmatched_count = len(unique_unmatched_set)
            # El total único debe considerar todas las categorías identificadas
            total_unique_identified = len(
                unique_valid_matches_set
                | unique_invalid_matches_set
                | unique_unmatched_set
            )

            logger.info(
                f"Recuento Único - Válidos: {unique_valid_count}, Inválidos: {unique_invalid_count}, Con Warnings: {unique_warning_count}, No Coincidentes: {unique_unmatched_count}, Total Únicos: {total_unique_identified}"
            )
            # print(f"DEBUG Identifiers: {debug_identifiers}") # Descomentar para depurar identificadores

            # Actualizar el diccionario summary con los recuentos únicos
            self.validation_results["summary"][
                "unique_valid_matches"
            ] = unique_valid_count
            self.validation_results["summary"][
                "unique_invalid_matches"
            ] = unique_invalid_count
            self.validation_results["summary"][
                "unique_datalayers_with_warnings"
            ] = unique_warning_count
            self.validation_results["summary"][
                "unique_unmatched_datalayers"
            ] = unique_unmatched_count
            self.validation_results["summary"][
                "total_unique_captured_relevant"
            ] = total_unique_identified

            logger.info("Calculando resultados finales de comparación...")
            datalayers_for_comparison = [
                {k: v for k, v in d.items() if k != "_captureTimestamp"}
                for d in captured_datalayers_final
            ]
            comparison_results = self._compare_with_reference(datalayers_for_comparison)
            self.validation_results["comparison"] = comparison_results
            missing_count_final = comparison_results.get("missing_count", 0)
            matched_count_final = comparison_results.get("matched_count", 0)
            # Actualizar not_found_sections con el resultado de la comparación
            self.validation_results["summary"][
                "not_found_sections"
            ] = missing_count_final

            # 7. Determinar validez general final: Inválido si hay matches únicos inválidos O si faltan referencias
            self.validation_results["valid"] = (
                unique_invalid_count == 0 and missing_count_final == 0
            )

            # Imprimir resumen final en consola usando los contadores ÚNICOS
            print("\n=== Resumen Final (Consola - Basado en Únicos) ===")
            print(
                f"Referencias Totales: {comparison_results.get('reference_count', 0)}"
            )
            print(
                f"Capturados Relevantes (Total): {relevant_count}"
            )  # Total de items procesados
            print(
                f"Capturados Relevantes (Únicos Identificados): {total_unique_identified}"
            )  # Total de items únicos
            print(
                f"  - Matches Válidos Únicos: {unique_valid_count}"
            )  # Items únicos que coincidieron sin error
            print(
                f"  - Matches Inválidos Únicos: {unique_invalid_count}"
            )  # Items únicos que coincidieron CON error
            print(
                f"  - DLs Únicos No Coincidentes (antes 'Extra'): {unique_unmatched_count}"
            )  # Items únicos sin match claro
            print(
                f"  - DLs Únicos Con Warnings (cualquier tipo): {unique_warning_count}"
            )  # Items únicos con al menos un warning
            print(
                f"Referencias No Encontradas: {missing_count_final}"
            )  # Referencias que no tuvieron match
            print(
                f"Cobertura (% referencias encontradas): {comparison_results.get('coverage_percent', 0.0):.1f}%"
            )

            return self.validation_results

        except Exception as e:
            logger.error(
                f"Error durante validación interactiva: {str(e)}", exc_info=True
            )
            self.validation_results["valid"] = False
            self.validation_results["errors"].append(f"Error validación: {str(e)}")
            if not isinstance(self.validation_results.get("warnings"), list):
                self.validation_results["warnings"] = []
            self.validation_results["warnings"].append(f"Error General: {str(e)}")
            return self.validation_results
        finally:
            self.headless = original_headless
            if hasattr(self, "page") and self.page and not self.page.is_closed():
                try:
                    self.page.remove_listener("framenavigated", self._handle_navigation)
                except Exception:
                    pass
                try:
                    self.page.evaluate(
                        f"localStorage.removeItem('{LOCAL_STORAGE_KEY}')"
                    )
                    logger.info(f"LocalStorage limpiado (key: {LOCAL_STORAGE_KEY}).")
                except Exception as ls_clean_err:
                    logger.warning(
                        f"No se pudo limpiar localStorage al final: {ls_clean_err}"
                    )
            if hasattr(self, "browser") and self.browser:
                try:
                    self.browser.close()
                    logger.info("Navegador cerrado.")
                except Exception as close_err:
                    logger.error(f"Error al cerrar navegador: {close_err}")
            if hasattr(self, "playwright"):
                try:
                    self.playwright.stop()
                except Exception as stop_err:
                    logger.error(f"Error al detener playwright: {stop_err}")

    # --- FIN Función interactive_validation MODIFICADA ---

    def validate_all_sections(self) -> Dict[str, Any]:
        # ... (código sin cambios) ...
        try:
            self.setup_driver()
            logger.info(f"Navegando a URL inicial: {self.url}")
            self.page.goto(self.url)
            timeout = self.config.get("browser", {}).get("wait_timeout", 10) * 1000
            self.page.wait_for_selector("body", timeout=timeout)
            sections = self.schema.get("sections", [])
            total_sections = len(sections)
            logger.info(f"Validando {total_sections} secciones")
            self.validation_results["summary"]["total_sections"] = total_sections
            self.validation_results["summary"]["not_found_sections"] = total_sections
            self.validation_results["valid"] = False
            logger.warning(
                "Modo automático no implementado completamente. Use --interactive."
            )
            self.validation_results["warnings"].append(
                "Modo automático no implementado completamente."
            )
            return self.validation_results
        except Exception as e:
            logger.error(f"Error durante la validación: {str(e)}", exc_info=True)
            self.validation_results["valid"] = False
            self.validation_results["errors"].append(f"Error de validación: {str(e)}")
            return self.validation_results
        finally:
            if hasattr(self, "browser") and self.browser:
                try:
                    self.browser.close()
                    logger.info(
                        "Navegador cerrado correctamente (validate_all_sections)"
                    )
                except Exception as e:
                    logger.error(
                        f"Error cerrando navegador en validate_all_sections: {e}"
                    )
            if hasattr(self, "playwright"):
                try:
                    self.playwright.stop()
                except Exception as e:
                    logger.error(
                        f"Error deteniendo playwright en validate_all_sections: {e}"
                    )

    def validate_all_sections(self) -> Dict[str, Any]:
        """
        Valida todas las secciones del esquema. (Función no modificada)

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
            # Esta parte necesitaría una implementación más robusta si se usara el modo automático.
            # Por ahora, simplemente marcamos todo como no encontrado si no es modo interactivo.
            self.validation_results["summary"]["not_found_sections"] = total_sections
            self.validation_results["valid"] = (
                False  # Asumimos inválido si no es interactivo
            )

            logger.warning(
                "Modo automático no implementado completamente. Use --interactive."
            )
            self.validation_results["warnings"].append(
                "Modo automático no implementado completamente."
            )

            return self.validation_results

        except Exception as e:
            logger.error(f"Error durante la validación: {str(e)}", exc_info=True)
            self.validation_results["valid"] = False
            self.validation_results["errors"].append(f"Error de validación: {str(e)}")
            return self.validation_results

        finally:
            if hasattr(self, "browser") and self.browser:
                try:
                    self.browser.close()
                    logger.info(
                        "Navegador cerrado correctamente (validate_all_sections)"
                    )
                except Exception as e:
                    logger.error(
                        f"Error cerrando navegador en validate_all_sections: {e}"
                    )
            if hasattr(self, "playwright"):
                try:
                    self.playwright.stop()
                except Exception as e:
                    logger.error(
                        f"Error deteniendo playwright en validate_all_sections: {e}"
                    )

    def get_results(self) -> Dict[str, Any]:
        """
        Obtiene los resultados de la validación. (Función no modificada)

        Returns:
            Resultados de la validación
        """
        return self.validation_results
