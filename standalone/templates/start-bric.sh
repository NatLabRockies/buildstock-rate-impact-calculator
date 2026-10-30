#!/bin/bash
# Start the BuildStock Rate Impact Calculator (stand-alone package).
#
# Shipped as "Start BRIC.command" on macOS (double-clickable in Finder) and
# start-bric.sh on Linux. The first run of each build unpacks the bundled
# Python environment into a per-user folder (BRIC_ROOT below); later runs
# start straight away. Environments of older versions are removed after a
# successful unpack. User data is never stored there.
set -u
shopt -s nullglob

BRIC_VERSION="@BRIC_VERSION@"
# Identifies this exact build, so a rebuild of the same version re-unpacks.
BRIC_BUILD="@BRIC_BUILD@"
HERE="$(cd "$(dirname "$0")" && pwd)"

pause_and_exit() {
    read -r -p "Press Enter to close this window..." _
    exit "$1"
}

setup_failed() {
    echo
    echo "BuildStock Rate Impact Calculator could not be set up. See the messages above."
    pause_and_exit 1
}

# LOG_DIR must match default_log_dir() in launcher.py.
case "$(uname -s)" in
    Darwin)
        BRIC_ROOT="$HOME/Library/Application Support/BRIC"
        LOG_DIR="$HOME/Library/Logs/BRIC"
        ;;
    *)
        BRIC_ROOT="${XDG_DATA_HOME:-$HOME/.local/share}/BRIC"
        LOG_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/BRIC"
        ;;
esac
LOG_DIR="${BRIC_LOG_DIR:-$LOG_DIR}"
INSTALL="$BRIC_ROOT/$BRIC_VERSION"
# Written last by a successful unpack and holds its build. A missing or
# different build means an interrupted unpack or a new build: redo it.
MARKER="$INSTALL/.bric-unpacked"

if [ "$(cat "$MARKER" 2>/dev/null)" != "$BRIC_BUILD" ]; then
    if [ ! -f "$HERE/environment.tar" ]; then
        echo "environment.tar was not found next to this script. Unzip the whole download first."
        setup_failed
    fi
    echo "Setting up BuildStock Rate Impact Calculator $BRIC_VERSION."
    echo "This happens once per version and takes a minute or two..."
    echo
    UNPACK="$HERE/pixi-unpack"
    chmod +x "$UNPACK" 2>/dev/null
    # macOS quarantines downloaded files. The user has already chosen to run
    # this script, so let it run the unpacker shipped beside it.
    xattr -d com.apple.quarantine "$UNPACK" 2>/dev/null
    rm -rf "$INSTALL"
    "$UNPACK" "$HERE/environment.tar" --output-directory "$INSTALL" --shell bash || setup_failed
    # Static libraries and C headers are only used to compile against the
    # environment, never to run the app.
    find "$INSTALL/env" -type f -name '*.a' -delete 2>/dev/null
    rm -rf "$INSTALL/env/include"
    echo "$BRIC_BUILD" > "$MARKER"

    # Remove environments left by older versions (about 2 GB each).
    for old in "$BRIC_ROOT"/*/; do
        [ "$(basename "$old")" = "$BRIC_VERSION" ] || rm -rf "$old"
    done
    echo
fi

# shellcheck disable=SC1091
source "$INSTALL/activate.sh" || setup_failed
# Arguments are passed through (e.g. --no-browser --port 8599 for automated checks).
bric "$@"
rc=$?
[ "$rc" -eq 0 ] && exit 0

echo
echo "BuildStock Rate Impact Calculator stopped unexpectedly (exit code $rc)."
logs=("$LOG_DIR"/bric-*.log)
if [ "${#logs[@]}" -gt 0 ]; then
    latest="${logs[${#logs[@]}-1]}"
    echo "The end of its log file ($latest) follows:"
    echo
    tail -n 40 "$latest"
fi
echo
echo "If this keeps happening, please send that log file to the BRIC developers."
pause_and_exit "$rc"
