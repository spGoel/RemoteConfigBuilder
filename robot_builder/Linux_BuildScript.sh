#!/bin/bash
#
# Land-based Linux build script.
# Usage: ./Linux_BuildScript.sh <target> <3L|5L|AVL> [flags...]
#

set -euo pipefail

# Remember where we started so every function can return to a known root
# instead of walking back up with fragile relative `cd ../../..` chains.
ROOT_DIR="$(pwd)"

# --- Small logging helper -----------------------------------------------------
log() { echo "[build] $*"; }

# --- Help text functions ------------------------------------------------------
echo_target_missing()
{
  echo ""
  echo "Positional Arguments:"
  echo "  <target>        Specifies the hardware or market target. Accepted values:"
  echo "                  - gli: GLI market"
  echo "                  - nsw: NSW market"
  echo "                  - qcom: QCOM market"
  echo "                  - asp: ASP market"
  echo ""
}

echo_build_type_missing()
{
  echo ""
  echo "  <build_type>    Specifies the build type. Accepted values:"
  echo "                  - 3L: Standard 3L build"
  echo "                  - 5L: Standard 5L build"
  echo "                  - AVL: Standard AVL build"
  echo ""
}

# --- Help message -------------------------------------------------------------
if [[ "${1:-}" == "--help" ]]; then
  echo ""
  echo ""
  echo "Usage: $0 <target> <3L|5L|AVL> [--platform] [--game] [--clean] [--showmode] [--production] [--robot] [--asan] [--tcmalloc]"
  echo_target_missing
  echo_build_type_missing
  echo "Optional Flags:"
  echo "  --platform      Build platform components"
  echo "  --game          Build game components"
  echo "  --clean         Clean previous builds"
  echo "  --showmode      Enable show mode"
  echo "  --production    Build production version"
  echo "  --robot         Enable autoplay robot feature"
  echo "  --asan          Enable debug ASAN build option"
  echo "  --tcmalloc      Enable debug tcMalloc build option"
  echo ""
  echo "5L Robot Build:"
  echo "  5L game builds also checkout Configurable Robot source into ./robot,"
  echo "  configure it from build/robot, and build librobot_test.so."
  echo ""
  echo "Examples:"
  echo "  Build both platform and game for GLI target with 3L:"
  echo "    ./LandBased_Linux_BuildScript.sh gli 3L"
  echo "  Build only game for NSW target with 5L and production mode:"
  echo "    ./LandBased_Linux_BuildScript.sh nsw 5L --game --production"
  echo "  Build platform with showmode and robot enabled for QCOM target with AVL:"
  echo "    ./LandBased_Linux_BuildScript.sh qcom AVL --platform --showmode --robot"
  echo "  Clean build for ASP target with 3L:"
  echo "    ./LandBased_Linux_BuildScript.sh asp 3L --clean"
  echo ""
  echo ""
  exit 0
fi

# --- Global set inside checkout functions -------------------------------------
m_gamePath=""
m_robotPath=""

# --- Configurable Robot source ------------------------------------------------
ROBOT_5L_PATH="https://svn.ali.global/nAble/Development/GDK5L/Test/Automation_Script/Configurable_Robot_5L/Robot"

# --- Read positional arguments ------------------------------------------------
target="${1:-}"
build_level="${2:-}"

# Presence checks BEFORE shifting, so a 0/1-arg invocation still gets the
# friendly "missing" messages instead of a `shift` error.
if [[ -z "$target" && -z "$build_level" ]]; then
    echo "  Build Target is missing. Provide one of the below options ......"
    echo_target_missing
    echo "  Build Type is missing. Provide one of the below options ......"
    echo_build_type_missing
    exit 1
fi

if [[ -z "$target" ]]; then
    echo "  Build Target is missing. Provide one of the below options ......"
    echo_target_missing
    exit 1
fi

if [[ -z "$build_level" ]]; then
    echo "  Build Type is missing. Provide one of the below options ......"
    echo_build_type_missing
    exit 1
fi

shift 2

# --- Value checks -------------------------------------------------------------
case "$target" in
    gli|nsw|qcom|asp) ;;
    *)
        echo "  Build Target is incorrect. Provide one of the below options ......"
        echo_target_missing
        exit 1
        ;;
esac

if [[ "$build_level" != "3L" && "$build_level" != "5L" && "$build_level" != "AVL" ]]; then
    echo "  Build Type is incorrect. Provide one of the below options ......"
    echo_build_type_missing
    exit 1
fi

# --- Flags --------------------------------------------------------------------
build_platform=false
build_game=false
clean_build=false
enable_showmode=false
is_production=false
autoplay_robot=false
enable_asan=false
enable_tcmalloc=false

# Parse optional flags
while [[ $# -gt 0 ]]; do
    case "$1" in
        --platform)   build_platform=true ;;
        --game)       build_game=true ;;
        --clean)      clean_build=true ;;
        --showmode)   enable_showmode=true ;;
        --production) is_production=true ;;
        --robot)      autoplay_robot=true ;;
        --asan)       enable_asan=true ;;
        --tcmalloc)   enable_tcmalloc=true ;;
        *)
            echo "Unknown flag: $1" >&2
            echo "Run '$0 --help' for usage." >&2
            exit 1
            ;;
    esac
    shift
done

# Default to building both if neither specified
if ! $build_platform && ! $build_game; then
    build_platform=true
    build_game=true
fi

# --- Target-specific variables ------------------------------------------------
configure_target_vars() {
    case "$target" in
        gli)
            build_dir="build/hostGLI"
            protocol="gampro"
            market="usa"
            ;;
        nsw)
            build_dir="build/hostNSW"
            protocol="xqcom"
            market="nsw"
            ;;
        qcom)
            build_dir="build/hostQCOM"
            protocol="xqcom"
            market="qcom_markets"
            ;;
        asp)
            build_dir="build/hostASP"
            protocol="asp1000"
            market="crown"
            ;;
        *)
            # Should never happen: target is validated above.
            echo "Internal error: unhandled target '$target'" >&2
            exit 1
            ;;
    esac
}

# --- Source checkout ----------------------------------------------------------
checkout_sources_3L() {
    cd "$ROOT_DIR"
    mkdir -p subversion
    cd subversion

    platform_path="https://svn.ali.global/gen7/mk7software/64-bit/Platform/Tags/platform_6.20.0-1.00.4"
    runtime_path="https://svn.ali.global/nAble/Release/3.01/3.01.020/Runtime"
    host_path="${runtime_path}/host/linux"
    core_path="${runtime_path}/core/GDK"
    game_path="https://svn.ali.global/nAble/GDK_Sample_Games/3.01/Release/3.01.020/FrankensteinGame/Frankenstein"

    [ ! -d platform ] && svn checkout "$platform_path" platform
    [ ! -d gdk_host ] && svn checkout "$host_path" gdk_host
    [ ! -d gdk_core ] && svn checkout "$core_path" gdk_core
    game_checkout_dir="$(basename "$game_path")"
    m_gamePath="$PWD/$game_checkout_dir"
    [ ! -d "$game_checkout_dir" ] && svn checkout "$game_path"
}

checkout_sources_5L() {
    cd "$ROOT_DIR"

    platform_path="https://svn.ali.global/gen7/mk7software/64-bit/Platform/Tags/platform_6.22.0-A.00.0"
    runtime_path="https://svn.ali.global/nAble/Development/GDK5L/Runtime"
    game_path="https://svn.ali.global/nAble/GDK_Sample_Games/GDK5L/Trunk/FrankensteinGame/Frankenstein"

    [ ! -d platform ] && svn checkout "$platform_path" platform
    [ ! -d Runtime ]  && svn checkout "$runtime_path"
    [ ! -d robot ] && svn checkout "$ROBOT_5L_PATH" robot
    m_robotPath="$ROOT_DIR/robot"

    mkdir -p SampleGames
    cd SampleGames
    game_checkout_dir="$(basename "$game_path")"
    [ ! -d "$game_checkout_dir" ] && svn checkout "$game_path"
    m_gamePath="$PWD/$game_checkout_dir"
}

checkout_sources_AVL() {
    log "AVL Checkout"
    cd "$ROOT_DIR"
    mkdir -p subversion
    cd subversion

    platform_path="https://svn.ali.global/gen7/mk7software/64-bit/Platform/DevLines/TXL-16485_TimeGraphs"
    gameplatform_path="https://svn.ali.global/gen7/mk7games/gameplatform/tags/2.0.1_HRG.082.004"
    game_path="https://svn.ali.global/gen7/mk7games/games/aussieboomer/tags/gampro_1.02.67623.001"

    [ ! -d platform ]     && svn checkout "$platform_path" platform
    [ ! -d gameplatform ] && svn checkout "$gameplatform_path" gameplatform
    [ ! -d game ]         && svn checkout "$game_path" game
}

# --- Builds -------------------------------------------------------------------
build_platform_3L() {
    cd "$ROOT_DIR"
    mkdir -p "$build_dir"
    cd "$build_dir"

    args=(
        -target=mk7i
        -protocol="$protocol"
        -market="$market"
        -gameplatform=gdk_host
        -game=none
    )
    $is_production   && args+=(-production)
    $enable_showmode && args+=(-show_mode)
    $autoplay_robot  && args+=(-nd_autoplay)
    $enable_asan     && args+=(-useasandefault)
    $enable_tcmalloc && args+=(-usetcmalloc)

    ../../subversion/platform/common/build/configure "${args[@]}"

    cd common/build
    $clean_build && make clean

    # Intentional: fast parallel pass, then a serial pass as a fallback.
    # `|| true` keeps set -e from aborting before the serial fallback runs.
    make "-j$(nproc)" || true
    make
}

build_game_3L() {
    cd "$ROOT_DIR"
    game_checkout_dir="$(basename "$m_gamePath")"
    mkdir -p "build/$game_checkout_dir"
    cd "$m_gamePath"
    chmod +x configure
    ./configure -g ../gdk_core/ -i "../../build/$game_checkout_dir"

    if $is_production; then
        make -s install-release
    else
        $clean_build && make clean
        # Intentional double build (see build_platform_3L).
        make "-j$(nproc)" install || true
        make install
    fi
}

build_platform_5L() {
    cd "$ROOT_DIR"
    mkdir -p "$build_dir"
    cd "$build_dir"
    log "build_dir: $build_dir"

    args=(
        -target=mk7i
        -protocol="$protocol"
        -market="$market"
        -gameplatform=Runtime/host/linux
        -game=none
    )
    $is_production   && args+=(-production)
    $enable_showmode && args+=(-show_mode)
    $autoplay_robot  && args+=(-nd_autoplay)
    $enable_asan     && args+=(-useasandefault)
    $enable_tcmalloc && args+=(-usetcmalloc)

    log "configure args: ${args[*]}"
    log "pwd: $PWD"
    HOST_BUILD_TYPE=cmake ../../platform/common/build/configure "${args[@]}"

    cd common/build
    $clean_build && make clean

    make "-j$(nproc)"
}

build_game_5L() {
    cd "$ROOT_DIR"
    cd "$build_dir"
    log "pwd: $PWD"
    log "game path: $m_gamePath"
    if $enable_asan; then 
        ./configure_game_build --make-args "install-debug" --game-src-dir="$m_gamePath" --cmake-options "PROFILE_ASAN:BOOL=ON" --cmake-options-force
    elif $enable_tcmalloc; then
        ./configure_game_build --make-args "install-debug" --game-src-dir="$m_gamePath" --cmake-options "PROFILE_TCMALLOC:BOOL=ON" --cmake-options-force
    elif $is_production; then
        ./configure_game_build --make-args "install-release" --game-src-dir="$m_gamePath" --cmake-options-force
    else
        ./configure_game_build --make-args "install-debug" --game-src-dir="$m_gamePath" --cmake-options-force
    fi
}

build_robot_5L() {
    cd "$ROOT_DIR"

    if [[ -z "$m_robotPath" ]]; then
        m_robotPath="$ROOT_DIR/robot"
    fi
    if [[ ! -d "$m_robotPath" ]]; then
        echo "Robot source folder not found: $m_robotPath" >&2
        echo "Expected SVN checkout: $ROBOT_5L_PATH" >&2
        exit 1
    fi

    game_checkout_dir="$(basename "$m_gamePath")"
    game_output_name="$(printf '%s' "$game_checkout_dir" | tr '[:upper:]' '[:lower:]')"
    robot_build_dir="$ROOT_DIR/build/robot"
    game_binary_dir="$ROOT_DIR/build/game/$game_output_name"

    log "Configuring configurable robot test library"
    mkdir -p "$robot_build_dir"
    cd "$robot_build_dir"
    cmake \
        -DCMAKE_TOOLCHAIN_FILE=../../platform/common/component/mk7i-toolchain.cmake \
        -DPLATFORM_BUILD_DIR=../$$build_dir/ \
        -DOUTPUT_DIR="../game/$game_output_name" \
        ../../robot/

    if [[ ! -d "$game_binary_dir" ]]; then
        echo "Game binaries folder not found: $game_binary_dir" >&2
        echo "Build the game first, then re-run the robot build." >&2
        exit 1
    fi

    log "Building librobot_test.so"
    cd "$game_binary_dir"
    make -C ../../robot

    log "Robot XML location: $ROOT_DIR/build/host/common/build/robotlogs/robot.xml"
    log "Default robot event log: $ROOT_DIR/build/host/common/build/robotlogs/eventsfile.txt"
}

# Note: AVL's "game" build actually configures both gameplatform and game
# in one pass, so the main dispatch only calls this (there is no separate
# build_platform_AVL).
build_game_AVL() {
    log "AVL Game Build"
    cd "$ROOT_DIR"
    mkdir -p "$build_dir"
    cd "$build_dir"

    args=(
        -target=mk7i
        -protocol="$protocol"
        -market="$market"
        -gameplatform=gameplatform
        -game=game
    )
    $is_production   && args+=(-production)
    $enable_showmode && args+=(-show_mode)
    $autoplay_robot  && args+=(-nd_autoplay)

    ../../subversion/platform/common/build/configure "${args[@]}"

    cd common/build
    $clean_build && make clean

    # Intentional double build (see build_platform_3L).
    make "-j$(nproc)" || true
    make
}

# --- Main execution -----------------------------------------------------------
configure_target_vars

if [[ "$build_level" == "3L" ]]; then
    checkout_sources_3L
    $build_platform && build_platform_3L
    $build_game     && build_game_3L
elif [[ "$build_level" == "5L" ]]; then
    checkout_sources_5L
    $build_platform && build_platform_5L
    if $build_game; then
        build_game_5L
        build_robot_5L
    fi
else
    checkout_sources_AVL
    $build_game && build_game_AVL
fi

log "Done."
