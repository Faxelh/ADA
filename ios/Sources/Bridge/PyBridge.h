// Pont minimal entre Swift et l'interpréteur Python intégré à l'app.
// Swift ne voit que ces trois fonctions : toute l'API C de Python reste
// dans PyBridge.c.

#ifndef ADA_PYBRIDGE_H
#define ADA_PYBRIDGE_H

// Démarre Python. `home` : dossier « python » de l'app ; `paths` : chemins
// de recherche des modules, séparés par des retours à la ligne.
// Renvoie NULL si tout va bien, sinon un message d'erreur (à libérer avec
// ada_python_free).
char *ada_python_start(const char *home, const char *paths);

// Appelle player.bridge.dispatch(op, json). Renvoie toujours une chaîne
// JSON (à libérer avec ada_python_free). Utilisable depuis n'importe quel fil.
char *ada_python_call(const char *op, const char *json);

void ada_python_free(char *text);

#endif
