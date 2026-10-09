#!/bin/bash
# Phase de compilation « Install Python » : copie dans l'app la bibliothèque
# standard de Python (partie iPhone ou simulateur), le code du moteur d'ADA'S
# et ses paquets (yt-dlp…), puis range chaque module binaire (.so) dans son
# propre « framework », comme l'exige iOS (voir docs.python.org, « Using
# Python on iOS »).
set -euo pipefail

APP="$CODESIGNING_FOLDER_PATH"
XCF="$PROJECT_DIR/Support/Python.xcframework"

if [ "${PLATFORM_NAME:-iphoneos}" = "iphonesimulator" ]; then
    SLICE=$(ls -d "$XCF"/ios-*simulator 2>/dev/null | head -1)
else
    SLICE="$XCF/ios-arm64"
fi
echo "Python : tranche $SLICE"
ARCH=$(echo "${ARCHS:-arm64}" | awk '{print $1}')

# 1. Bibliothèque standard
mkdir -p "$APP/python/lib"
if [ -d "$XCF/lib" ] && [ -d "$SLICE/lib-$ARCH" ]; then
    # Format récent : partie commune (pur Python) + modules binaires propres à l'architecture.
    echo "Bibliothèque commune + lib-$ARCH"
    rsync -a --delete "$XCF/lib/" "$APP/python/lib/"
    rsync -a "$SLICE/lib-$ARCH/" "$APP/python/lib/"
elif ls "$SLICE/lib" 2>/dev/null | grep -q '^python3'; then
    rsync -a --delete "$SLICE/lib/" "$APP/python/lib/"
else
    echo "error: bibliothèque standard introuvable dans $XCF (contenu :)"
    find "$XCF" -maxdepth 2 | head -40
    exit 1
fi
PYTHON_VER=$(ls -1 "$APP/python/lib" | grep '^python3' | head -1)
echo "Bibliothèque standard : $PYTHON_VER"
STDLIB="$APP/python/lib/$PYTHON_VER"
# Inutile sur iPhone (allège l'app) :
rm -rf "$STDLIB/test" "$STDLIB/idlelib" "$STDLIB/tkinter" "$STDLIB/turtledemo" "$STDLIB/ensurepip" \
       "$STDLIB/lib2to3" "$STDLIB/pydoc_data" "$STDLIB/config-"* || true
find "$APP/python" -name "__pycache__" -type d -prune -exec rm -rf {} + || true

# 2. Moteur d'ADA'S et paquets
rsync -a --delete --exclude "__pycache__" "$PROJECT_DIR/build-python/app/" "$APP/app/"
rsync -a --delete --exclude "__pycache__" "$PROJECT_DIR/build-python/app_packages/" "$APP/app_packages/"

# 3. Modules binaires -> frameworks
TEMPLATE="$PROJECT_DIR/scripts/dylib-Info-template.plist"
install_dylib () {
    INSTALL_BASE=$1
    FULL_EXT=$2
    RELATIVE_EXT=${FULL_EXT#$APP/}
    PYTHON_EXT=${RELATIVE_EXT/$INSTALL_BASE/}
    FULL_MODULE_NAME=$(echo "$PYTHON_EXT" | cut -d "." -f 1 | tr "/" ".")
    FRAMEWORK_BUNDLE_ID=$(echo "$PRODUCT_BUNDLE_IDENTIFIER.$FULL_MODULE_NAME" | tr "_" "-")
    FRAMEWORK_FOLDER="Frameworks/$FULL_MODULE_NAME.framework"
    if [ ! -d "$APP/$FRAMEWORK_FOLDER" ]; then
        mkdir -p "$APP/$FRAMEWORK_FOLDER"
        cp "$TEMPLATE" "$APP/$FRAMEWORK_FOLDER/Info.plist"
        plutil -replace CFBundleExecutable -string "$FULL_MODULE_NAME" "$APP/$FRAMEWORK_FOLDER/Info.plist"
        plutil -replace CFBundleIdentifier -string "$FRAMEWORK_BUNDLE_ID" "$APP/$FRAMEWORK_FOLDER/Info.plist"
        if [ "${PLATFORM_NAME:-iphoneos}" = "iphonesimulator" ]; then
            plutil -replace CFBundleSupportedPlatforms -json '["iPhoneSimulator"]' "$APP/$FRAMEWORK_FOLDER/Info.plist"
        fi
    fi
    mv "$FULL_EXT" "$APP/$FRAMEWORK_FOLDER/$FULL_MODULE_NAME"
    echo "$FRAMEWORK_FOLDER/$FULL_MODULE_NAME" > "${FULL_EXT%.so}.fwork"
    echo "${RELATIVE_EXT%.so}.fwork" > "$APP/$FRAMEWORK_FOLDER/$FULL_MODULE_NAME.origin"
}

COUNT=0
while IFS= read -r FULL_EXT; do
    install_dylib "python/lib/$PYTHON_VER/lib-dynload/" "$FULL_EXT"
    COUNT=$((COUNT + 1))
done < <(find "$STDLIB/lib-dynload" -name "*.so")
while IFS= read -r FULL_EXT; do
    install_dylib "app_packages/" "$FULL_EXT"
    COUNT=$((COUNT + 1))
done < <(find "$APP/app_packages" -name "*.so")
echo "$COUNT module(s) binaire(s) rangé(s) en frameworks"

# 4. Signature (seulement si Xcode signe ; l'.ipa non signée est signée par Sideloadly)
if [ "${CODE_SIGNING_ALLOWED:-NO}" = "YES" ] && [ -n "${EXPANDED_CODE_SIGN_IDENTITY:-}" ]; then
    find "$APP/Frameworks" -name "*.framework" -exec /usr/bin/codesign --force --sign "$EXPANDED_CODE_SIGN_IDENTITY" \
        ${OTHER_CODE_SIGN_FLAGS:-} -o runtime --timestamp=none --preserve-metadata=identifier,entitlements,flags \
        --generate-entitlement-der "{}" \;
fi
echo "Python installé dans l'app."
