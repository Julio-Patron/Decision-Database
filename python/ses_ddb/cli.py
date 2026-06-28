import typer
import json
import subprocess
import sys
from pathlib import Path

app = typer.Typer(help="Decision Database commands")

ENGINE1_BIN = Path(__file__).parent.parent.parent / "target" / "release" / "engine_1_bridge.exe"
LEDGER_DEFAULT = Path.cwd() / "tempus_ddb.sqlite"
KEY_DEFAULT = Path.cwd() / "keypair.json"

@app.command()
def init_keys(output: str = str(KEY_DEFAULT)):
    subprocess.run([
        str(ENGINE1_BIN.parent / "tempus_ddb"),
        "GenKeys", "--output", output
    ], check=True)
    print(f"Claves generadas en: {output}")

@app.command()
def ingest(path: str, embedded: bool = True):
    from ses.agent import RAG
    rag = RAG(embedded=embedded)
    rag.ingest(path)
    print(f"Documento ingerido: {path}")

@app.command()
def rule(rule: str, context: str):
    rule_data = json.loads(Path(rule).read_text())
    context_data = json.loads(Path(context).read_text())
    proc = subprocess.run(
        [str(ENGINE1_BIN)],
        input=json.dumps({"rule": rule_data, "context": context_data}),
        capture_output=True, text=True, check=True
    )
    result = json.loads(proc.stdout)
    print(json.dumps(result, indent=2))

@app.command()
def record(payload: str, rule: str = None, genesis: bool = False,
           ledger: str = str(LEDGER_DEFAULT), key: str = str(KEY_DEFAULT)):
    try:
        from _tempus_ddb import TempusDDB
    except ImportError:
        print("ERROR: PyO3 module '_tempus_ddb' not found.", file=sys.stderr)
        raise typer.Exit(1)

    ddb = TempusDDB(str(Path(ledger).absolute()), str(Path(key).absolute()))
    rule_str = Path(rule).read_text() if rule else "{}"
    payload_str = Path(payload).read_text()

    decision_id = ddb.record(payload=payload_str, rules=rule_str, genesis=genesis)
    print(f"Decisión registrada: {decision_id}")

@app.command()
def status(ledger: str = str(LEDGER_DEFAULT)):
    try:
        from _tempus_ddb import TempusDDB
        ddb = TempusDDB(str(Path(ledger).absolute()), str(KEY_DEFAULT))
        info = json.loads(ddb.export())
        print(f"Entradas en ledger: {len(info)}")
        print(f"Último hash: {info[-1]['id'] if info else 'N/A'}")
    except ImportError:
        print("Ledger stats: no disponible")

if __name__ == "__main__":
    app()
