"""
Lightweight observability for the converter microservice.

One dedicated logger ("praj.converter") with its own handler so our structured lines are
distinct from uvicorn's access log and never double-print. Every log line carries a
`rid=` correlation id; PRAJ_IQ sends an `X-Request-ID` header so one onboarding can be
traced across both services, and the middleware echoes it back on the response.
"""
from __future__ import annotations

import logging
import os
import sys

_LEVEL = os.getenv("CONVERTER_LOG_LEVEL", "INFO").upper()


def get_logger() -> logging.Logger:
    log = logging.getLogger("praj.converter")
    if not log.handlers:
        h = logging.StreamHandler(sys.stdout)
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [converter] %(message)s",
                                         datefmt="%H:%M:%S"))
        log.addHandler(h)
        log.setLevel(getattr(logging, _LEVEL, logging.INFO))
        log.propagate = False      # don't bubble into uvicorn/root (avoids double lines)
    return log


logger = get_logger()
