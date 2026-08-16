"""2 soniyadan uzoq HTTP so‘rovlarni log qilish."""
from __future__ import annotations

import logging
import time

logger = logging.getLogger("tezpos.slow")


class SlowRequestMiddleware:
    threshold = 2.0

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        t0 = time.time()
        response = self.get_response(request)
        dt = time.time() - t0
        if dt >= self.threshold:
            path = getattr(request, "path", "") or ""
            logger.warning("[SLOW] %s %s took %.2fs status=%s", request.method, path, dt, getattr(response, "status_code", "?"))
        return response
