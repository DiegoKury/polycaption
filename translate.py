"""Offline multi-language translation via argos-translate. Translates a spoken
phrase into every configured language, pivoting through English when there's no
direct package (e.g. de->ja becomes de->en->ja).

Crucially, translation NEVER blocks: package downloads happen on background
threads only. If a language pair isn't installed yet, the original text is
returned unchanged and a background install is kicked off, so the capture/UI
threads are never frozen waiting on the network. Everything also degrades
gracefully if argos itself is unavailable."""
import logging
import threading

# stanza (argos's sentence splitter) logs a harmless WARNING every time it builds a
# pipeline ("Language en package default expects mwt, which has been added"). It resets
# its own logger level on init, so setLevel() doesn't stick — a filter does: drop every
# stanza record below ERROR. Survives stanza re-configuring the logger.
logging.getLogger('stanza').addFilter(lambda r: r.levelno >= logging.ERROR)

_lock = threading.Lock()
_installed = set()        # (from_code, to_code) pairs confirmed installed on disk
_scanned = False          # have we read the installed package list at least once?
_available = True         # flips False if argos can't be imported at all
_attempted = set()        # language-sets we've already started a background install for


def _scan_installed():
    """Populate _installed from argos's on-disk packages (once). Cheap, no network."""
    global _scanned, _available
    if _scanned or not _available:
        return
    try:
        import argostranslate.package as pkg
        import argostranslate.translate  # noqa: F401  (validate it imports)
        _installed.update((p.from_code, p.to_code) for p in pkg.get_installed_packages())
        _scanned = True
    except Exception:
        _available = False


def _needed_pairs(langs):
    """The en<->lang packages required to translate among `langs`. English is the
    pivot hub, so any other pair (e.g. de<->ja) is reachable by routing through it."""
    need = set()
    for lang in langs:
        if lang and lang != "en":
            need.add(("en", lang))
            need.add((lang, "en"))
    return need


def _pair_ready(from_code, to_code):
    """True if `from_code`->`to_code` can be translated right now with installed
    packages (directly or by pivoting through English). No network."""
    if from_code == to_code:
        return True
    _scan_installed()
    if not _available:
        return False
    need = set()
    if from_code != "en":
        need.add((from_code, "en"))
    if to_code != "en":
        need.add(("en", to_code))
    return need <= _installed


def _install(langs):
    """Download+install whatever packages are needed for `langs` (pivoting through
    English). Runs on a background thread — may hit the network. Updates _installed."""
    global _available
    if not _available:
        return
    need = _needed_pairs(langs) - _installed
    if not need:
        return
    with _lock:
        need = _needed_pairs(langs) - _installed
        if not need:
            return
        try:
            import argostranslate.package as pkg
            import argostranslate.translate  # noqa: F401
            _installed.update((p.from_code, p.to_code) for p in pkg.get_installed_packages())
            need -= _installed
            if need:
                pkg.update_package_index()
                for p in pkg.get_available_packages():
                    if (p.from_code, p.to_code) in need:
                        pkg.install_from_path(p.download())
                        _installed.add((p.from_code, p.to_code))
        except Exception:
            _available = False


def _install_bg(langs):
    """Start a background install for `langs`, at most once per distinct set."""
    key = frozenset(langs)
    if key in _attempted:
        return
    _attempted.add(key)
    threading.Thread(target=_install, args=(list(langs),), daemon=True).start()


def warmup(langs):
    """Pre-install packages for `langs` in the background so the first phrase isn't
    delayed (best-effort; translation still works incrementally as packages land)."""
    _install_bg(langs)


def language_availability():
    """Inspect the live argos package index and return (target_langs, source_langs):
    the codes argos can translate *to* from English (en->xx, i.e. displayable) and
    *from* into English (xx->en, i.e. speakable). English is implicitly in both.
    Returns (None, None) when argos is unavailable (not installed, or offline with no
    cached index) so callers can skip validation. May hit the network — call off the
    hot path."""
    if not _available:
        return None, None
    try:
        import argostranslate.package as pkg
        try:
            pkg.update_package_index()
        except Exception:
            pass  # offline: fall back to whatever's already cached/installed
        pairs = {(p.from_code, p.to_code)
                 for p in list(pkg.get_available_packages()) + list(pkg.get_installed_packages())}
    except Exception:
        return None, None
    if not pairs:
        return None, None
    targets = {to for frm, to in pairs if frm == "en"} | {"en"}
    sources = {frm for frm, to in pairs if to == "en"} | {"en"}
    return targets, sources


def translate(text, from_code, to_code):
    """Translate text between two language codes. Returns text unchanged (and starts a
    one-time background install) if the needed packages aren't ready yet, so this never
    blocks on the network. argos auto-pivots through English when packages allow."""
    if from_code == to_code or not (text or "").strip():
        return text
    if not _pair_ready(from_code, to_code):
        _install_bg([from_code, to_code])
        return text
    try:
        import argostranslate.translate as tr
        return tr.translate(text, from_code, to_code)
    except Exception:
        return text


def to_languages(text, detected_lang, langs):
    """Translate `text` (spoken in `detected_lang`) into each code in `langs`,
    preserving order. Returns a list of (code, translated_text). The line whose code
    matches the spoken language keeps the original transcript verbatim; any line whose
    packages aren't installed yet also shows the original until the background install
    lands."""
    text = (text or "").strip()
    if not text:
        return [(c, "") for c in langs]
    src = (detected_lang or (langs[0] if langs else "en")).lower()
    return [(c, text if c == src else translate(text, src, c)) for c in langs]
