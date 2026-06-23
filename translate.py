"""Offline multi-language translation via argos-translate. Translates a spoken
phrase into every configured language, pivoting through English when there's no
direct package (e.g. de->ja becomes de->en->ja). All functions degrade gracefully:
if argos or a language package is unavailable, the original text is returned
unchanged rather than raising."""
import threading

_lock = threading.Lock()
_installed = set()   # (from_code, to_code) pairs we've confirmed installed
_available = True    # flips to False if argos can't be imported/initialized


def _needed_pairs(langs):
    """The argos packages required to translate among `langs`. English is the pivot
    hub, so we install en<->each non-English language; argos finds any other path
    (e.g. de<->ja) by routing through English."""
    need = set()
    for lang in langs:
        if lang and lang != "en":
            need.add(("en", lang))
            need.add((lang, "en"))
    return need


def _ensure(langs):
    """Lazily import argos and install whatever packages are needed to translate
    among `langs`. Returns True when translation is usable. Safe to call from
    multiple threads and cheap to call repeatedly (already-installed pairs are
    skipped)."""
    global _available
    if not _available:
        return False
    need = _needed_pairs(langs) - _installed
    if not need:
        return True
    with _lock:
        need = _needed_pairs(langs) - _installed
        if not need:
            return True
        try:
            import argostranslate.package as pkg
            import argostranslate.translate  # noqa: F401  (validate it imports)
            _installed.update((p.from_code, p.to_code) for p in pkg.get_installed_packages())
            need -= _installed
            if need:
                pkg.update_package_index()
                for p in pkg.get_available_packages():
                    if (p.from_code, p.to_code) in need:
                        pkg.install_from_path(p.download())
                        _installed.add((p.from_code, p.to_code))
            return True
        except Exception:
            _available = False
            return False


def warmup(langs):
    """Pre-install packages for `langs` in the background so the first phrase isn't
    delayed."""
    threading.Thread(target=_ensure, args=(list(langs),), daemon=True).start()


def translate(text, from_code, to_code):
    """Translate text between two language codes; returns text unchanged on any
    failure. argos auto-pivots through English when no direct package exists."""
    if from_code == to_code or not (text or "").strip():
        return text
    if not _ensure([from_code, to_code]):
        return text
    try:
        import argostranslate.translate as tr
        return tr.translate(text, from_code, to_code)
    except Exception:
        return text


def to_languages(text, detected_lang, langs):
    """Translate `text` (spoken in `detected_lang`) into each code in `langs`,
    preserving order. Returns a list of (code, translated_text). The line whose
    code matches the spoken language keeps the original transcript verbatim."""
    text = (text or "").strip()
    if not text:
        return [(c, "") for c in langs]
    src = (detected_lang or (langs[0] if langs else "en")).lower()
    _ensure(list(langs) + [src])
    return [(c, text if c == src else translate(text, src, c)) for c in langs]
