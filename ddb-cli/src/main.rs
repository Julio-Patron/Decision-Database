use clap::{Parser, Subcommand};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::io::{self, Read};

#[derive(Parser)]
#[command(name = "ddb")]
#[command(about = "Decision Database — unified CLI", long_about = None)]
struct Cli {
    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
enum Commands {
    /// Ledger operations (tamper-evident decision chain)
    Ledger {
        #[command(subcommand)]
        action: LedgerAction,
    },
    /// Rule evaluation (JSON-Logic engine)
    Rule {
        #[command(subcommand)]
        action: RuleAction,
    },
    /// On-chain synchronization (Base/EVM)
    Chain {
        #[command(subcommand)]
        action: ChainAction,
    },
    /// Vector search operations
    Vector {
        #[command(subcommand)]
        action: VectorAction,
    },
}

#[derive(Subcommand)]
enum LedgerAction {
    /// Initialize the SQLite database schema
    Init {
        #[arg(long, default_value = "ddb_ledger.db")]
        db: String,
    },
    /// Generate a new Ed25519 cryptographic keypair
    GenKeys {
        #[arg(long, default_value = "keys.json")]
        output: String,
    },
    /// Record a new decision in the local ledger
    Record {
        #[arg(long, default_value = "ddb_ledger.db")]
        db: String,
        #[arg(long)]
        payload: String,
        #[arg(long)]
        rules: String,
        #[arg(long, default_value = "keys.json")]
        keyfile: String,
        /// ID of the parent decision. If omitted, links to the last recorded.
        #[arg(long)]
        parent: Option<String>,
        /// Force recording as genesis (no parent required)
        #[arg(long)]
        genesis: bool,
    },
    /// Walk the database to verify chain integrity
    Validate {
        #[arg(long, default_value = "ddb_ledger.db")]
        db: String,
    },
    /// List recorded decisions
    List {
        #[arg(long, default_value = "ddb_ledger.db")]
        db: String,
        #[arg(long)]
        limit: Option<usize>,
    },
    /// Export all decisions as JSON
    Export {
        #[arg(long, default_value = "ddb_ledger.db")]
        db: String,
    },
}

#[derive(Subcommand)]
enum RuleAction {
    /// Evaluate a rule against a context (JSON strings)
    Eval {
        #[arg(long)]
        rule: String,
        #[arg(long)]
        context: String,
    },
    /// Evaluate via stdin/stdout bridge (compatible with engine_1_bridge)
    Bridge {
        /// Read a JSON input from stdin with rule + context fields
        #[arg(long, default_value_t = false)]
        stdin: bool,
    },
    /// Validate a rule against a default context
    Validate {
        #[arg(long)]
        rule: String,
    },
}

#[derive(Subcommand)]
enum ChainAction {
    /// Sync deposit events from the blockchain (EVM / Base)
    Sync {
        #[arg(long, default_value = "false")]
        use_dynamodb: bool,
        #[arg(long)]
        balances_table: Option<String>,
        #[arg(long)]
        nonces_table: Option<String>,
    },
    /// Slash on-chain balances that exceed off-chain records
    Slasher {
        #[arg(long, default_value = "false")]
        use_dynamodb: bool,
        #[arg(long)]
        balances_table: Option<String>,
        #[arg(long)]
        nonces_table: Option<String>,
    },
}

#[derive(Subcommand)]
enum VectorAction {
    /// Search (placeholder — use Python SDK for full RAG)
    Search {
        #[arg(long)]
        query: String,
        #[arg(long, default_value_t = 10)]
        top_k: usize,
    },
}

fn main() {
    let cli = Cli::parse();

    match cli.command {
        Commands::Ledger { action } => handle_ledger(action),
        Commands::Rule { action } => handle_rule(action),
        Commands::Chain { action } => handle_chain(action),
        Commands::Vector { action } => handle_vector(action),
    }
}

// ─── Ledger ────────────────────────────────────────────────────────────────

fn handle_ledger(action: LedgerAction) {
    match action {
        LedgerAction::Init { db } => {
            ddb_ledger_init(&db);
        }
        LedgerAction::GenKeys { output } => {
            ddb_ledger_gen_keys(&output);
        }
        LedgerAction::Record {
            db,
            payload,
            rules,
            keyfile,
            parent: _,
            genesis,
        } => {
            ddb_ledger_record(&db, &payload, &rules, &keyfile, genesis);
        }
        LedgerAction::Validate { db } => {
            ddb_ledger_validate(&db);
        }
        LedgerAction::List { db, limit } => {
            ddb_ledger_list(&db, limit);
        }
        LedgerAction::Export { db } => {
            ddb_ledger_export(&db);
        }
    }
}

fn ddb_ledger_init(db: &str) {
    use rusqlite::Connection;
    let conn = Connection::open(db).expect("Failed to open database");
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS decisions (
            id TEXT PRIMARY KEY,
            parent_id TEXT NOT NULL,
            causal_depth INTEGER NOT NULL,
            actor_id TEXT NOT NULL,
            timestamp INTEGER NOT NULL,
            payload TEXT NOT NULL,
            rules_evaluated TEXT NOT NULL,
            signature TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_parent_id ON decisions (parent_id);
        CREATE INDEX IF NOT EXISTS idx_causal_depth ON decisions (causal_depth);",
    )
    .expect("Failed to create schema");
    eprintln!("Database initialized: {}", db);
}

fn ddb_ledger_gen_keys(output: &str) {
    use ed25519_dalek::SigningKey;
    use rand::rngs::OsRng;
    use std::fs::File;
    use std::io::Write;

    let mut csprng = OsRng;
    let signing_key = SigningKey::generate(&mut csprng);
    let verifying_key = signing_key.verifying_key();

    let key_json = serde_json::json!({
        "public_key": hex::encode(verifying_key.to_bytes()),
        "private_key": hex::encode(signing_key.to_bytes()),
    });

    let json_str = serde_json::to_string_pretty(&key_json).unwrap();
    let mut file = File::create(output).expect("Failed to create key file");
    file.write_all(json_str.as_bytes())
        .expect("Failed to write key file");

    #[cfg(unix)]
    {
        use std::fs::Permissions;
        use std::os::unix::fs::PermissionsExt;
        let perms = Permissions::from_mode(0o600);
        std::fs::set_permissions(output, perms).ok();
    }

    eprintln!("Keys generated and saved to: {}", output);
    println!(
        "{}",
        serde_json::to_string(&serde_json::json!({"public_key": key_json["public_key"]})).unwrap()
    );
}

fn ddb_ledger_record(db: &str, payload: &str, rules: &str, keyfile: &str, genesis: bool) {
    use ed25519_dalek::Signer;
    use rusqlite::Connection;
    use std::time::SystemTime;

    let signing_key = load_keypair(keyfile);
    let verifying_key = signing_key.verifying_key();
    let actor_id = hex::encode(verifying_key.to_bytes());

    let mut conn = Connection::open(db).expect("Failed to open database");

    let tx = conn
        .transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)
        .expect("Failed to start transaction");

    let (parent_id, causal_depth) = match get_last_decision_conn(&tx) {
        Ok(Some(last)) => {
            if genesis {
                ("genesis".to_string(), 0u64)
            } else {
                (last.id, last.causal_depth + 1)
            }
        }
        Ok(None) => {
            if genesis {
                ("genesis".to_string(), 0)
            } else {
                eprintln!("Error: Database is empty. Use --genesis for first decision.");
                std::process::exit(1);
            }
        }
        Err(e) => {
            eprintln!("Error: {}", e);
            std::process::exit(1);
        }
    };

    if parent_id == "genesis" {
        let exists: bool = tx
            .prepare("SELECT 1 FROM decisions WHERE parent_id = 'genesis' LIMIT 1")
            .unwrap()
            .exists([])
            .unwrap_or(false);
        if exists {
            eprintln!("Error: A genesis decision already exists.");
            std::process::exit(1);
        }
    }

    let timestamp = SystemTime::now()
        .duration_since(SystemTime::UNIX_EPOCH)
        .unwrap()
        .as_micros() as u64;

    let hash_bytes = calculate_canonical_hash(&parent_id, &actor_id, timestamp, payload, rules);
    let id = hex::encode(hash_bytes);
    let signature = hex::encode(signing_key.sign(&hash_bytes).to_bytes());

    let decision = serde_json::json!({
        "id": id,
        "parent_id": parent_id,
        "causal_depth": causal_depth,
        "actor_id": actor_id,
        "timestamp": timestamp,
        "payload": payload,
        "rules_evaluated": rules,
        "signature": signature,
    });

    let id = decision["id"].as_str().unwrap_or_default();
    let parent_id = decision["parent_id"].as_str().unwrap_or_default();
    let actor_id = decision["actor_id"].as_str().unwrap_or_default();
    let signature = decision["signature"].as_str().unwrap_or_default();
    tx.execute(
        "INSERT INTO decisions (id, parent_id, causal_depth, actor_id, timestamp, payload, rules_evaluated, signature)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
        rusqlite::params![
            id, parent_id, causal_depth,
            actor_id, timestamp, payload, rules, signature
        ],
    )
    .expect("Failed to insert decision");

    tx.commit().expect("Failed to commit");
    println!("{}", serde_json::to_string(&decision).unwrap());
}

fn ddb_ledger_validate(db: &str) {
    use ed25519_dalek::{Signature, Verifier, VerifyingKey};
    use rusqlite::Connection;
    use std::collections::HashMap;

    let conn = Connection::open(db).expect("Failed to open database");

    #[derive(Serialize, Deserialize, Clone)]
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

    let mut stmt = conn.prepare(
        "SELECT id, parent_id, causal_depth, actor_id, timestamp, payload, rules_evaluated, signature
         FROM decisions ORDER BY causal_depth ASC, timestamp ASC",
    )
    .unwrap();

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
        .unwrap();

    let decisions: Vec<Decision> = rows.filter_map(|r| r.ok()).collect();

    if decisions.is_empty() {
        println!(
            "{}",
            serde_json::to_string(&serde_json::json!({
                "status": "valid", "message": "Database is empty.", "total_records": 0
            }))
            .unwrap()
        );
        return;
    }

    let map: HashMap<&str, &Decision> = decisions.iter().map(|d| (d.id.as_str(), d)).collect();
    let mut errors = Vec::new();

    for d in &decisions {
        let h = calculate_canonical_hash(
            &d.parent_id,
            &d.actor_id,
            d.timestamp,
            &d.payload,
            &d.rules_evaluated,
        );
        let computed = hex::encode(h);
        if computed != d.id {
            errors.push(format!("Decision '{}' has invalid hash", d.id));
            continue;
        }

        let pk_bytes = hex::decode(&d.actor_id).unwrap_or_default();
        let pk_arr: [u8; 32] = match pk_bytes.try_into() {
            Ok(a) => a,
            Err(_) => {
                errors.push(format!("Invalid public key for '{}'", d.id));
                continue;
            }
        };
        let vk = match VerifyingKey::from_bytes(&pk_arr) {
            Ok(k) => k,
            Err(_) => {
                errors.push(format!("Invalid verifying key for '{}'", d.id));
                continue;
            }
        };
        let sig_bytes = hex::decode(&d.signature).unwrap_or_default();
        let sig_arr: [u8; 64] = match sig_bytes.try_into() {
            Ok(a) => a,
            Err(_) => {
                errors.push(format!("Invalid signature size for '{}'", d.id));
                continue;
            }
        };
        let sig = Signature::from_bytes(&sig_arr);
        let id_bytes = hex::decode(&d.id).unwrap_or_default();
        if vk.verify(&id_bytes, &sig).is_err() {
            errors.push(format!("Signature invalid for '{}'", d.id));
        }

        if d.parent_id != "genesis" {
            match map.get(d.parent_id.as_str()) {
                Some(p) => {
                    if d.causal_depth != p.causal_depth + 1 {
                        errors.push(format!("Depth mismatch for '{}'", d.id));
                    }
                    if d.timestamp < p.timestamp {
                        errors.push(format!("Temporal anomaly for '{}'", d.id));
                    }
                }
                None => errors.push(format!("Orphan '{}'", d.id)),
            }
        } else if d.causal_depth != 0 {
            errors.push(format!("Genesis '{}' depth must be 0", d.id));
        }
    }

    if errors.is_empty() {
        println!("{}", serde_json::to_string(&serde_json::json!({
            "status": "valid", "message": "All decisions verified.", "total_records": decisions.len()
        })).unwrap());
    } else {
        println!(
            "{}",
            serde_json::to_string(&serde_json::json!({
                "status": "invalid", "errors": errors, "total_records": decisions.len()
            }))
            .unwrap()
        );
        std::process::exit(1);
    }
}

fn ddb_ledger_list(db: &str, limit: Option<usize>) {
    use rusqlite::Connection;
    let conn = Connection::open(db).expect("Failed to open database");
    let limit_val = limit.map(|l| l as i64).unwrap_or(-1);

    let mut stmt = conn
        .prepare(
            "SELECT id, parent_id, causal_depth, actor_id, timestamp, payload, rules_evaluated, signature
             FROM decisions ORDER BY causal_depth DESC, timestamp DESC LIMIT ?1",
        )
        .unwrap();

    #[derive(Serialize)]
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

    let rows = stmt
        .query_map([limit_val], |row| {
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
        .unwrap();

    let decisions: Vec<Decision> = rows.filter_map(|r| r.ok()).collect();
    println!("{}", serde_json::to_string(&decisions).unwrap());
}

fn ddb_ledger_export(db: &str) {
    use rusqlite::Connection;
    let conn = Connection::open(db).expect("Failed to open database");

    #[derive(Serialize)]
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

    let mut stmt = conn
        .prepare(
            "SELECT id, parent_id, causal_depth, actor_id, timestamp, payload, rules_evaluated, signature
             FROM decisions ORDER BY causal_depth ASC, timestamp ASC",
        )
        .unwrap();

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
        .unwrap();

    let decisions: Vec<Decision> = rows.filter_map(|r| r.ok()).collect();
    println!("{}", serde_json::to_string(&decisions).unwrap());
}

// ─── Shared ledger helpers ─────────────────────────────────────────────────

#[derive(Serialize, Deserialize, Debug, Clone)]
struct DecisionRecord {
    id: String,
    parent_id: String,
    causal_depth: u64,
    actor_id: String,
    timestamp: u64,
    payload: String,
    rules_evaluated: String,
    signature: String,
}

fn load_keypair(path: &str) -> ed25519_dalek::SigningKey {
    use std::fs;

    #[derive(Deserialize)]
    #[allow(dead_code)]
    struct KeyFile {
        public_key: String,
        private_key: String,
    }

    let content = fs::read_to_string(path).expect("Failed to read key file");
    let kf: KeyFile = serde_json::from_str(&content).expect("Invalid key file JSON");
    let bytes = hex::decode(&kf.private_key).expect("Invalid private key hex");
    let arr: [u8; 32] = bytes.try_into().expect("Private key must be 32 bytes");
    ed25519_dalek::SigningKey::from_bytes(&arr)
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
    struct Canonical<'a> {
        parent_id: &'a str,
        actor_id: &'a str,
        timestamp: u64,
        payload: Value,
        rules_evaluated: Value,
    }

    let canonical = Canonical {
        parent_id,
        actor_id,
        timestamp,
        payload: serde_json::from_str(payload).unwrap_or(Value::Null),
        rules_evaluated: serde_json::from_str(rules_evaluated).unwrap_or(Value::Null),
    };

    let bytes = serde_json::to_vec(&canonical).unwrap();
    let mut hasher = Sha256::new();
    hasher.update(&bytes);
    hasher.finalize().into()
}

fn get_last_decision_conn(conn: &rusqlite::Connection) -> Result<Option<DecisionRecord>, String> {
    let mut stmt = conn
        .prepare(
            "SELECT id, parent_id, causal_depth, actor_id, timestamp, payload, rules_evaluated, signature
             FROM decisions ORDER BY causal_depth DESC, timestamp DESC LIMIT 1",
        )
        .map_err(|e| format!("Prepare error: {}", e))?;

    let mut rows = stmt.query([]).map_err(|e| format!("Query error: {}", e))?;
    if let Some(row) = rows.next().map_err(|e| format!("Row error: {}", e))? {
        Ok(Some(DecisionRecord {
            id: row.get(0).unwrap(),
            parent_id: row.get(1).unwrap(),
            causal_depth: row.get(2).unwrap(),
            actor_id: row.get(3).unwrap(),
            timestamp: row.get(4).unwrap(),
            payload: row.get(5).unwrap(),
            rules_evaluated: row.get(6).unwrap(),
            signature: row.get(7).unwrap(),
        }))
    } else {
        Ok(None)
    }
}

// ─── Rules ─────────────────────────────────────────────────────────────────

fn handle_rule(action: RuleAction) {
    match action {
        RuleAction::Eval { rule, context } => match ddb_sdk::evaluate(&rule, &context) {
            Ok(result) => println!("{}", result),
            Err(e) => {
                eprintln!("Error: {}", e);
                std::process::exit(1);
            }
        },
        RuleAction::Bridge { stdin } => {
            if stdin {
                rule_bridge_stdin();
            } else {
                eprintln!("Use --stdin to read from stdin, or use `ddb rule eval`");
                std::process::exit(1);
            }
        }
        RuleAction::Validate { rule } => match ddb_sdk::validate_rule(&rule) {
            Ok(true) => println!(r#"{{"status":"valid"}}"#),
            Ok(false) => {
                eprintln!("Rule validation failed");
                std::process::exit(1);
            }
            Err(e) => {
                eprintln!("Error: {}", e);
                std::process::exit(1);
            }
        },
    }
}

fn rule_bridge_stdin() {
    let mut input = String::new();
    io::stdin()
        .read_to_string(&mut input)
        .expect("stdin read error");

    #[derive(Deserialize)]
    struct BridgeInput {
        rule: ddb_sdk::RuleDefinition,
        context: Value,
    }

    let input: BridgeInput = match serde_json::from_str(&input) {
        Ok(v) => v,
        Err(e) => {
            let out = serde_json::json!({
                "success": false,
                "error": format!("Parse error: {}", e)
            });
            println!("{}", out);
            return;
        }
    };

    let context_str = input.context.to_string();
    match ddb_sdk::execute_explain(&input.rule, &context_str) {
        Ok(explain) => {
            let out = serde_json::json!({
                "success": true,
                "result": explain.result,
                "explain_tree": explain,
                "logic_snapshot": explain.logic_snapshot,
                "error": null
            });
            println!("{}", out);
        }
        Err(e) => {
            let out = serde_json::json!({
                "success": false,
                "error": format!("Evaluation error: {:?}", e)
            });
            println!("{}", out);
        }
    }
}

// ─── Chain ─────────────────────────────────────────────────────────────────

fn handle_chain(action: ChainAction) {
    match action {
        ChainAction::Sync {
            use_dynamodb,
            balances_table,
            nonces_table,
        } => {
            let rt = tokio::runtime::Runtime::new().unwrap();
            rt.block_on(chain_sync(use_dynamodb, balances_table, nonces_table));
        }
        ChainAction::Slasher {
            use_dynamodb,
            balances_table,
            nonces_table,
        } => {
            let rt = tokio::runtime::Runtime::new().unwrap();
            rt.block_on(chain_slasher(use_dynamodb, balances_table, nonces_table));
        }
    }
}

async fn chain_sync(
    use_dynamodb: bool,
    balances_table: Option<String>,
    nonces_table: Option<String>,
) {
    let bt = balances_table.unwrap_or_else(|| "B2A_Balances".to_string());
    let nt = nonces_table.unwrap_or_else(|| "B2A_Nonces".to_string());

    let storage: std::sync::Arc<ddb_sdk::StorageBackend> = if use_dynamodb {
        std::sync::Arc::new(ddb_sdk::StorageBackend::Dynamo(
            ddb_sdk::DynamoStorage::new(&bt, &nt).await,
        ))
    } else {
        std::sync::Arc::new(ddb_sdk::StorageBackend::Memory(
            ddb_sdk::MemoryStorage::new(),
        ))
    };

    let config = ddb_sdk::BlockchainConfig::from_env();
    let provider = config.read_provider();

    let mut last_block = storage.get_last_sync_block().await.unwrap_or_default();

    let latest =
        match ddb_sdk::retry_with_backoff(|| async { provider.get_block_number().await }, 3, 500)
            .await
        {
            Ok(n) => n,
            Err(_) => {
                eprintln!("Failed to get latest block");
                return;
            }
        };

    if last_block == 0 {
        last_block = latest.saturating_sub(200);
    }

    let from_block = last_block + 1;
    let to_block = std::cmp::min(latest, from_block + 1999);

    if to_block < from_block {
        eprintln!("No new blocks to sync");
        return;
    }

    use alloy::providers::Provider;
    use alloy::rpc::types::eth::Filter;
    use alloy::sol_types::SolEvent;

    let filter = Filter::new()
        .address(config.contract_address)
        .from_block(from_block)
        .to_block(to_block)
        .event_signature(ddb_sdk::B2AStaking::Deposited::SIGNATURE_HASH);

    let logs =
        match ddb_sdk::retry_with_backoff(|| async { provider.get_logs(&filter).await }, 3, 500)
            .await
        {
            Ok(l) => l,
            Err(_) => {
                eprintln!("Failed to fetch logs");
                return;
            }
        };

    let mut highest = last_block;
    for log_entry in logs {
        if let Ok(decoded) = log_entry.log_decode::<ddb_sdk::B2AStaking::Deposited>() {
            let event = decoded.inner.data;
            if let Err(e) = storage.add_balance(&event.user, event.amount).await {
                eprintln!("Failed to add balance: {:?}", e);
            }
            if let Some(block) = log_entry.block_number {
                if block > highest {
                    highest = block;
                }
            }
        }
    }

    if highest > last_block {
        storage.set_last_sync_block(highest).await.ok();
    }

    eprintln!("Sync complete. Processed up to block {}", highest);
}

async fn chain_slasher(
    use_dynamodb: bool,
    balances_table: Option<String>,
    nonces_table: Option<String>,
) {
    let bt = balances_table.unwrap_or_else(|| "B2A_Balances".to_string());
    let nt = nonces_table.unwrap_or_else(|| "B2A_Nonces".to_string());

    let storage: std::sync::Arc<ddb_sdk::StorageBackend> = if use_dynamodb {
        std::sync::Arc::new(ddb_sdk::StorageBackend::Dynamo(
            ddb_sdk::DynamoStorage::new(&bt, &nt).await,
        ))
    } else {
        std::sync::Arc::new(ddb_sdk::StorageBackend::Memory(
            ddb_sdk::MemoryStorage::new(),
        ))
    };

    let config = ddb_sdk::BlockchainConfig::from_env_with_secret_fallback().await;
    let provider = config.write_provider();
    let contract = ddb_sdk::B2AStaking::new(config.contract_address, provider.clone());

    let all_balances = match storage.get_all_balances().await {
        Ok(b) => b,
        Err(e) => {
            eprintln!("Failed to get balances: {:?}", e);
            return;
        }
    };

    for (addr_str, offchain) in all_balances {
        if addr_str.starts_with("__") {
            continue;
        }
        if let Ok(address) = addr_str.parse::<ddb_sdk::Address>() {
            let onchain = match ddb_sdk::retry_with_backoff(
                || async { contract.balances(address).call().await },
                3,
                500,
            )
            .await
            {
                Ok(r) => r._0,
                Err(e) => {
                    eprintln!("Failed to get on-chain balance for {}: {:?}", address, e);
                    continue;
                }
            };

            if onchain > offchain {
                let diff = onchain - offchain;
                let dust = ddb_sdk::U256::from(100_000_000_000_000u64);
                if diff > dust {
                    eprintln!("Slashing {} for {}", diff, address);
                    let result: Result<(), Box<dyn std::error::Error>> =
                        ddb_sdk::retry_with_backoff(
                            || async {
                                let call = contract.slash(address, diff);
                                call.send().await?.watch().await.ok();
                                Ok::<(), Box<dyn std::error::Error>>(())
                            },
                            3,
                            500,
                        )
                        .await;
                    result.ok();
                }
            }
        }
    }

    eprintln!("Slashing round complete.");
}

// ─── Vector ────────────────────────────────────────────────────────────────

fn handle_vector(action: VectorAction) {
    match action {
        VectorAction::Search { query, top_k: _ } => {
            eprintln!(
                "Vector search requires the Python SDK. Run: python -c \"import ddb; ddb.RAG(namespace='...').search('{}')\"",
                query
            );
            let empty: Vec<ddb_sdk::SearchResult> = Vec::new();
            println!("{}", serde_json::to_string(&empty).unwrap());
        }
    }
}
