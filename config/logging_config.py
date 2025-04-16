#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import logging.config
from datetime import datetime


def configure_logging(log_dir="logs"):
    """Configura el sistema de logging para la aplicación"""
    # Asegurar que existe el directorio de logs
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    # Nombre de archivo con timestamp
    log_filename = f"validation_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    log_path = os.path.join(log_dir, log_filename)

    # Configuración
    logging_config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "standard": {
                "format": "%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S",
            },
            "simple": {"format": "%(levelname)s: %(message)s"},
        },
        "handlers": {
            "file": {
                "level": "INFO",
                "class": "logging.FileHandler",
                "filename": log_path,
                "formatter": "standard",
                "encoding": "utf-8",
            },
            "console": {
                "level": "INFO",
                "class": "logging.StreamHandler",
                "formatter": "simple",
                "stream": "ext://sys.stdout",
            },
        },
        "loggers": {
            "": {  # Root logger
                "handlers": ["file", "console"],
                "level": "INFO",
                "propagate": True,
            },
            "selenium": {
                "level": "WARNING",
            },
            "urllib3": {
                "level": "WARNING",
            },
            "webdriver_manager": {
                "level": "WARNING",
            },
        },
    }

    # Aplicar la configuración
    logging.config.dictConfig(logging_config)

    return log_path
