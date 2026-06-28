#[cfg(not(target_arch = "wasm32"))]
use pyo3::exceptions::{PyIOError, PyPermissionError, PyRuntimeError};
#[cfg(not(target_arch = "wasm32"))]
use pyo3::prelude::*;

#[cfg(target_arch = "wasm32")]
use wasm_bindgen::prelude::*;

// --- 1. PATRÓN STORAGE LAYER (TRAIT) ---

// --- 2. PATRÓN STORAGE LAYER (TRAIT) ---
pub trait StorageLayer {
    fn insert_decision(&mut self, payload: &str, rules: &str, genesis: bool) -> Result<(), String>;
    fn get_latest_hash(&self) -> Result<String, String>;
    fn export_ledger(&self) -> Result<String, String>;
}

// Experimental WASM support: in-memory stub storage only, not persistent ledger storage.
#[cfg(target_arch = "wasm32")]
pub struct MemoryStorage {
    records: Vec<String>,
    latest_hash: String,
}

#[cfg(target_arch = "wasm32")]
impl MemoryStorage {
    pub fn new() -> Self {
        Self {
            records: Vec::new(),
            latest_hash: "GENESIS_HASH_MEM".to_string(),
        }
    }
}

#[cfg(target_arch = "wasm32")]
impl StorageLayer for MemoryStorage {
    fn insert_decision(
        &mut self,
        payload: &str,
        _rules: &str,
        genesis: bool,
    ) -> Result<(), String> {
        self.records.push(payload.to_string());
        if genesis {
            self.latest_hash = "GENESIS_HASH_MEM".to_string();
        } else {
            self.latest_hash = "NEW_HASH_MEM".to_string(); // Simulación de nuevo hash
        }
        Ok(())
    }

    fn get_latest_hash(&self) -> Result<String, String> {
        Ok(self.latest_hash.clone())
    }

    fn export_ledger(&self) -> Result<String, String> {
        // En una implementación real, serializaríamos a JSON usando serde_json
        Ok(format!("[{}]", self.records.join(",")))
    }
}

// Implementación SQLite (Para Python / Nativo)
#[cfg(not(target_arch = "wasm32"))]
use rusqlite::Connection;

#[cfg(not(target_arch = "wasm32"))]
use serde::{Deserialize, Serialize};

#[cfg(not(target_arch = "wasm32"))]
use sha2::{Digest, Sha256};

#[cfg(not(target_arch = "wasm32"))]
use ed25519_dalek::Signer;

#[cfg(not(target_arch = "wasm32"))]
#[derive(Debug, Serialize, Deserialize, Clone)]
struct Decision {
    id: String,
    parent_id: String,
    causal_depth: u64,
    actor_id: String,
    timestamp: u64,
    payload: String,
    rules_evaluated: String,
    signature: String,
}

#[cfg(not(target_arch = "wasm32"))]
pub struct SqliteStorage {
    conn: Connection,
    keyfile: String,
}

#[cfg(not(target_arch = "wasm32"))]
impl SqliteStorage {
    pub fn new(db_path: String, keyfile: String) -> Result<Self, String> {
        let conn = Connection::open(&db_path)
            .map_err(|e| format!("Failed to open SQLite database '{}': {}", db_path, e))?;

        conn.execute(
            "CREATE TABLE IF NOT EXISTS decisions (
                id TEXT PRIMARY KEY,
                parent_id TEXT NOT NULL,
                causal_depth INTEGER NOT NULL,
                actor_id TEXT NOT NULL,
                timestamp INTEGER NOT NULL,
                payload TEXT NOT NULL,
                rules_evaluated TEXT NOT NULL,
                signature TEXT NOT NULL
            );",
            [],
        )
        .map_err(|e| format!("Failed to create decisions table: {}", e))?;

        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_parent_id ON decisions (parent_id);",
            [],
        )
        .map_err(|e| format!("Failed to create parent index: {}", e))?;

        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_causal_depth ON decisions (causal_depth);",
            [],
        )
        .map_err(|e| format!("Failed to create causal depth index: {}", e))?;

        Ok(Self { conn, keyfile })
    }

    /// Load an Ed25519 signing key from the keyfile on disk.
    fn load_signing_key(&self) -> Result<ed25519_dalek::SigningKey, String> {
        #[derive(Deserialize)]
        struct KeyPairConfig {
            #[allow(dead_code)]
            public_key: String,
            private_key: String,
        }

        let content = std::fs::read_to_string(&self.keyfile)
            .map_err(|e| format!("Failed to read key file '{}': {}", self.keyfile, e))?;
        let config: KeyPairConfig = serde_json::from_str(&content)
            .map_err(|e| format!("Failed to parse key file JSON: {}", e))?;

        let private_key_bytes = hex::decode(&config.private_key)
            .map_err(|e| format!("Invalid private key hex: {}", e))?;
        let private_key_array: [u8; 32] = private_key_bytes
            .try_into()
            .map_err(|_| "Private key must be exactly 32 bytes".to_string())?;

        Ok(ed25519_dalek::SigningKey::from_bytes(&private_key_array))
    }

    /// Compute the canonical SHA-256 hash for a decision (matches main.rs logic).
    fn calculate_canonical_hash(
        parent_id: &str,
        actor_id: &str,
        timestamp: u64,
        payload: &str,
        rules_evaluated: &str,
    ) -> [u8; 32] {
        #[derive(Serialize)]
        struct CanonicalPayload<'a> {
            parent_id: &'a str,
            actor_id: &'a str,
            timestamp: u64,
            payload: serde_json::Value,
            rules_evaluated: serde_json::Value,
        }

        let payload_val: serde_json::Value =
            serde_json::from_str(payload).unwrap_or(serde_json::Value::Null);
        let rules_val: serde_json::Value =
            serde_json::from_str(rules_evaluated).unwrap_or(serde_json::Value::Null);

        let canonical = CanonicalPayload {
            parent_id,
            actor_id,
            timestamp,
            payload: payload_val,
            rules_evaluated: rules_val,
        };

        let canonical_bytes = serde_json::to_vec(&canonical).unwrap();

        let mut hasher = Sha256::new();
        hasher.update(&canonical_bytes);
        hasher.finalize().into()
    }

    fn get_last_decision_conn(conn: &rusqlite::Connection) -> Result<Option<Decision>, String> {
        let mut stmt = conn.prepare(
            "SELECT id, parent_id, causal_depth, actor_id, timestamp, payload, rules_evaluated, signature
             FROM decisions
             ORDER BY causal_depth DESC, timestamp DESC
             LIMIT 1"
        ).map_err(|e| format!("Failed to prepare select statement: {}", e))?;

        let mut rows = stmt
            .query([])
            .map_err(|e| format!("Failed to query database: {}", e))?;
        if let Some(row) = rows
            .next()
            .map_err(|e| format!("Error advancing row: {}", e))?
        {
            Ok(Some(Decision {
                id: row.get(0).map_err(|e| e.to_string())?,
                parent_id: row.get(1).map_err(|e| e.to_string())?,
                causal_depth: row.get(2).map_err(|e| e.to_string())?,
                actor_id: row.get(3).map_err(|e| e.to_string())?,
                timestamp: row.get(4).map_err(|e| e.to_string())?,
                payload: row.get(5).map_err(|e| e.to_string())?,
                rules_evaluated: row.get(6).map_err(|e| e.to_string())?,
                signature: row.get(7).map_err(|e| e.to_string())?,
            }))
        } else {
            Ok(None)
        }
    }

    #[allow(dead_code)]
    fn get_last_decision(&self) -> Result<Option<Decision>, String> {
        Self::get_last_decision_conn(&self.conn)
    }

    pub fn validate_ledger(&self) -> Result<String, String> {
        let mut stmt = self.conn.prepare(
            "SELECT id, parent_id, causal_depth, actor_id, timestamp, payload, rules_evaluated, signature
             FROM decisions
             ORDER BY causal_depth ASC, timestamp ASC"
        ).map_err(|e| format!("Failed to prepare select statement: {}", e))?;

        let rows = stmt
            .query_map([], |row| {
                Ok(Decision {
                    id: row.get(0)?,
                    parent_id: row.get(1)?,
                    causal_depth: row.get(2)?,
                    actor_id: row.get(3)?,
                    timestamp: row.get(4)?,
                    payload: row.get(5)?,
                    rules_evaluated: row.get(6)?,
                    signature: row.get(7)?,
                })
            })
            .map_err(|e| format!("Failed to execute query: {}", e))?;

        let mut decisions = Vec::new();
        for r in rows {
            match r {
                Ok(d) => decisions.push(d),
                Err(e) => return Err(format!("Error reading record: {}", e)),
            }
        }

        if decisions.is_empty() {
            return Ok(serde_json::to_string(&serde_json::json!({
                "status": "valid",
                "message": "Database is empty.",
                "total_records": 0
            }))
            .unwrap());
        }

        use std::collections::HashMap;
        let mut decision_map = HashMap::new();
        for d in &decisions {
            decision_map.insert(d.id.clone(), d.clone());
        }

        let mut errors = Vec::new();

        for d in &decisions {
            let computed_hash = Self::calculate_canonical_hash(
                &d.parent_id,
                &d.actor_id,
                d.timestamp,
                &d.payload,
                &d.rules_evaluated,
            );
            let computed_id = hex::encode(computed_hash);
            if computed_id != d.id {
                errors.push(format!(
                    "Decision '{}' has invalid hash. Computed: '{}', Recorded: '{}'",
                    d.id, computed_id, d.id
                ));
                continue;
            }

            use ed25519_dalek::{Signature, Verifier, VerifyingKey};
            let pub_bytes = match hex::decode(&d.actor_id) {
                Ok(b) => b,
                Err(e) => {
                    errors.push(format!("Invalid actor_id: {}", e));
                    continue;
                }
            };
            let pub_array: [u8; 32] = match pub_bytes.try_into() {
                Ok(a) => a,
                Err(_) => {
                    errors.push("Invalid public key size".to_string());
                    continue;
                }
            };
            let verifying_key = match VerifyingKey::from_bytes(&pub_array) {
                Ok(k) => k,
                Err(e) => {
                    errors.push(format!("Invalid public key: {}", e));
                    continue;
                }
            };

            let sig_bytes = match hex::decode(&d.signature) {
                Ok(b) => b,
                Err(e) => {
                    errors.push(format!("Invalid signature hex: {}", e));
                    continue;
                }
            };
            let sig_array: [u8; 64] = match sig_bytes.try_into() {
                Ok(a) => a,
                Err(_) => {
                    errors.push("Invalid signature size".to_string());
                    continue;
                }
            };
            let signature = Signature::from_bytes(&sig_array);

            let id_bytes = match hex::decode(&d.id) {
                Ok(b) => b,
                Err(e) => {
                    errors.push(format!("Invalid id hex: {}", e));
                    continue;
                }
            };

            if let Err(e) = verifying_key.verify(&id_bytes, &signature) {
                errors.push(format!(
                    "Decision '{}' signature verification failed: {}",
                    d.id, e
                ));
                continue;
            }

            if d.parent_id != "genesis" {
                match decision_map.get(&d.parent_id) {
                    Some(parent) => {
                        if d.causal_depth != parent.causal_depth + 1 {
                            errors.push(format!(
                                "Decision '{}' causal depth mismatch. Expected '{}', found '{}'",
                                d.id,
                                parent.causal_depth + 1,
                                d.causal_depth
                            ));
                        }
                        if d.timestamp < parent.timestamp {
                            errors.push(format!(
                                "Decision '{}' temporal anomaly. Precedes parent '{}'",
                                d.id, d.parent_id
                            ));
                        }
                    }
                    None => {
                        errors.push(format!(
                            "Decision '{}' orphan node. Parent '{}' missing",
                            d.id, d.parent_id
                        ));
                    }
                }
            } else {
                if d.causal_depth != 0 {
                    errors.push(format!(
                        "Genesis decision '{}' must have causal_depth 0",
                        d.id
                    ));
                }
            }
        }

        if errors.is_empty() {
            Ok(serde_json::to_string(&serde_json::json!({
                "status": "valid",
                "message": "All decisions verified successfully.",
                "total_records": decisions.len()
            }))
            .unwrap())
        } else {
            Err(serde_json::to_string(&serde_json::json!({
                "status": "invalid",
                "errors": errors,
                "total_records": decisions.len()
            }))
            .unwrap())
        }
    }
}

#[cfg(not(target_arch = "wasm32"))]
impl StorageLayer for SqliteStorage {
    fn insert_decision(&mut self, payload: &str, rules: &str, genesis: bool) -> Result<(), String> {
        // Validate payload and rules are valid JSON
        serde_json::from_str::<serde_json::Value>(payload)
            .map_err(|e| format!("Payload is not valid JSON: {}", e))?;
        serde_json::from_str::<serde_json::Value>(rules)
            .map_err(|e| format!("Rules is not valid JSON: {}", e))?;

        // Load signing key from keyfile
        let signing_key = self.load_signing_key()?;
        let verifying_key = signing_key.verifying_key();
        let actor_id = hex::encode(verifying_key.to_bytes());

        let tx = self
            .conn
            .transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)
            .map_err(|e| format!("Failed to begin transaction: {}", e))?;

        // Resolve parent_id and causal_depth
        let (parent_id, causal_depth) = match Self::get_last_decision_conn(&tx)? {
            Some(last_d) => {
                if genesis {
                    // Even if there are existing decisions, --genesis forces a new root
                    ("genesis".to_string(), 0u64)
                } else {
                    (last_d.id, last_d.causal_depth + 1)
                }
            }
            None => {
                if genesis {
                    ("genesis".to_string(), 0u64)
                } else {
                    return Err(
                        "Database is empty. Use genesis=true to record the first decision."
                            .to_string(),
                    );
                }
            }
        };

        if parent_id == "genesis" {
            let mut stmt = tx
                .prepare("SELECT 1 FROM decisions WHERE parent_id = 'genesis' LIMIT 1")
                .map_err(|e| format!("Prepare error: {}", e))?;
            let exists = stmt.exists([]).map_err(|e| format!("Query error: {}", e))?;
            if exists {
                return Err("A genesis decision already exists in the database.".to_string());
            }
        }

        // Timestamp in microseconds
        let timestamp = std::time::SystemTime::now()
            .duration_since(std::time::SystemTime::UNIX_EPOCH)
            .map_err(|e| format!("System time error: {}", e))?
            .as_micros() as u64;

        // Compute the deterministic SHA-256 hash
        let hash_bytes =
            Self::calculate_canonical_hash(&parent_id, &actor_id, timestamp, payload, rules);
        let id = hex::encode(hash_bytes);

        // Ed25519 signature of the hash
        let signature_struct = signing_key.sign(&hash_bytes);
        let signature = hex::encode(signature_struct.to_bytes());

        // Insert into SQLite
        tx.execute(
            "INSERT INTO decisions (id, parent_id, causal_depth, actor_id, timestamp, payload, rules_evaluated, signature)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
            (
                &id,
                &parent_id,
                causal_depth,
                &actor_id,
                timestamp,
                payload,
                rules,
                &signature,
            ),
        ).map_err(|e| format!("Failed to insert decision into SQLite: {}", e))?;

        tx.commit()
            .map_err(|e| format!("Failed to commit transaction: {}", e))?;

        Ok(())
    }

    fn get_latest_hash(&self) -> Result<String, String> {
        let mut stmt = self
            .conn
            .prepare("SELECT id FROM decisions ORDER BY causal_depth DESC, timestamp DESC LIMIT 1")
            .map_err(|e| format!("Failed to prepare select statement: {}", e))?;

        let mut rows = stmt
            .query([])
            .map_err(|e| format!("Failed to query database: {}", e))?;
        if let Some(row) = rows
            .next()
            .map_err(|e| format!("Error advancing row: {}", e))?
        {
            let id: String = row.get(0).unwrap();
            Ok(id)
        } else {
            Ok("GENESIS".to_string())
        }
    }

    fn export_ledger(&self) -> Result<String, String> {
        let mut stmt = self.conn.prepare(
            "SELECT id, parent_id, causal_depth, actor_id, timestamp, payload, rules_evaluated, signature
             FROM decisions
             ORDER BY causal_depth ASC, timestamp ASC"
        ).map_err(|e| format!("Failed to prepare select statement: {}", e))?;

        let rows = stmt
            .query_map([], |row| {
                Ok(Decision {
                    id: row.get(0)?,
                    parent_id: row.get(1)?,
                    causal_depth: row.get(2)?,
                    actor_id: row.get(3)?,
                    timestamp: row.get(4)?,
                    payload: row.get(5)?,
                    rules_evaluated: row.get(6)?,
                    signature: row.get(7)?,
                })
            })
            .map_err(|e| format!("Failed to execute query: {}", e))?;

        let mut decisions = Vec::new();
        for r in rows {
            match r {
                Ok(d) => decisions.push(d),
                Err(e) => return Err(format!("Error reading record: {}", e)),
            }
        }

        serde_json::to_string(&decisions)
            .map_err(|e| format!("Failed to serialize ledger to JSON: {}", e))
    }
}
// --- 3. BINDINGS PARA PYTHON (PYO3) ---
#[cfg(not(target_arch = "wasm32"))]
#[pyclass]
pub struct TempusDDB {
    storage: SqliteStorage,
    #[allow(dead_code)]
    keyfile: String,
}

#[cfg(not(target_arch = "wasm32"))]
#[pymethods]
impl TempusDDB {
    #[new]
    fn new(db_path: String, keyfile: String) -> PyResult<Self> {
        let storage = SqliteStorage::new(db_path, keyfile.clone())
            .map_err(|e| PyPermissionError::new_err(e))?;

        if std::path::Path::new(&keyfile).exists() {
            storage
                .load_signing_key()
                .map_err(PyPermissionError::new_err)?;
        }

        Ok(TempusDDB { storage, keyfile })
    }

    #[allow(unused_variables)]
    #[pyo3(signature = (payload, rules, genesis=false))]
    fn record(&mut self, payload: &str, rules: &str, genesis: bool) -> PyResult<String> {
        self.storage
            .insert_decision(payload, rules, genesis)
            .map_err(|e| PyPermissionError::new_err(e))?;

        let result_json = format!(
            r#"{{"status": "success", "action": "recorded", "latest_hash": "{}"}}"#,
            self.storage.get_latest_hash().unwrap_or_default()
        );
        Ok(result_json)
    }

    #[pyo3(signature = ())]
    fn validate(&self) -> PyResult<String> {
        self.storage
            .validate_ledger()
            .map_err(|e| PyRuntimeError::new_err(e))
    }

    #[pyo3(signature = ())]
    fn export(&self) -> PyResult<String> {
        self.storage
            .export_ledger()
            .map_err(|e| PyRuntimeError::new_err(e))
    }
}

#[cfg(not(target_arch = "wasm32"))]
#[pyfunction]
pub fn gen_keys(output: String) -> PyResult<String> {
    use ed25519_dalek::SigningKey;
    use rand::rngs::OsRng;
    use std::fs::File;
    use std::io::Write;

    #[derive(Serialize)]
    struct KeyPairConfig {
        public_key: String,
        private_key: String,
    }

    let mut csprng = OsRng;
    let signing_key = SigningKey::generate(&mut csprng);
    let verifying_key = signing_key.verifying_key();

    let config = KeyPairConfig {
        public_key: hex::encode(verifying_key.to_bytes()),
        private_key: hex::encode(signing_key.to_bytes()),
    };

    let json_str = serde_json::to_string_pretty(&config)
        .map_err(|e| PyRuntimeError::new_err(format!("Serialization error: {}", e)))?;

    let mut file = File::create(&output)
        .map_err(|e| PyIOError::new_err(format!("Error creating key file {}: {}", output, e)))?;

    file.write_all(json_str.as_bytes())
        .map_err(|e| PyIOError::new_err(format!("Error writing key file {}: {}", output, e)))?;

    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let perms = std::fs::Permissions::from_mode(0o600);
        if let Err(e) = std::fs::set_permissions(&output, perms) {
            eprintln!("Warning: Could not set key file permissions: {}", e);
        }
    }

    Ok(json_str)
}

#[cfg(not(target_arch = "wasm32"))]
#[pymodule]
fn _tempus_ddb(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<TempusDDB>()?;
    m.add_function(wrap_pyfunction!(gen_keys, m)?)?;
    Ok(())
}

// --- 4. BINDINGS PARA JAVASCRIPT (WASM-BINDGEN) ---
#[cfg(target_arch = "wasm32")]
#[wasm_bindgen]
pub struct TempusDDBWasm {
    storage: MemoryStorage,
    keyfile: String,
}

#[cfg(target_arch = "wasm32")]
#[wasm_bindgen]
impl TempusDDBWasm {
    #[wasm_bindgen(constructor)]
    pub fn new(keyfile: String) -> Result<TempusDDBWasm, JsValue> {
        let storage = MemoryStorage::new();
        Ok(TempusDDBWasm { storage, keyfile })
    }

    #[wasm_bindgen]
    pub fn record(&mut self, payload: &str, rules: &str, genesis: bool) -> Result<String, JsValue> {
        self.storage
            .insert_decision(payload, rules, genesis)
            .map_err(|e| JsValue::from_str(&e))?;

        let result_json = format!(
            r#"{{"status": "success", "action": "recorded", "latest_hash": "{}"}}"#,
            self.storage.get_latest_hash().unwrap_or_default()
        );
        Ok(result_json)
    }

    #[wasm_bindgen]
    pub fn get_ledger(&self) -> Result<String, JsValue> {
        self.storage
            .export_ledger()
            .map_err(|e| JsValue::from_str(&e))
    }
}
