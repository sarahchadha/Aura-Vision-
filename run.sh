#!/usr/bin/env bash
# AuraVision Linux / macOS Launcher
set -e

echo "============================================================"
echo "  AuraVision Edge Processing System"
echo "  NSUT Devlympics Edition"
echo "============================================================"
echo ""

if ! command -v python3 &> /dev/null
then
    echo "[ERROR] python3 could not be found. Please install Python 3.10+."
    exit 1
fi

python3 auravision.py
