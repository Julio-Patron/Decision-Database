use clap::{Parser, Subcommand};
use rusqlite::Connection;
use serde::{Deserialize, Serialize};
use std::fs::File;
use std::io::Write;
use std::time::SystemTime;

#[derive(Parser)]
#[command(name = "tempus-ddb")]
#[command(about = "Tempus DDB Core (Edge Version) - Decentralized Decision Ledger", long_about = None)]
struct Cli {
    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
enum Commands {
    /// Initialize the SQLite database schema
    Init {
        #[arg(long, default_value = "tempus_ddb.db")]
        db: String,
    },
    /// Generate a new Ed25519 cryptographic keypair
    GenKeys {
        #[arg(long, default_value = "keys.json")]
        output: String,
    },
    /// Record a new decision in the local ledger
    Record {
        #[arg(long, default_value = "tempus_ddb.db")]
        db: String,

        /// JSON payload representing inputs/outputs/decisions
        #[arg(long)]
        payload: String,

        /// JSON rules or rule ID representing the logic applied
        #[arg(long)]
        rules: String,

        /// Path to the keys.json file containing the actor's private key
        #[arg(long, default_value = "keys.json")]
        keyfile: String,

        /// ID of the parent decision. If omitted, links to the last recorded decision.
        #[arg(long)]
        parent: Option<String>,

        /// Forces recording this as a genesis decision (no parent required)
        #[arg(long)]
        genesis: bool,
    },
    /// Walk the database to verify the integrity and cryptographic authenticity of the chain
    Validate {
        #[arg(long, default_value = "tempus_ddb.db")]
        db: String,
    },
    /// List recorded decisions in chronological order
    List {
        #[arg(long, default_value = "tempus_ddb.db")]
        db: String,

        #[arg(long)]
        limit: Option<usize>,
    },
    /// Export all decisions as a JSON array for cloud synchronization
    Export {
        #[arg(long, default_value = "tempus_ddb.db")]
        db: String,
    },
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct Decision {
    pub id: String,
    pub parent_id: String,
    pub causal_depth: u64,
    pub actor_id: String,
    pub timestamp: u64,
    pub payload: String,
    pub rules_evaluated: String,
    pub signature: String,
}

#[derive(Serialize, Deserialize)]
struct KeyPairConfig {
    public_key: String,
    private_key: String,
}

fn calculate_canonical_hash(
    parent_id: &str,
    actor_id: &str,
    timestamp: u64,
    payload: &str,
    rules_evaluated: &str,
) -> [u8; 32] {
    use sha2::{Digest, Sha256};

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

fn verify_decision_signature(d: &Decision) -> Result<(), String> {
    use ed25519_dalek::{Signature, Verifier, VerifyingKey};

    let pub_bytes = hex::decode(&d.actor_id).map_err(|e| format!("Invalid actor_id hex: {}", e))?;
    let pub_array: [u8; 32] = pub_bytes
        .try_into()
        .map_err(|_| "Actor public key must be exactly 32 bytes")?;
    let verifying_key =
        VerifyingKey::from_bytes(&pub_array).map_err(|e| format!("Invalid public key: {}", e))?;

    let sig_bytes =
        hex::decode(&d.signature).map_err(|e| format!("Invalid signature hex: {}", e))?;
    let sig_array: [u8; 64] = sig_bytes
        .try_into()
        .map_err(|_| "Signature must be exactly 64 bytes")?;
    let signature = Signature::from_bytes(&sig_array);

    let id_bytes = hex::decode(&d.id).map_err(|e| format!("Invalid id hex: {}", e))?;

    verifying_key
        .verify(&id_bytes, &signature)
        .map_err(|e| format!("Cryptographic signature is invalid: {}", e))?;

    Ok(())
}

fn init_db(db_path: &str) -> Result<Connection, String> {
    let conn =
        Connection::open(db_path).map_err(|e| format!("Failed to open SQLite database: {}", e))?;

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

    Ok(conn)
}

fn get_last_decision(conn: &Connection) -> Result<Option<Decision>, String> {
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

fn get_decision(conn: &Connection, id: &str) -> Result<Option<Decision>, String> {
    let mut stmt = conn.prepare(
        "SELECT id, parent_id, causal_depth, actor_id, timestamp, payload, rules_evaluated, signature 
         FROM decisions 
         WHERE id = ?1"
    ).map_err(|e| format!("Failed to prepare select statement: {}", e))?;

    let mut rows = stmt
        .query([id])
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

fn insert_decision(conn: &Connection, d: &Decision) -> Result<(), String> {
    conn.execute(
        "INSERT INTO decisions (id, parent_id, causal_depth, actor_id, timestamp, payload, rules_evaluated, signature) 
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
        (
            &d.id,
            &d.parent_id,
            d.causal_depth,
            &d.actor_id,
            d.timestamp,
            &d.payload,
            &d.rules_evaluated,
            &d.signature,
        ),
    ).map_err(|e| format!("Failed to insert decision into SQLite: {}", e))?;
    Ok(())
}

fn load_keypair(path: &str) -> Result<ed25519_dalek::SigningKey, String> {
    let content = std::fs::read_to_string(path)
        .map_err(|e| format!("Failed to read key file {}: {}", path, e))?;
    let config: KeyPairConfig = serde_json::from_str(&content)
        .map_err(|e| format!("Failed to parse key file JSON: {}", e))?;

    let private_key_bytes =
        hex::decode(&config.private_key).map_err(|e| format!("Invalid private key hex: {}", e))?;
    let private_key_array: [u8; 32] = private_key_bytes
        .try_into()
        .map_err(|_| "Private key must be exactly 32 bytes")?;

    let signing_key = ed25519_dalek::SigningKey::from_bytes(&private_key_array);
    Ok(signing_key)
}

fn main() {
    let cli = Cli::parse();

    match cli.command {
        Commands::Init { db } => match init_db(&db) {
            Ok(_) => {
                eprintln!("Database initialized successfully: {}", db);
            }
            Err(e) => {
                eprintln!("Error: {}", e);
                std::process::exit(1);
            }
        },
        Commands::GenKeys { output } => {
            use rand::rngs::OsRng;
            let mut csprng = OsRng;
            let signing_key = ed25519_dalek::SigningKey::generate(&mut csprng);
            let verifying_key = signing_key.verifying_key();

            let config = KeyPairConfig {
                public_key: hex::encode(verifying_key.to_bytes()),
                private_key: hex::encode(signing_key.to_bytes()),
            };

            let json_str = serde_json::to_string_pretty(&config).unwrap();
            let mut file = match File::create(&output) {
                Ok(f) => f,
                Err(e) => {
                    eprintln!("Error creating key file {}: {}", output, e);
                    std::process::exit(1);
                }
            };

            if let Err(e) = file.write_all(json_str.as_bytes()) {
                eprintln!("Error writing key file: {}", e);
                std::process::exit(1);
            }

            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                let perms = std::fs::Permissions::from_mode(0o600);
                if let Err(e) = std::fs::set_permissions(&output, perms) {
                    eprintln!("Warning: Could not set key file permissions: {}", e);
                }
            }

            eprintln!("Cryptographic keys generated and saved to: {}", output);
            println!(
                "{}",
                serde_json::to_string(&serde_json::json!({
                    "public_key": config.public_key
                }))
                .unwrap()
            );
        }
        Commands::Record {
            db,
            payload,
            rules,
            keyfile,
            parent,
            genesis,
        } => {
            // Validate payload and rules are valid JSON
            if let Err(e) = serde_json::from_str::<serde_json::Value>(&payload) {
                eprintln!("Error: 'payload' is not a valid JSON string: {}", e);
                std::process::exit(1);
            }
            if let Err(e) = serde_json::from_str::<serde_json::Value>(&rules) {
                eprintln!("Error: 'rules' is not a valid JSON string: {}", e);
                std::process::exit(1);
            }

            // Load credentials
            let signing_key = match load_keypair(&keyfile) {
                Ok(k) => k,
                Err(e) => {
                    eprintln!("Error loading credentials: {}", e);
                    std::process::exit(1);
                }
            };
            let verifying_key = signing_key.verifying_key();
            let actor_id = hex::encode(verifying_key.to_bytes());

            // Open db
            let mut conn = match init_db(&db) {
                Ok(c) => c,
                Err(e) => {
                    eprintln!("Error: {}", e);
                    std::process::exit(1);
                }
            };

            let tx = match conn.transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)
            {
                Ok(t) => t,
                Err(e) => {
                    eprintln!("Error starting transaction: {}", e);
                    std::process::exit(1);
                }
            };

            // Resolve parent_id and causal_depth
            let (parent_id, causal_depth) = if let Some(p_id) = parent {
                // Verify parent actually exists
                match get_decision(&tx, &p_id) {
                    Ok(Some(parent_d)) => (p_id, parent_d.causal_depth + 1),
                    Ok(None) => {
                        eprintln!("Error: Parent decision '{}' not found in database", p_id);
                        std::process::exit(1);
                    }
                    Err(e) => {
                        eprintln!("Error checking parent decision: {}", e);
                        std::process::exit(1);
                    }
                }
            } else {
                // Query last decision
                match get_last_decision(&tx) {
                    Ok(Some(last_d)) => {
                        if genesis {
                            ("genesis".to_string(), 0)
                        } else {
                            (last_d.id, last_d.causal_depth + 1)
                        }
                    }
                    Ok(None) => {
                        if genesis {
                            ("genesis".to_string(), 0)
                        } else {
                            eprintln!("Error: Database is empty. Pass --genesis to record the first decision, or specify --parent.");
                            std::process::exit(1);
                        }
                    }
                    Err(e) => {
                        eprintln!("Error querying last decision: {}", e);
                        std::process::exit(1);
                    }
                }
            };

            if parent_id == "genesis" {
                let mut stmt = tx
                    .prepare("SELECT 1 FROM decisions WHERE parent_id = 'genesis' LIMIT 1")
                    .unwrap();
                let exists = stmt.exists([]).unwrap_or(false);
                if exists {
                    eprintln!("Error: A genesis decision already exists in the database.");
                    std::process::exit(1);
                }
            }

            // Timestamp in microseconds
            let timestamp = SystemTime::now()
                .duration_since(SystemTime::UNIX_EPOCH)
                .unwrap()
                .as_micros() as u64;

            // Generate deterministic SHA-256 hash
            let hash_bytes =
                calculate_canonical_hash(&parent_id, &actor_id, timestamp, &payload, &rules);
            let id = hex::encode(hash_bytes);

            // Cryptographic signing of the ID hash
            use ed25519_dalek::Signer;
            let signature_struct = signing_key.sign(&hash_bytes);
            let signature = hex::encode(signature_struct.to_bytes());

            let decision = Decision {
                id,
                parent_id,
                causal_depth,
                actor_id,
                timestamp,
                payload,
                rules_evaluated: rules,
                signature,
            };

            // Insert into local SQLite
            if let Err(e) = insert_decision(&tx, &decision) {
                eprintln!("Error saving decision: {}", e);
                std::process::exit(1);
            }

            if let Err(e) = tx.commit() {
                eprintln!("Error committing transaction: {}", e);
                std::process::exit(1);
            }

            // Print the newly created decision as a JSON string to stdout
            println!("{}", serde_json::to_string(&decision).unwrap());
        }
        Commands::Validate { db } => {
            let conn = match init_db(&db) {
                Ok(c) => c,
                Err(e) => {
                    eprintln!("Error opening database: {}", e);
                    std::process::exit(1);
                }
            };

            // Read all decisions sorted causally
            let mut stmt = match conn.prepare(
                "SELECT id, parent_id, causal_depth, actor_id, timestamp, payload, rules_evaluated, signature 
                 FROM decisions 
                 ORDER BY causal_depth ASC, timestamp ASC"
            ) {
                Ok(s) => s,
                Err(e) => {
                    eprintln!("Failed to prepare select statement: {}", e);
                    std::process::exit(1);
                }
            };

            let rows = match stmt.query_map([], |row| {
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
            }) {
                Ok(r) => r,
                Err(e) => {
                    eprintln!("Failed to execute query: {}", e);
                    std::process::exit(1);
                }
            };

            let mut decisions = Vec::new();
            for r in rows {
                match r {
                    Ok(d) => decisions.push(d),
                    Err(e) => {
                        eprintln!("Error reading record: {}", e);
                        std::process::exit(1);
                    }
                }
            }

            if decisions.is_empty() {
                println!(
                    "{}",
                    serde_json::to_string(&serde_json::json!({
                        "status": "valid",
                        "message": "Database is empty.",
                        "total_records": 0
                    }))
                    .unwrap()
                );
                return;
            }

            use std::collections::HashMap;
            let mut decision_map = HashMap::new();
            for d in &decisions {
                decision_map.insert(d.id.clone(), d.clone());
            }

            let mut errors = Vec::new();

            for d in &decisions {
                // A. Validate hash integrity
                let computed_hash = calculate_canonical_hash(
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

                // B. Validate cryptographic signature
                if let Err(e) = verify_decision_signature(d) {
                    errors.push(format!(
                        "Decision '{}' signature verification failed: {}",
                        d.id, e
                    ));
                    continue;
                }

                // C. Validate causal parent link
                if d.parent_id != "genesis" {
                    match decision_map.get(&d.parent_id) {
                        Some(parent) => {
                            // D. Validate causal depth progression
                            if d.causal_depth != parent.causal_depth + 1 {
                                errors.push(format!(
                                    "Decision '{}' causal depth mismatch. Expected '{}' (Parent depth + 1), found '{}'",
                                    d.id, parent.causal_depth + 1, d.causal_depth
                                ));
                            }
                            // E. Validate temporal monotonicity
                            if d.timestamp < parent.timestamp {
                                errors.push(format!(
                                    "Decision '{}' timestamp temporal anomaly. Precedes parent '{}' (Self: {}, Parent: {})",
                                    d.id, d.parent_id, d.timestamp, parent.timestamp
                                ));
                            }
                        }
                        None => {
                            errors.push(format!(
                                "Decision '{}' orphan node. Parent '{}' is missing from the database",
                                d.id, d.parent_id
                            ));
                        }
                    }
                } else {
                    // Genesis verification
                    if d.causal_depth != 0 {
                        errors.push(format!(
                            "Genesis decision '{}' must have causal_depth 0, found '{}'",
                            d.id, d.causal_depth
                        ));
                    }
                }
            }

            if errors.is_empty() {
                println!(
                    "{}",
                    serde_json::to_string(&serde_json::json!({
                        "status": "valid",
                        "message": "All decisions verified successfully.",
                        "total_records": decisions.len()
                    }))
                    .unwrap()
                );
            } else {
                println!(
                    "{}",
                    serde_json::to_string(&serde_json::json!({
                        "status": "invalid",
                        "errors": errors,
                        "total_records": decisions.len()
                    }))
                    .unwrap()
                );
                std::process::exit(1);
            }
        }
        Commands::List { db, limit } => {
            let conn = match init_db(&db) {
                Ok(c) => c,
                Err(e) => {
                    eprintln!("Error opening database: {}", e);
                    std::process::exit(1);
                }
            };

            let query = "SELECT id, parent_id, causal_depth, actor_id, timestamp, payload, rules_evaluated, signature 
                         FROM decisions 
                         ORDER BY causal_depth DESC, timestamp DESC 
                         LIMIT ?1";

            let mut stmt = match conn.prepare(query) {
                Ok(s) => s,
                Err(e) => {
                    eprintln!("Failed to prepare select statement: {}", e);
                    std::process::exit(1);
                }
            };

            let limit_val = limit.map(|l| l as i64).unwrap_or(-1);

            let rows = match stmt.query_map([limit_val], |row| {
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
            }) {
                Ok(r) => r,
                Err(e) => {
                    eprintln!("Failed to execute query: {}", e);
                    std::process::exit(1);
                }
            };

            let mut decisions = Vec::new();
            for r in rows {
                match r {
                    Ok(d) => decisions.push(d),
                    Err(e) => {
                        eprintln!("Error reading record: {}", e);
                        std::process::exit(1);
                    }
                }
            }

            println!("{}", serde_json::to_string(&decisions).unwrap());
        }
        Commands::Export { db } => {
            let conn = match init_db(&db) {
                Ok(c) => c,
                Err(e) => {
                    eprintln!("Error opening database: {}", e);
                    std::process::exit(1);
                }
            };

            let mut stmt = match conn.prepare(
                "SELECT id, parent_id, causal_depth, actor_id, timestamp, payload, rules_evaluated, signature 
                 FROM decisions 
                 ORDER BY causal_depth ASC, timestamp ASC"
            ) {
                Ok(s) => s,
                Err(e) => {
                    eprintln!("Failed to prepare select statement: {}", e);
                    std::process::exit(1);
                }
            };

            let rows = match stmt.query_map([], |row| {
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
            }) {
                Ok(r) => r,
                Err(e) => {
                    eprintln!("Failed to execute query: {}", e);
                    std::process::exit(1);
                }
            };

            let mut decisions = Vec::new();
            for r in rows {
                match r {
                    Ok(d) => decisions.push(d),
                    Err(e) => {
                        eprintln!("Error reading record: {}", e);
                        std::process::exit(1);
                    }
                }
            }

            println!("{}", serde_json::to_string(&decisions).unwrap());
        }
    }
}
