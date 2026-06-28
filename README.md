# Core Ledger (Decision Database) Unified Engine

Este repositorio es la versión unificada del motor de base de datos de decisiones (DDB), diseñado para procesar, auditar y persistir la toma de decisiones utilizando lógica determinista, RAG (Retrieval-Augmented Generation) y sincronización con Blockchain.

Originalmente fragmentado en múltiples repositorios, este proyecto consolida todos los motores en un ecosistema robusto, combinando el alto rendimiento de **Rust** con la flexibilidad de orquestación en **Python**.

## 🏗️ Arquitectura y Componentes

El proyecto se divide en dos mundos interconectados: **Rust** (Evaluación y Persistencia) y **Python** (Semántica y Orquestación).

### Motores Rust (Workspace: `Cargo.toml` raíz)
* **`Motor-Evaluacion-Logica`**: El núcleo de ejecución de reglas de negocio (`logic-evaluator-core`), construido sobre `logic-core-lib`. Incluye un `engine_1_bridge` que expone la API hacia procesos externos (Python) vía IPC de Stdin/Stdout.
* **`Motor-Base-Datos-Decisiones` (Engine 5)**: Motor responsable del registro inmutable (Ledger) de las decisiones usando SQLite local y exportaciones B2A. Expone `CoreLedger` a Python mediante **PyO3**.
* **`Motor-Sincronizacion-OnChain` (Engine 4)**: Scripts y binarios (`slasher`, `sync_deposits`) que permiten interactuar asíncronamente con Smart Contracts (Solana/EVM) y validar el consenso distribuido.

### Framework Python (`python/`)
* **Paquete `ses`**: Consolida la gestión semántica y transaccional:
  * **RAG (Engine 2)**: (`orchestrator.core.rag`) Ingesta y búsqueda vectorial de contexto en documentos.
  * **Curaduría y Pagos (Engine 3)**: Módulos bajo `orchestrator.curation` y `orchestrator.payments`.
* **Paquete `orchestrator`**: CLI orquestador global (vía Typer) para conectar todos los motores y simular ejecuciones completas de ingesta y validación de reglas.

## 🚀 Instalación y Configuración

El proyecto requiere **Rust** (cargo) y **Python** (>=3.9). Se recomienda usar `uv` o un entorno virtual para Python.

### 1. Compilar los Motores Rust
Desde la raíz del proyecto, compila todo el workspace en modo release. Esto generará el ejecutable del bridge y la extensión nativa `.dll` / `.so` para Python.

```bash
cargo build --release
```

### 2. Configurar el Entorno Python
Copia la librería compilada generada por PyO3 a la carpeta de Python para que sea importable:
*(En Windows)*
```bash
cp target/release/_core_ledger.dll python/_core_ledger.pyd
```
*(En Linux/Mac)*
```bash
cp target/release/lib_core_ledger.so python/_core_ledger.so
```

### 3. Instalar Paquetes Python
Instala el framework unificado en modo desarrollador:

```bash
pip install -e ./python
```

## 🛠️ Uso del CLI (orchestrator-cli)

El CLI te permite orquestar todas las funcionalidades desde la terminal.

**1. Generar Claves Criptográficas (Engine 5)**
```bash
orchestrator-cli init-keys
```

**2. Ingestar un Documento Contextual (Engine 2)**
```bash
orchestrator-cli ingest ./documento.txt
```

**3. Evaluar Lógica de Negocio (Engine 1)**
```bash
orchestrator-cli rule ./regla.json ./contexto.json
```

**4. Registrar una Decisión Inmutable (Engine 5)**
```bash
orchestrator-cli record ./payload.json --rule ./regla.json
```

**5. Revisar el Estado del Ledger (Engine 5)**
```bash
orchestrator-cli status
```

## 🧪 Pruebas End-to-End y Demostración

Puedes validar que el enlace entre Python y los binarios Rust funciona ejecutando el script de demostración incluido:

```bash
uv run python python/e2e_demo.py
```

**Salida Esperada:**
```text
DECISION DATABASE (DDB) — Demo End-to-End
=========================================
1. Ingesta (Engine 2)
  [OK] RAG Engine importado
2. Evaluación (Engine 1)
  [OK] Bridge Rust encontrado
3. Registro (Engine 5)
  [OK] PyO3 CoreLedger importado
```
Esto probará:
- La importación del motor RAG (Engine 2).
- La disponibilidad e intercomunicación con el CLI binario de Rust (Engine 1).
- La vinculación de PyO3 con el Ledger DDB (Engine 5).

## ⚡ Benchmarks de Rendimiento

El motor de reglas está altamente optimizado. A continuación, se muestran latencias medidas usando **Criterion** en un CPU moderno:

### Evaluación Base (`logic-core`)
* **Evaluación Numérica (1 regla vs 1 contexto):** `~1.24 µs`
* **Evaluación Genérica (JSON libre):** `~1.87 µs`
* **Evaluación en Lote (1 regla vs 10,000 contextos):** `~2.27 ms`

### Orquestación de Negocio (`logic-evaluator-core`)
* **Ejecutar 1 Regla (con validación de metadata):** `~1.79 µs`
* **Ejecutar Batch (1 regla vs 1,000 contextos):** `~287 µs`
* **Cadena de Reglas (2 reglas encadenadas):** `~4.73 µs`
* **Evaluación Explicativa (`execute_explain`, modo auditoría con árbol de decisiones):** `~3.49 µs`
* **Carga de 100 reglas desde JSON (RuleStore):** `~82 µs`
* **Búsqueda (Get) en cache de 100 reglas:** `~24 ns`
