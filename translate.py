"""Offline EN<->ES translation via argos-translate, used by language mode so it can
render both languages locally without any Claude request. All functions degrade
gracefully: if argos or its language packages are unavailable, the original text is
returned unchanged rather than raising."""
import threading

_lock = threading.Lock()
_ready = False
_available = True  # flips to False if argos can't be imported/initialized


def _ensure():
    """Lazily import argos and install the EN<->ES packages if missing. Returns True
    when translation is usable. Safe to call from multiple threads."""
    global _ready, _available
    if _ready:
        return True
    if not _available:
        return False
    with _lock:
        if _ready:
            return True
        try:
            import argostranslate.package as pkg
            import argostranslate.translate  # noqa: F401  (validate it imports)
            installed = {(p.from_code, p.to_code) for p in pkg.get_installed_packages()}
            need = {("en", "es"), ("es", "en")} - installed
            if need:
                pkg.update_package_index()
                for p in pkg.get_available_packages():
                    if (p.from_code, p.to_code) in need:
                        pkg.install_from_path(p.download())
            _ready = True
            return True
        except Exception:
            _available = False
            return False


def warmup():
    """Pre-load packages in the background so the first phrase isn't delayed."""
    threading.Thread(target=_ensure, daemon=True).start()


def translate(text, from_code, to_code):
    """Translate text between two language codes; returns text unchanged on any failure."""
    if from_code == to_code or not (text or "").strip():
        return text
    if not _ensure():
        return text
    try:
        import argostranslate.translate as tr
        return tr.translate(text, from_code, to_code)
    except Exception:
        return text


def to_en_es(text, lang):
    """Given a transcript and its detected language code, return (english, spanish)."""
    text = (text or "").strip()
    if not text:
        return "", ""
    lang = (lang or "en").lower()
    if lang == "es":
        return translate(text, "es", "en"), text
    if lang == "en":
        return text, translate(text, "en", "es")
    # Any other detected language: pivot through English.
    en = translate(text, lang, "en")
    return en, translate(en, "en", "es")
