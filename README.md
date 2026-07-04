# Decision Database (DDB) — Unified SDK & CLI

Consolidación de 5 motores de decisión en un SDK Rust unificado (`ddb-sdk`) y CLI (`ddb-cli`). Procesa, audita y persiste toma de decisiones usando lógica determinista, ledger inmutable y sincronización on-chain.

## Arquitectura

```
┌──────────────────────────────────────────────────┐
│                    ddb-cli                        │
│   ledger │ rule │ chain │ vector                  │
└──────────────────────┬───────────────────────────┘
                       │
┌──────────────────────▼───────────────────────────┐
│                    ddb-sdk                        │
│  ┌──────────┬──────────┬──────────┬──────────┐   │
│  │  ledger  │  rules   │  chain   │  vector   │   │
│  │ (PyO3)   │ (json-   │ (alloy/  │ (trait   │   │
│  │ SQLite   │  logic)  │  evm)    │  stub)   │   │
│  └──────────┴──────────┴──────────┴──────────┘   │
└──────────────────────────────────────────────────┘
```

### Módulos

| SDK Module | Crate origen | Descripción |
|---|---|---|
| `ddb_sdk::ledger` | `core-ledger` | Ledger inmutable SQLite + Ed25519 + PyO3 bindings |
| `ddb_sdk::rules` | `logic-core` / `logic-evaluator` | Motor de reglas JSON-Logic |
| `ddb_sdk::chain` | `motor-sincronizacion-onchain` | Sincronización EVM/Base, slashing |
| `ddb_sdk::vector` | — | Trait `VectorStore` + `cosine_similarity` (placeholder) |

## Requisitos

- Rust >= 1.83 (edition 2021)
- SQLite3 (dev libraries para compilar `rusqlite`)

## Instalación

```bash
# Compilar todo el workspace
cargo build --release

# El CLI queda en: target/release/ddb
```

## Uso del CLI

### Ledger (registro inmutable)

```bash
# Inicializar base de datos
ddb ledger init --db mi_ledger.db

# Generar par de claves Ed25519
ddb ledger gen-keys --output keys.json

# Registrar decisión
ddb ledger record \
  --db mi_ledger.db \
  --payload '{"action":"approve"}' \
  --rules '{"==": [1, 1]}' \
  --keyfile keys.json

# Registrar como génesis (primera decisión)
ddb ledger record \
  --db mi_ledger.db \
  --payload '{"action":"init"}' \
  --rules '{}' \
  --keyfile keys.json \
  --genesis

# Validar integridad de la cadena
ddb ledger validate --db mi_ledger.db

# Listar decisiones
ddb ledger list --db mi_ledger.db --limit 10

# Exportar como JSON
ddb ledger export --db mi_ledger.db
```

### Reglas (evaluación JSON-Logic)

```bash
# Evaluar regla contra contexto
ddb rule eval \
  --rule '{"==": [{"var": "x"}, 1]}' \
  --context '{"x": 1}'

# Validar regla
ddb rule validate --rule '{"==": [1, 1]}'

# Bridge stdin/stdout (compatible con engine_1_bridge legacy)
echo '{"rule":"{\"==\":[1,1]}","context":"{}"}' | ddb rule bridge --stdin
```

### On-chain (EVM / Base)

```bash
# Sincronizar depósitos on-chain
ddb chain sync

# Ejecutar slasher
ddb chain slasher
```

### Vector search (placeholder)

```bash
ddb vector search --query "mi busqueda" --top-k 5
```

## Uso como SDK (Rust)

```toml
[dependencies]
ddb-sdk = { path = "ddb-sdk" }
```

```rust
use ddb_sdk::prelude::*;

// Evaluar regla
let result = ddb_sdk::evaluate(
    r#"{"==": [{"var": "x"}, 1]}"#,
    r#"{"x": 1}"#,
)?;

// Validar ledger
let storage = SqliteStorage::new("db.db".into(), "keys.json".into())?;
let report = storage.validate_ledger()?;
```

## Desarrollo

```bash
# Formateo
cargo fmt --all -- --check

# Clippy (con -D warnings como CI)
cargo clippy --all-targets --all-features -- -D warnings

# Tests
cargo test --workspace

# Build release
cargo build --release
```

## Benchmarks

| Operación | Latencia |
|---|---|
| Evaluación numérica (1 regla, 1 contexto) | ~1.24 µs |
| Evaluación genérica (JSON libre) | ~1.87 µs |
| Evaluación batch (1 regla, 10k contextos) | ~2.27 ms |
| Ejecución con metadata (1 regla) | ~1.79 µs |
| Cadena de 2 reglas | ~4.73 µs |
| Carga de 100 reglas desde JSON | ~82 µs |
