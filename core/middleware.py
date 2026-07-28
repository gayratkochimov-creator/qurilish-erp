class NoHTMLCacheMiddleware:
    """HTML sahifalarni brauzer keshlamasin — o'zgarishlar darhol ko'rinsin.
    (Statik fayllar — CSS/JS/shrift — keshlanaveradi.)"""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        ct = response.get("Content-Type", "")
        if ct.startswith("text/html"):
            response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response["Pragma"] = "no-cache"
            response["Expires"] = "0"
        return response
