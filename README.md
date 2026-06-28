# Tempus DDB (Decision Database) Unified Engine

Este repositorio es la versión unificada del motor de base de datos de decisiones (DDB), diseñado para procesar, auditar y persistir la toma de decisiones utilizando lógica determinista, RAG (Retrieval-Augmented Generation) y sincronización con Blockchain.

Originalmente fragmentado en múltiples repositorios, este proyecto consolida todos los motores en un ecosistema robusto, combinando el alto rendimiento de **Rust** con la flexibilidad de orquestación en **Python**.

## 🏗️ Arquitectura y Componentes

El proyecto se divide en dos mundos interconectados: **Rust** (Evaluación y Persistencia) y **Python** (Semántica y Orquestación).

### Motores Rust (Workspace: `Cargo.toml` raíz)
* **`Motor-Evaluacion-Logica`**: El núcleo de ejecución de reglas de negocio (`tempus-engine-core`), construido sobre `jsonlogic-fast-core`. Incluye un `engine_1_bridge` que expone la API hacia procesos externos (Python) vía IPC de Stdin/Stdout.
* **`Motor-Base-Datos-Decisiones` (Engine 5)**: Motor responsable del registro inmutable (Ledger) de las decisiones usando SQLite local y exportaciones B2A. Expone `TempusDDB` a Python mediante **PyO3**.
* **`Motor-Sincronizacion-OnChain` (Engine 4)**: Scripts y binarios (`slasher`, `sync_deposits`) que permiten interactuar asíncronamente con Smart Contracts (Solana/EVM) y validar el consenso distribuido.

### Framework Python (`python/`)
* **Paquete `ses`**: Consolida la gestión semántica y transaccional:
  * **RAG (Engine 2)**: (`ses.core.rag`) Ingesta y búsqueda vectorial de contexto en documentos.
  * **Curaduría y Pagos (Engine 3)**: Módulos bajo `ses.curation` y `ses.payments`.
* **Paquete `ses_ddb`**: CLI orquestador global (vía Typer) para conectar todos los motores y simular ejecuciones completas de ingesta y validación de reglas.

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
cp target/release/_tempus_ddb.dll python/_tempus_ddb.pyd
```
*(En Linux/Mac)*
```bash
cp target/release/lib_tempus_ddb.so python/_tempus_ddb.so
```

### 3. Instalar Paquetes Python
Instala el framework unificado en modo desarrollador:

```bash
pip install -e ./python
```

## 🛠️ Uso del CLI (ses-ddb)

El CLI te permite orquestar todas las funcionalidades desde la terminal.

**1. Generar Claves Criptográficas (Engine 5)**
```bash
ses-ddb init-keys
```

**2. Ingestar un Documento Contextual (Engine 2)**
```bash
ses-ddb ingest ./documento.txt
```

**3. Evaluar Lógica de Negocio (Engine 1)**
```bash
ses-ddb rule ./regla.json ./contexto.json
```

**4. Registrar una Decisión Inmutable (Engine 5)**
```bash
ses-ddb record ./payload.json --rule ./regla.json
```

**5. Revisar el Estado del Ledger (Engine 5)**
```bash
ses-ddb status
```

## 🧪 Pruebas End-to-End

Puedes validar que el enlace entre Python y los binarios Rust funciona ejecutando el script de demostración incluido:

```bash
python python/e2e_demo.py
```

Esto probará:
- La importación del motor RAG (Engine 2).
- La disponibilidad e intercomunicación con el CLI binario de Rust (Engine 1).
- La vinculación de PyO3 con el Ledger DDB (Engine 5).
