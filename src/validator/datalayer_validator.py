import json
import logging
import re
import os
import time
from typing import Dict, List, Any, Tuple, Optional

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from webdriver_manager.chrome import ChromeDriverManager

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
        Configura el driver de Selenium según los parámetros de configuración.
        """
        browser_config = self.config.get("browser", {})

        chrome_options = Options()
        if self.headless:
            chrome_options.add_argument("--headless")

        # Buscar binario de Chrome en varias ubicaciones comunes
        chrome_paths = [
            "/usr/bin/google-chrome",  # Linux
            "/usr/bin/chromium-browser",  # Linux Chromium
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",  # macOS
            "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",  # Windows
            "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",  # Windows 32-bit
        ]

        # Verificar si existe el binario en alguna de las rutas
        for path in chrome_paths:
            if os.path.exists(path):
                chrome_options.binary_location = path
                logger.info(f"Se encontró Chrome en: {path}")
                break

        # Configurar tamaño de ventana
        window_size = browser_config.get("window_size", {"width": 1920, "height": 1080})
        chrome_options.add_argument(
            f"--window-size={window_size['width']},{window_size['height']}"
        )

        # User agent personalizado
        user_agent = browser_config.get("user_agent")
        if user_agent:
            chrome_options.add_argument(f"--user-agent={user_agent}")

        # Argumentos adicionales para estabilidad
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")

        service = Service(ChromeDriverManager().install())
        self.driver = webdriver.Chrome(service=service, options=chrome_options)

        # Configurar timeouts
        self.driver.set_page_load_timeout(browser_config.get("page_load_timeout", 30))
        logger.info("WebDriver configurado correctamente")

    def _calculate_match_score(
        self,
        datalayer: Dict[str, Any],
        expected_properties: Dict[str, Any],
        required_fields: List[str],
    ) -> Tuple[float, List[str]]:

        errors = []
        total_props = len(expected_properties)
        if total_props == 0:
            return 0.0, ["No hay propiedades esperadas definidas en el esquema"]

        matched_props = 0

        # Verificar campos requeridos
        for field in required_fields:
            if field not in datalayer:
                errors.append(f"Campo requerido '{field}' no encontrado")
            elif field in expected_properties:
                # Si el campo es requerido y está en las propiedades esperadas,
                # verificar si coincide o es dinámico
                expected_value = expected_properties[field]

                # Si es un valor dinámico (null o con {{...}}), considerarlo una coincidencia
                is_dynamic = expected_value is None or (
                    isinstance(expected_value, str)
                    and "{{" in expected_value
                    and "}}" in expected_value
                )

                if is_dynamic:
                    matched_props += 1
                # Para strings, normalizar la comparación
                elif isinstance(expected_value, str) and isinstance(
                    datalayer[field], str
                ):
                    # Normalizar caracteres unicode para la comparación
                    norm_expected = self._normalize_string(expected_value)
                    norm_actual = self._normalize_string(datalayer[field])

                    if norm_expected == norm_actual:
                        matched_props += 1
                    else:
                        # Intentar una comparación menos estricta para caracteres especiales
                        # Eliminar caracteres de escape y normalizar
                        clean_expected = self._clean_string(expected_value)
                        clean_actual = self._clean_string(datalayer[field])

                        if clean_expected == clean_actual:
                            matched_props += 1
                        else:
                            errors.append(
                                f"Valor para '{field}' no coincide: esperado '{expected_value}', encontrado '{datalayer[field]}'"
                            )
                elif datalayer[field] == expected_value:
                    matched_props += 1
                else:
                    errors.append(
                        f"Valor para '{field}' no coincide: esperado '{expected_value}', encontrado '{datalayer[field]}'"
                    )

        # Verificar otras propiedades
        for prop, expected_value in expected_properties.items():
            if (
                prop in datalayer and prop not in required_fields
            ):  # Evitar contar dos veces los requeridos
                # Si es un valor dinámico (null o con {{...}}), considerarlo una coincidencia
                is_dynamic = expected_value is None or (
                    isinstance(expected_value, str)
                    and "{{" in expected_value
                    and "}}" in expected_value
                )

                if is_dynamic:
                    matched_props += 1
                # Para strings, normalizar la comparación
                elif isinstance(expected_value, str) and isinstance(
                    datalayer[prop], str
                ):
                    # Normalizar caracteres unicode para la comparación
                    norm_expected = self._normalize_string(expected_value)
                    norm_actual = self._normalize_string(datalayer[prop])

                    if norm_expected == norm_actual:
                        matched_props += 1
                    else:
                        # Intentar una comparación menos estricta para caracteres especiales
                        clean_expected = self._clean_string(expected_value)
                        clean_actual = self._clean_string(datalayer[prop])

                        if clean_expected == clean_actual:
                            matched_props += 1
                        else:
                            errors.append(
                                f"Valor para '{prop}' no coincide: esperado '{expected_value}', encontrado '{datalayer[prop]}'"
                            )
                elif datalayer[prop] == expected_value:
                    matched_props += 1
                else:
                    errors.append(
                        f"Valor para '{prop}' no coincide: esperado '{expected_value}', encontrado '{datalayer[prop]}'"
                    )

        # Calcular puntuación
        score = matched_props / total_props if total_props > 0 else 0
        return score, errors

    def _sort_reference_properties(self, captured_datalayer: Dict[str, Any], reference_properties: Dict[str, Any]) -> Dict[str, Any]:
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
     if not isinstance(captured_datalayer, dict) or not isinstance(reference_properties, dict):
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

    def _compare_with_reference(self, captured_datalayers: List[Dict[str, Any]]) -> Dict[str, Any]:
     """
     Compara los DataLayers capturados con los DataLayers de referencia en el esquema.

     Args:
        captured_datalayers: Lista de DataLayers capturados

     Returns:
        Diccionario con información de comparación
     """
     comparison_results = {
        "reference_count": 0,         # Cantidad de DataLayers en el archivo de referencia
        "captured_count": len(captured_datalayers),  # Cantidad de DataLayers capturados
        "matched_count": 0,           # DataLayers que coinciden entre referencia y capturados
        "missing_count": 0,           # DataLayers de referencia que no se encontraron
        "extra_count": 0,             # DataLayers capturados que no estaban en la referencia
        "match_details": [],          # Detalles de las coincidencias encontradas
        "missing_details": [],        # Detalles de DataLayers faltantes
        "extra_details": []           # Detalles de DataLayers extra
     }

    # Obtener lista de DataLayers de referencia (propiedades de cada sección)
     reference_datalayers = []
     for section in self.schema.get("sections", []):
        datalayer_props = section.get("datalayer", {}).get("properties", {})
        if datalayer_props:
            reference_datalayers.append({
                "properties": datalayer_props,
                "title": section.get("title", "Unknown Section"),
                "id": section.get("id", "unknown_id"),
                "match_found": False  # Flag para saber si se encontró coincidencia
            })

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
                if key in ["event", "event_category", "event_action", "event_label"]:
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
            sorted_reference_properties = self._sort_reference_properties(datalayer, best_match["properties"])

            # Guardar detalles de la coincidencia
            comparison_results["match_details"].append({
                "datalayer_index": i,
                "reference_title": best_match["title"],
                "reference_id": best_match["id"],
                "match_score": best_match_score,
                "data": datalayer,
                "reference_data": sorted_reference_properties  # Guardar propiedades de referencia ordenadas
            })

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
            comparison_results["extra_details"].append({
                "datalayer_index": i,
                "data": datalayer
            })

     # Buscar DataLayers de referencia que no se encontraron
     for ref_dl in reference_datalayers:
        if not ref_dl["match_found"]:
            comparison_results["missing_count"] += 1
            comparison_results["missing_details"].append({
                "reference_title": ref_dl["title"],
                "reference_id": ref_dl["id"],
                "properties": ref_dl["properties"]
            })

     # Calcular métricas adicionales
     if comparison_results["reference_count"] > 0:
        comparison_results["coverage_percent"] = round(
            (comparison_results["matched_count"] / comparison_results["reference_count"]) * 100, 1
        )
     else:
        comparison_results["coverage_percent"] = 0

     return comparison_results

    def _filter_datalayers(self, captured_datalayers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
     """
     Filtra los DataLayers capturados para eliminar los que no son relevantes para la validación.

     Args:
        captured_datalayers: Lista de DataLayers capturados

     Returns:
        Lista filtrada de DataLayers relevantes
     """
     # Lista de eventos que no son relevantes para la validación
     excluded_events = ["gtm.js", "gtm.dom", "gtm.load", "gtm.click", "gtm.scrollDepth", "gtm.historyChange"]

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

     logger.info(f"DataLayers filtrados: {len(captured_datalayers)} capturados, {len(filtered_datalayers)} relevantes")

     # Si después del filtrado no quedan DataLayers, devolver los originales
     if not filtered_datalayers and captured_datalayers:
        logger.warning("El filtrado eliminó todos los DataLayers. Devolviendo los originales.")
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
            if '\\u' in text:
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
        cleaned = ''.join(c for c in cleaned if c.isalnum() or c.isspace())
        cleaned = " ".join(cleaned.split())  # Normalizar espacios

        return cleaned

    def interactive_validation(self) -> Dict[str, Any]:

     try:
        # Configurar el driver en modo visible (no headless)
        original_headless = self.headless
        self.headless = False
        self.setup_driver()

        # Navegar a la URL inicial
        logger.info(f"Navegando a URL inicial: {self.url}")
        self.driver.get(self.url)

        # Esperar a que la página cargue inicialmente
        WebDriverWait(self.driver, 10).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )

        # Inyectar script simplificado para monitorear DataLayers
        monitor_script = """
        // Crear un arreglo para almacenar todos los DataLayers
        window.allDataLayers = [];

        // Capturar cualquier DataLayer existente
        if (typeof window.dataLayer !== 'undefined') {
            if (Array.isArray(window.dataLayer)) {
                for (var i = 0; i < window.dataLayer.length; i++) {
                    try {
                        // No necesitamos JSON.parse(JSON.stringify()) aquí,
                        // solo guardar una copia del objeto
                        window.allDataLayers.push(Object.assign({}, window.dataLayer[i]));
                    } catch (e) {
                        console.error('Error al copiar dataLayer existente:', e);
                    }
                }
                console.log('Captured ' + window.dataLayer.length + ' existing dataLayer items');
            }
        } else {
            // Si no existe, crearlo
            window.dataLayer = [];
            console.log('Created new dataLayer');
        }

        // Guardar referencia al método push original
        var originalPush = Array.prototype.push;

        // Sobreescribir el método push del arreglo dataLayer
        window.dataLayer.push = function() {
            // Capturar cada objeto que se añade a dataLayer
            for (var i = 0; i < arguments.length; i++) {
                var obj = arguments[i];
                try {
                    // Crear una copia simple del objeto sin serializar/deserializar
                    var copy = Object.assign({}, obj);
                    window.allDataLayers.push(copy);
                    console.log('DataLayer captured:', copy);
                } catch (e) {
                    console.error('Error capturing dataLayer:', e);
                }
            }

            // Llamar al método push original
            return originalPush.apply(this, arguments);
        };

        console.log('DataLayer monitoring initialized. Total captures:', window.allDataLayers.length);

        // Agregar un mecanismo para inspeccionar desde la consola
        window.inspectDataLayers = function() {
            console.table(window.allDataLayers);
            return window.allDataLayers;
        };
        """

        # Ejecutar el script simplificado
        self.driver.execute_script(monitor_script)

        print("\n=== MODO INTERACTIVO DE VALIDACIÓN ===")
        print(f"Se ha abierto el navegador para validar: {self.url}")
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

        # Esperar un momento para asegurar que todos los DataLayers se hayan procesado
        time.sleep(1)

        # Capturar DataLayers usando métodos más simples
        try:
            # Primero intentamos obtener desde window.allDataLayers
            captured_datalayers = self.driver.execute_script(
                "return window.allDataLayers || [];"
            )
            logger.info(
                f"Capturados {len(captured_datalayers)} DataLayers desde window.allDataLayers"
            )

            # Si no hay resultados, intentamos con otra estrategia
            if not captured_datalayers or len(captured_datalayers) == 0:
                # Intentar acceder directamente a window.dataLayer
                direct_layers = self.driver.execute_script(
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
            logger.error(f"Error al capturar DataLayers: {e}")
            captured_datalayers = []

        logger.info(
            f"Se han capturado {len(captured_datalayers)} DataLayers en modo interactivo."
        )

        # Filtrar los DataLayers que no son relevantes para la validación
        filtered_datalayers = self._filter_datalayers(captured_datalayers)
        logger.info(f"DataLayers relevantes para validación: {len(filtered_datalayers)}")

        if not filtered_datalayers:
            # Si no hay DataLayers relevantes después del filtrado
            self.validation_results["valid"] = False
            self.validation_results["errors"].append(
                "No se encontraron DataLayers relevantes para validación"
            )
            print("\n⚠️ ADVERTENCIA: No se detectaron DataLayers relevantes para validación.")
            print("Los DataLayers capturados son del sistema GTM o no coinciden con la estructura esperada.")
            return self.validation_results

        print(f"\nSe capturaron {len(captured_datalayers)} DataLayers.")
        print(f"De los cuales {len(filtered_datalayers)} son relevantes para validación.")

        # Usar filtered_datalayers en lugar de captured_datalayers en el resto del método
        captured_datalayers = filtered_datalayers

        if not captured_datalayers or len(captured_datalayers) == 0:
            self.validation_results["valid"] = False
            self.validation_results["errors"].append(
                "No se encontraron DataLayers durante la navegación interactiva"
            )
            print(
                "\n⚠️ ADVERTENCIA: No se detectaron DataLayers. Prueba con estos consejos:"
            )
            print(
                "1. Abre la consola del navegador (F12) y verifica si hay DataLayers usando:"
            )
            print("   console.log(window.dataLayer)")
            print(
                "2. Asegúrate de interactuar con elementos que disparen eventos (botones, formularios)"
            )
            print(
                "3. Verifica si el sitio usa alguna otra estructura de datos para analytics"
            )
            return self.validation_results

        # Mostrar un ejemplo del primer DataLayer capturado
        if captured_datalayers and len(captured_datalayers) > 0:
            print("\nEjemplo del primer DataLayer capturado:")
            try:
                first_dl = captured_datalayers[0]
                pretty_json = json.dumps(first_dl, indent=2, ensure_ascii=False)
                print(pretty_json)
            except Exception as e:
                print(f"[Error al mostrar el ejemplo: {str(e)}]")

        # Actualizar las estadísticas
        self.validation_results["summary"]["total_sections"] = len(
            self.schema.get("sections", [])
        )
        self.validation_results["summary"]["not_found_sections"] = (
            self.validation_results["summary"]["total_sections"]
        )

        # Procesar cada DataLayer capturado
        for i, datalayer in enumerate(captured_datalayers):
            # Intentar encontrar coincidencias con secciones del esquema
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

                # Calcular puntuación de coincidencia
                score, errors = self._calculate_match_score(
                    datalayer, expected_properties, required_fields
                )

                # Si es el mejor match hasta ahora
                if score > best_match_score:
                    best_match_score = score
                    best_match_section = section
                    matched_errors = errors

            # Determinar si es una coincidencia válida
            match_threshold = self.config.get("validation", {}).get(
                "match_threshold", 0.7
            )
            is_valid_match = best_match_score >= match_threshold

            # Actualizar estadísticas si encontramos una coincidencia
            if is_valid_match and best_match_section:
                # Decrementar secciones no encontradas
                self.validation_results["summary"]["not_found_sections"] -= 1

                if len(matched_errors) == 0:
                    self.validation_results["summary"]["valid_sections"] += 1
                else:
                    self.validation_results["summary"]["invalid_sections"] += 1

            # Guardar detalles de este DataLayer
            detail = {
                "datalayer_index": i,
                "data": datalayer,
                "valid": is_valid_match and len(matched_errors) == 0,
                "errors": matched_errors,
                "source": "interactive",
            }

            # Si tenemos una coincidencia, agregar información de la sección
            if best_match_section:
                detail["matched_section"] = best_match_section.get(
                    "title", "Unknown Section"
                )
                detail["match_score"] = best_match_score

            self.validation_results["details"].append(detail)

            # Mostrar progreso
            if i % 10 == 0 and i > 0:
                print(f"Procesados {i} de {len(captured_datalayers)} DataLayers...")

        # Actualizar estado global
        self.validation_results["valid"] = (
            self.validation_results["summary"]["valid_sections"] > 0
        )

        if self.validation_results["summary"]["valid_sections"] > 0:
            print(
                f"\nValidación completada: {self.validation_results['summary']['valid_sections']} de {self.validation_results['summary']['total_sections']} secciones válidas"
            )
        else:
            print("\nNo se encontraron secciones válidas durante la validación")

        # Realizar comparación con DataLayers de referencia
        logger.info("Comparando DataLayers capturados con la referencia...")
        comparison_results = self._compare_with_reference(captured_datalayers)
        self.validation_results["comparison"] = comparison_results

        # Mostrar resumen de la comparación
        print("\n=== Comparación con DataLayers de Referencia ===")
        print(f"DataLayers en archivo de referencia: {comparison_results['reference_count']}")
        print(f"DataLayers capturados: {comparison_results['captured_count']}")
        print(f"Coincidencias encontradas: {comparison_results['matched_count']}")
        print(f"DataLayers de referencia no encontrados: {comparison_results['missing_count']}")
        print(f"DataLayers capturados extra: {comparison_results['extra_count']}")
        print(f"Cobertura: {comparison_results['coverage_percent']}%")

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
        # Restaurar el valor original de headless
        self.headless = original_headless
        if self.driver:
            # No cerrar automáticamente si hay un error para permitir inspección manual
            try:
                if self.driver.session_id:
                    self.driver.quit()
                    logger.info("WebDriver cerrado correctamente")
            except:
                pass

    def validate_all_sections(self) -> Dict[str, Any]:
        """
        Valida todas las secciones del esquema.

        Returns:
            Resultados completos de la validación
        """
        try:
            # Configurar el driver
            self.setup_driver()

            # Navegar a la URL inicial
            logger.info(f"Navegando a URL inicial: {self.url}")
            self.driver.get(self.url)

            # Esperar a que la página cargue
            wait = WebDriverWait(
                self.driver, self.config.get("browser", {}).get("wait_timeout", 10)
            )
            wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))

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
            if self.driver:
                self.driver.quit()
                logger.info("WebDriver cerrado correctamente")


    def get_results(self) -> Dict[str, Any]:
        """
        Obtiene los resultados de la validación.

        Returns:
            Resultados de la validación
        """
        return self.validation_results
