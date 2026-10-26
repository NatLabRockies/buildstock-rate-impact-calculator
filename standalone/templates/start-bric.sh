#!/bin/bash
# Start the BuildStock Rate Impact Calculator (stand-alone package).
#
# Shipped as "Start BRIC.command" on macOS (double-clickable in Finder) and
# start-bric.sh on Linux. The first run of each version unpacks the bundled
# Python environment into a per-user folder (BRIC_ROOT below); later runs
# start straight away. Environments of older versions are removed after a
# successful unpack. User data is never stored there.
set -u
shopt -s nullglob

BRIC_VERSION="@BRIC_VERSION@"
HERE="$(cd "$(dirname "$0")" && pwd)"

fail() {
    echo
    echo "BuildStock Rate Impact Calculator could not start. See the messages above."
    read -r -p "Press Enter to close this window..." _
    exit 1
}

case "$(uname -s)" in
    Darwin) BRIC_ROOT="$HOME/Library/Application Support/BRIC" ;;
    *) BRIC_ROOT="${XDG_DATA_HOME:-$HOME/.local/share}/BRIC" ;;
esac
INSTALL="$BRIC_ROOT/$BRIC_VERSION"
# Written last by a successful unpack. A folder without it is left over from an
# interrupted unpack and is redone.
MARKER="$INSTALL/.bric-unpacked"

if [ ! -f "$MARKER" ]; then
    if [ ! -f "$HERE/environment.tar" ]; then
        echo "environment.tar was not found next to this script. Unzip the whole download first."
        fail
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
    "$UNPACK" "$HERE/environment.tar" --output-directory "$INSTALL" --shell bash || fail
    echo "$BRIC_VERSION" > "$MARKER"

    # Remove environments left by older versions (about 2 GB each).
    for old in "$BRIC_ROOT"/*/; do
        [ "$(basename "$old")" = "$BRIC_VERSION" ] || rm -rf "$old"
    done
    echo
fi

# shellcheck disable=SC1091
source "$INSTALL/activate.sh" || fail
# Arguments are passed through (e.g. --no-browser --port 8599 for automated checks).
bric "$@" || fail
