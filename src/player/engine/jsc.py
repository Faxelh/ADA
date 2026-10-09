"""
Moteur JavaScript pour YouTube sur iPhone.

YouTube chiffre l'adresse de ses vidéos avec du JavaScript (« défis » n et
signature). Sur ordinateur, yt-dlp lance Deno ou Node. Sur iPhone on ne
peut pas lancer de programme : ADA utilise donc JavaScriptCore, le moteur
JavaScript de Safari intégré à iOS, et le branche dans yt-dlp comme un
fournisseur de défis (« JS challenge provider »).

install() est sans effet hors d'iOS/macOS (Android, tests sur PC).
"""

import logging
import threading

log = logging.getLogger("player.jsc")

RUNTIME_NAME = "javascriptcore"

_installed = None
_lock = threading.Lock()
CALLS = 0  # nombre de scripts exécutés (diagnostic et tests)
_RUNTIME_REGISTERED = False

# Le script de yt-dlp affiche son résultat avec console.log : on fournit un
# console minimal qui accumule les lignes, relues ensuite.
_PRELUDE = """
var __player_out = [];
var console = {
  log: function () { __player_out.push(Array.prototype.map.call(arguments, String).join(' ')); },
  error: function () {}, warn: function () {}, info: function () {}, debug: function () {}
};
"""


class JavaScriptError(RuntimeError):
    pass


def _native():
    """Classe JSContext, ou None si JavaScriptCore n'est pas disponible."""
    try:
        from rubicon.objc import ObjCClass
        from rubicon.objc.runtime import load_library
    except Exception:
        return None
    try:
        load_library("JavaScriptCore")
        return ObjCClass("JSContext")
    except Exception:
        return None


def available():
    return _native() is not None


def run_script(script):
    """Exécute un script JavaScript ; renvoie tout ce qu'il a écrit avec console.log."""
    JSContext = _native()
    if JSContext is None:
        raise JavaScriptError("JavaScriptCore indisponible sur cet appareil.")
    try:
        from rubicon.objc.runtime import autoreleasepool
    except Exception:  # anciennes versions de rubicon
        autoreleasepool = None

    def execute():
        context = JSContext.alloc().init()
        context.evaluateScript(_PRELUDE)
        context.evaluateScript(script)
        exception = context.exception
        if exception is not None:
            message = str(exception.toString())
            context.exception = None
            raise JavaScriptError(f"Erreur JavaScript : {message}")
        output = context.evaluateScript("__player_out.join('\\n')")
        return str(output.toString()) if output is not None else ""

    if autoreleasepool is not None:
        with autoreleasepool():
            return execute()
    return execute()


def _version():
    try:
        from rubicon.objc import ObjCClass
        info = ObjCClass("NSProcessInfo").processInfo
        version = info.operatingSystemVersion
        return f"{version.field_0}.{version.field_1}.{version.field_2}"
    except Exception:
        return "1.0"


def install(runner=None):
    """
    Déclare JavaScriptCore auprès de yt-dlp. runner : fonction de test qui
    remplace run_script. Renvoie True si le fournisseur est actif.
    """
    global _installed
    with _lock:
        if _installed is not None:
            return _installed
        if runner is None and not available():
            _installed = False
            return False
        try:
            _installed = _register(runner or run_script)
        except Exception:
            log.exception("Impossible de brancher JavaScriptCore dans yt-dlp")
            _installed = False
        return _installed


def _register(runner):
    from yt_dlp.extractor.youtube.jsc._builtin.ejs import EJSBaseJCP
    from yt_dlp.extractor.youtube.jsc.provider import (
        JsChallengeProviderError,
        register_preference,
        register_provider,
    )

    try:
        from yt_dlp.utils._jsruntime import JsRuntimeInfo
        info = JsRuntimeInfo(name=RUNTIME_NAME, path=RUNTIME_NAME, version=_version(),
                             version_tuple=(1, 0, 0), supported=True)
    except Exception:
        class _Info:
            name = RUNTIME_NAME
            path = RUNTIME_NAME
            version = "1.0"
            version_tuple = (1, 0, 0)
            supported = True
        info = _Info()

    class JavaScriptCoreJCP(EJSBaseJCP):
        PROVIDER_NAME = RUNTIME_NAME
        JS_RUNTIME_NAME = RUNTIME_NAME
        PROVIDER_VERSION = "1.0.0"
        BUG_REPORT_LOCATION = "ADA"

        @property
        def runtime_info(self):
            return info

        def _run_js_runtime(self, stdin, /):
            global CALLS
            CALLS += 1
            try:
                return runner(stdin)
            except JavaScriptError as error:
                raise JsChallengeProviderError(str(error)) from error

    register_provider(JavaScriptCoreJCP)

    # Déclare aussi JavaScriptCore comme « moteur JavaScript » de yt-dlp
    # (option js_runtimes) : l'extracteur YouTube choisit alors les clients
    # qui donnent toutes les qualités au lieu des clients « sans JavaScript ».
    try:
        from yt_dlp.globals import supported_js_runtimes
        from yt_dlp.utils._jsruntime import JsRuntime

        class JavaScriptCoreJsRuntime(JsRuntime):
            def _info(self):
                return info

        supported_js_runtimes.value[RUNTIME_NAME] = JavaScriptCoreJsRuntime
        global _RUNTIME_REGISTERED
        _RUNTIME_REGISTERED = True
    except Exception:
        log.info("Registre des moteurs JavaScript de yt-dlp indisponible (version ancienne ?)")

    @register_preference(JavaScriptCoreJCP)
    def _preference(provider, requests):
        return 1000

    log.info("JavaScriptCore branché dans yt-dlp pour YouTube")
    return True


def ytdlp_options():
    """Options yt-dlp à ajouter quand JavaScriptCore est branché."""
    if install() and _RUNTIME_REGISTERED:
        return {"js_runtimes": {RUNTIME_NAME: {}}}
    return {}
