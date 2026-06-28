import typer
import json
import subprocess
import sys
from pathlib import Path

app = typer.Typer(help="Decision Database commands")

ENGINE1_BIN = Path(__file__).parent.parent.parent / "target" / "release" / "engine_1_bridge.exe"
LEDGER_DEFAULT = Path.cwd() / "core_ledger.sqlite"
KEY_DEFAULT = Path.cwd() / "keypair.json"

@app.command()
def init_keys(output: str = str(KEY_DEFAULT)):
    subprocess.run([
        str(ENGINE1_BIN.parent / "core_ledger"),
        "GenKeys", "--output", output
    ], check=True)
    print(f"Claves generadas en: {output}")

@app.command()
def ingest(path: str, embedded: bool = True):
    from orchestrator.agent import RAG
    rag = RAG(embedded=embedded)
    rag.ingest(path)
    print(f"Documento ingerido: {path}")

@app.command()
def rule(rule: str, context: str):
    from orchestrator_cli.schemas import BridgeInputSchema, BridgeOutputSchema
    import sys
    
    try:
        rule_data = json.loads(Path(rule).read_text())
        context_data = json.loads(Path(context).read_text())
        
        # Validar con Pydantic antes de enviar al Engine 1 (Rust)
        validated_input = BridgeInputSchema(rule=rule_data, context=context_data)
    except Exception as e:
        print(f"Error de validación Pydantic: {e}", file=sys.stderr)
        raise typer.Exit(1)

    proc = subprocess.run(
        [str(ENGINE1_BIN)],
        input=validated_input.model_dump_json(),
        capture_output=True, text=True, check=True
    )
    
    try:
        result = BridgeOutputSchema.model_validate_json(proc.stdout)
        print(result.model_dump_json(indent=2))
    except Exception as e:
        print(f"Error parseando salida del Bridge: {e}", file=sys.stderr)
        print(f"Stdout crudo: {proc.stdout}", file=sys.stderr)
        raise typer.Exit(1)

@app.command()
def record(payload: str, rule: str = None, genesis: bool = False,
           ledger: str = str(LEDGER_DEFAULT), key: str = str(KEY_DEFAULT)):
    try:
        from _core_ledger import CoreLedger
    except ImportError:
        print("ERROR: PyO3 module '_core_ledger' not found.", file=sys.stderr)
        raise typer.Exit(1)

    ddb = CoreLedger(str(Path(ledger).absolute()), str(Path(key).absolute()))
    rule_str = Path(rule).read_text() if rule else "{}"
    payload_str = Path(payload).read_text()

    decision_id = ddb.record(payload=payload_str, rules=rule_str, genesis=genesis)
    print(f"Decisión registrada: {decision_id}")

@app.command()
def status(ledger: str = str(LEDGER_DEFAULT)):
    try:
        from _core_ledger import CoreLedger
        ddb = CoreLedger(str(Path(ledger).absolute()), str(KEY_DEFAULT))
        info = json.loads(ddb.export())
        print(f"Entradas en ledger: {len(info)}")
        print(f"Último hash: {info[-1]['id'] if info else 'N/A'}")
    except ImportError:
        print("Ledger stats: no disponible")

if __name__ == "__main__":
    app()
