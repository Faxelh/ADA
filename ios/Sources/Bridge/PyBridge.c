#define PY_SSIZE_T_CLEAN
#if __has_include(<Python/Python.h>)
#include <Python/Python.h>
#else
#include <Python.h>
#endif
#include <stdlib.h>
#include <string.h>
#include "PyBridge.h"

static PyObject *dispatch_fn = NULL;
static int started = 0;

static char *copy_text(const char *text) {
    if (text == NULL) {
        text = "";
    }
    size_t size = strlen(text) + 1;
    char *out = malloc(size);
    if (out != NULL) {
        memcpy(out, text, size);
    }
    return out;
}

static char *status_text(PyStatus status, const char *where) {
    const char *message = status.err_msg ? status.err_msg : "erreur inconnue";
    const char *func = status.func ? status.func : "";
    size_t size = strlen(where) + strlen(message) + strlen(func) + 16;
    char *out = malloc(size);
    if (out != NULL) {
        snprintf(out, size, "%s : %s (%s)", where, message, func);
    }
    return out;
}

// Texte complet de l'exception Python en cours (avec la pile d'appels).
static char *exception_text(void) {
    PyObject *type = NULL, *value = NULL, *trace = NULL;
    PyErr_Fetch(&type, &value, &trace);
    PyErr_NormalizeException(&type, &value, &trace);
    char *out = NULL;
    PyObject *module = PyImport_ImportModule("traceback");
    if (module != NULL && type != NULL) {
        PyObject *lines = PyObject_CallMethod(module, "format_exception", "OOO", type,
                                              value ? value : Py_None, trace ? trace : Py_None);
        if (lines != NULL) {
            PyObject *empty = PyUnicode_FromString("");
            PyObject *joined = empty ? PyUnicode_Join(empty, lines) : NULL;
            if (joined != NULL) {
                out = copy_text(PyUnicode_AsUTF8(joined));
                Py_DECREF(joined);
            }
            Py_XDECREF(empty);
            Py_DECREF(lines);
        }
    }
    Py_XDECREF(module);
    if (out == NULL && value != NULL) {
        PyObject *text = PyObject_Str(value);
        if (text != NULL) {
            out = copy_text(PyUnicode_AsUTF8(text));
            Py_DECREF(text);
        }
    }
    PyErr_Clear();
    Py_XDECREF(type);
    Py_XDECREF(value);
    Py_XDECREF(trace);
    return out ? out : copy_text("erreur Python inconnue");
}

char *ada_python_start(const char *home, const char *paths) {
    if (started) {
        return NULL;
    }
    PyStatus status;
    PyPreConfig preconfig;
    PyPreConfig_InitIsolatedConfig(&preconfig);
    preconfig.utf8_mode = 1;
    preconfig.configure_locale = 1;
    status = Py_PreInitialize(&preconfig);
    if (PyStatus_Exception(status)) {
        return status_text(status, "Préinitialisation de Python");
    }

    PyConfig config;
    PyConfig_InitIsolatedConfig(&config);
    config.buffered_stdio = 0;
    config.write_bytecode = 0;
    config.install_signal_handlers = 1;
    config.module_search_paths_set = 1;

    status = PyConfig_SetBytesString(&config, &config.home, home);
    if (PyStatus_Exception(status)) {
        PyConfig_Clear(&config);
        return status_text(status, "Dossier de Python");
    }
    status = PyConfig_SetBytesString(&config, &config.program_name, "ADA");
    if (PyStatus_Exception(status)) {
        PyConfig_Clear(&config);
        return status_text(status, "Nom du programme");
    }

    char *list = copy_text(paths);
    char *cursor = list;
    while (cursor != NULL && *cursor != '\0') {
        char *end = strchr(cursor, '\n');
        if (end != NULL) {
            *end = '\0';
        }
        if (*cursor != '\0') {
            wchar_t *wide = Py_DecodeLocale(cursor, NULL);
            if (wide != NULL) {
                status = PyWideStringList_Append(&config.module_search_paths, wide);
                PyMem_RawFree(wide);
                if (PyStatus_Exception(status)) {
                    free(list);
                    PyConfig_Clear(&config);
                    return status_text(status, "Chemins de Python");
                }
            }
        }
        cursor = end ? end + 1 : NULL;
    }
    free(list);

    status = Py_InitializeFromConfig(&config);
    PyConfig_Clear(&config);
    if (PyStatus_Exception(status)) {
        return status_text(status, "Démarrage de Python");
    }

    PyObject *module = PyImport_ImportModule("player.bridge");
    if (module == NULL) {
        char *error = exception_text();
        PyEval_SaveThread();
        started = 1;
        return error;
    }
    dispatch_fn = PyObject_GetAttrString(module, "dispatch");
    Py_DECREF(module);
    char *error = NULL;
    if (dispatch_fn == NULL) {
        error = exception_text();
    }
    started = 1;
    // On rend la main : les fils de téléchargement Python peuvent tourner,
    // et chaque appel reprend le verrou de Python (GIL) le temps de s'exécuter.
    PyEval_SaveThread();
    return error;
}

char *ada_python_call(const char *op, const char *json) {
    if (!started || dispatch_fn == NULL) {
        return copy_text("{\"error\":\"Python n'est pas démarré\"}");
    }
    PyGILState_STATE gil = PyGILState_Ensure();
    char *out = NULL;
    PyObject *result = PyObject_CallFunction(dispatch_fn, "ss", op ? op : "", json ? json : "{}");
    if (result != NULL) {
        const char *text = PyUnicode_Check(result) ? PyUnicode_AsUTF8(result) : NULL;
        out = copy_text(text ? text : "{\"error\":\"réponse invalide\"}");
        Py_DECREF(result);
    } else {
        char *error = exception_text();
        free(error);
        out = copy_text("{\"error\":\"exception Python\"}");
    }
    PyGILState_Release(gil);
    return out;
}

void ada_python_free(char *text) {
    free(text);
}
