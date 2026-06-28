#!/usr/bin/env python3
import json
import subprocess
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
ENGINE1_BRIDGE = BASE_DIR / "target" / "release" / "engine_1_bridge.exe"
LEDGER_PATH = BASE_DIR / "_e2e_demo_ledger.sqlite"
KEY_PATH = BASE_DIR / "_e2e_demo_keypair.json"

def main():
    print("DECISION DATABASE (DDB) — Demo End-to-End")
    print("=========================================")
    print("1. Ingesta (Engine 2)")
    try:
        from orchestrator.agent import RAG
        rag = RAG(embedded=True)
        print("  [OK] RAG Engine importado")
    except ImportError as e:
        print(f"  [ERROR] Error RAG: {e}")

    print("2. Evaluación (Engine 1)")
    if ENGINE1_BRIDGE.exists():
        print("  [OK] Bridge Rust encontrado")
    else:
        print("  [WARN] Bridge no compilado. Ejecuta: cargo build --release")

    print("3. Registro (Engine 5)")
    try:
        from _core_ledger import CoreLedger
        print("  [OK] PyO3 CoreLedger importado")
    except ImportError as e:
        print(f"  [ERROR] Error DDB: {e}")

if __name__ == "__main__":
    main()
