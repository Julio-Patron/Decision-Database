use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SearchResult {
    pub id: String,
    pub score: f64,
    pub payload: Option<Value>,
}

pub trait VectorStore: Send + Sync {
    fn search(&self, query: &[f32], top_k: usize) -> Result<Vec<SearchResult>, String>;
    fn upsert(&mut self, id: &str, vector: &[f32], payload: Option<Value>) -> Result<(), String>;
    fn delete(&mut self, id: &str) -> Result<(), String>;
}

pub fn cosine_similarity(a: &[f32], b: &[f32]) -> f64 {
    let dot: f32 = a.iter().zip(b.iter()).map(|(x, y)| x * y).sum();
    let norm_a: f32 = a.iter().map(|x| x * x).sum::<f32>().sqrt();
    let norm_b: f32 = b.iter().map(|x| x * x).sum::<f32>().sqrt();
    if norm_a == 0.0 || norm_b == 0.0 {
        return 0.0;
    }
    (dot / (norm_a * norm_b)) as f64
}

#[allow(unused_variables)]
pub fn embed_text(text: &str) -> Result<Vec<f32>, String> {
    Err("Embedding requires Python sentence-transformers or an external service. Use ddb.rag.RAG from Python for now.".to_string())
}
