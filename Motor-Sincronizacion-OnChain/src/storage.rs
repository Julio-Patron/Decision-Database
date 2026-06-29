use alloy_primitives::{Address, U256};
use std::collections::HashMap;
use std::sync::Arc;
use tokio::sync::RwLock;

pub enum StorageBackend {
    Dynamo(DynamoStorage),
    Memory(MemoryStorage),
}

impl StorageBackend {
    pub async fn get_all_balances(
        &self,
    ) -> Result<Vec<(String, U256)>, Box<dyn std::error::Error>> {
        match self {
            StorageBackend::Dynamo(s) => s.get_all_balances().await,
            StorageBackend::Memory(s) => s.get_all_balances().await,
        }
    }

    pub async fn get_last_sync_block(&self) -> Result<u64, Box<dyn std::error::Error>> {
        match self {
            StorageBackend::Dynamo(s) => s.get_last_sync_block().await,
            StorageBackend::Memory(s) => s.get_last_sync_block().await,
        }
    }

    pub async fn set_last_sync_block(&self, block: u64) -> Result<(), Box<dyn std::error::Error>> {
        match self {
            StorageBackend::Dynamo(s) => s.set_last_sync_block(block).await,
            StorageBackend::Memory(s) => s.set_last_sync_block(block).await,
        }
    }

    pub async fn add_balance(
        &self,
        user: &Address,
        amount: U256,
    ) -> Result<(), Box<dyn std::error::Error>> {
        match self {
            StorageBackend::Dynamo(s) => s.add_balance(user, amount).await,
            StorageBackend::Memory(s) => s.add_balance(user, amount).await,
        }
    }
}

pub struct DynamoStorage {}
impl DynamoStorage {
    pub async fn new(_balances_table: &str, _nonces_table: &str) -> Self {
        DynamoStorage {}
    }
    pub async fn get_all_balances(
        &self,
    ) -> Result<Vec<(String, U256)>, Box<dyn std::error::Error>> {
        Ok(vec![])
    }
    pub async fn get_last_sync_block(&self) -> Result<u64, Box<dyn std::error::Error>> {
        Ok(0)
    }
    pub async fn set_last_sync_block(&self, _block: u64) -> Result<(), Box<dyn std::error::Error>> {
        Ok(())
    }
    pub async fn add_balance(
        &self,
        _user: &Address,
        _amount: U256,
    ) -> Result<(), Box<dyn std::error::Error>> {
        Ok(())
    }
}

pub struct MemoryStorage {
    balances: Arc<RwLock<HashMap<String, U256>>>,
    last_block: Arc<RwLock<u64>>,
}
impl Default for MemoryStorage {
    fn default() -> Self {
        Self::new()
    }
}

impl MemoryStorage {
    pub fn new() -> Self {
        MemoryStorage {
            balances: Arc::new(RwLock::new(HashMap::new())),
            last_block: Arc::new(RwLock::new(0)),
        }
    }
    pub async fn get_all_balances(
        &self,
    ) -> Result<Vec<(String, U256)>, Box<dyn std::error::Error>> {
        let balances = self.balances.read().await;
        Ok(balances.iter().map(|(k, v)| (k.clone(), *v)).collect())
    }
    pub async fn get_last_sync_block(&self) -> Result<u64, Box<dyn std::error::Error>> {
        Ok(*self.last_block.read().await)
    }
    pub async fn set_last_sync_block(&self, block: u64) -> Result<(), Box<dyn std::error::Error>> {
        *self.last_block.write().await = block;
        Ok(())
    }
    pub async fn add_balance(
        &self,
        user: &Address,
        amount: U256,
    ) -> Result<(), Box<dyn std::error::Error>> {
        let mut balances = self.balances.write().await;
        let addr = user.to_string();
        let current = balances.entry(addr).or_insert(U256::ZERO);
        *current += amount;
        Ok(())
    }
}
