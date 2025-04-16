#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import logging
from typing import List, Dict, Any

logger = logging.getLogger(__name__)


class SchemaBuilder:
    """Construye un esquema de validación a partir de los DataLayers de referencia"""

    def __init__(self, reference_datalayers: List[Dict[str, Any]]):
        """
        Inicializa el constructor de esquemas

        Args:
            reference_datalayers: Lista de DataLayers de referencia cargados del JSON
        """
        self.reference_datalayers = reference_datalayers

    def build_schema(self) -> Dict[str, Any]:
        """
        Construye el esquema de validación para todos los datalayers

        Returns:
            Esquema de validación estructurado
        """
        logger.info("Construyendo esquema de validación")

        schema = {
            "metadata": {
                "total_sections": len(self.reference_datalayers),
                "generation_time": "",  # Se llenará en el reporte
            },
            "global_patterns": {
                "component_name": r"{{component_name}}",
                "element_text": r"{{element_name}}",
                "user_type": "null",
            },
            "sections": [],
        }

        # Procesar cada DataLayer de referencia
        for i, datalayer in enumerate(self.reference_datalayers):
            section_schema = self._build_section_schema(i, datalayer)
            if section_schema:
                schema["sections"].append(section_schema)

        logger.info(f"Esquema construido con {len(schema['sections'])} secciones")
        return schema

    def _build_section_schema(
        self, index: int, datalayer: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Construye el esquema para una sección específica

        Args:
            index: Índice del DataLayer
            datalayer: DataLayer de referencia

        Returns:
            Esquema para la sección
        """
        try:
            # Construir un identificador único para la sección
            # Intentar usar combinaciones de event_category y event_label si están disponibles
            section_title = f"{datalayer.get('event_category', 'Unknown')} - {datalayer.get('event_label', 'Unknown')}"

            # Identificar campos dinámicos
            dynamic_fields = self._identify_dynamic_fields(datalayer)

            # Identificar campos requeridos
            required_fields = self._identify_required_fields(datalayer)

            # Construir el esquema de la sección
            section_schema = {
                "title": section_title,
                "id": f"datalayer_{index}",
                "datalayer": {
                    "properties": datalayer,  # Usar el DataLayer completo como propiedades esperadas
                    "required_fields": required_fields,
                    "dynamic_fields": dynamic_fields,
                },
                "activation": {
                    "condition": self._extract_activation_condition(datalayer),
                    "type": self._determine_activation_type(datalayer),
                },
            }

            return section_schema

        except Exception as e:
            logger.error(
                f"Error al construir esquema para DataLayer {index}: {e}", exc_info=True
            )
            return None

    def _identify_dynamic_fields(self, datalayer: Dict[str, Any]) -> Dict[str, str]:
        """
        Identifica campos con valores dinámicos según los criterios especificados:
        - Si un campo tiene un valor con formato {{...}}
        - Si un campo tiene valor null

        Args:
            datalayer: DataLayer de referencia

        Returns:
            Diccionario de campos dinámicos con sus patrones
        """
        dynamic_fields = {}

        for key, value in datalayer.items():
            # Criterio 1: valor es null
            if value is None:
                dynamic_fields[key] = "null"
                continue

            # Criterio 2: valor tiene formato {{...}}
            if isinstance(value, str) and "{{" in value and "}}" in value:
                dynamic_fields[key] = value

        return dynamic_fields

    def _identify_required_fields(self, datalayer: Dict[str, Any]) -> List[str]:
        """
        Identifica campos requeridos basados en las propiedades del DataLayer

        Args:
            datalayer: DataLayer de referencia

        Returns:
            Lista de campos requeridos
        """
        # Por defecto, estos campos son siempre requeridos en DataLayers de GA
        required = ["event"]

        # Campos comunes que suelen ser requeridos en GA
        common_required = ["event_category", "event_action", "event_label"]

        # Agregar campos comunes si están presentes y no son dinámicos
        for field in common_required:
            if field in datalayer:
                required.append(field)

        return required

    def _extract_activation_condition(self, datalayer: Dict[str, Any]) -> str:
        """
        Extrae una descripción de la condición de activación basada en los datos del DataLayer

        Args:
            datalayer: DataLayer de referencia

        Returns:
            Descripción de la condición de activación
        """
        # Si hay un event_label que indica acción
        event_label = datalayer.get("event_label", "")
        event_category = datalayer.get("event_category", "")
        event_action = datalayer.get("event_action", "")

        if event_action in ["Interaction", "Click", "Submit"]:
            return f"Cuando el usuario interactúa con {event_label} en la sección {event_category}"
        elif event_action in ["View", "Content", "Load"]:
            return f"Cuando el usuario ve {event_label} en la sección {event_category}"
        else:
            return f"Cuando se activa {event_label} en {event_category}"

    def _determine_activation_type(self, datalayer: Dict[str, Any]) -> str:
        """
        Determina el tipo de activación basado en los datos del DataLayer

        Args:
            datalayer: DataLayer de referencia

        Returns:
            Tipo de activación (click, view, load, etc.)
        """
        event_action = datalayer.get("event_action", "").lower()
        interaction = str(datalayer.get("interaction", "")).lower()

        if event_action in ["click", "interaction"] or interaction == "yes":
            return "click"
        elif event_action in ["view", "impression", "content"]:
            return "view"
        elif event_action in ["load", "pageview"]:
            return "load"
        elif event_action in ["scroll"]:
            return "scroll"
        elif event_action in ["hover", "mouse"]:
            return "hover"
        elif event_action in ["submit", "form_submit"]:
            return "submit"
        else:
            return "custom"
