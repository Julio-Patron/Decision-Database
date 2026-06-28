use std::io::{self, Read};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use logic_evaluator::metadata::RuleDefinition;
use logic_evaluator::{execute_explain, ExplainResult};

#[derive(Deserialize)]
struct DecisionInput {
    rule: RuleDefinition,
    context: Value,
}

#[derive(Serialize)]
struct DecisionContext {
    success: bool,
    result: Option<Value>,
    explain_tree: Option<ExplainResult>,
    logic_snapshot: Option<Value>,
    error: Option<String>,
}

fn main() {
    let mut input_str = String::new();
    io::stdin().read_to_string(&mut input_str).expect("stdin read error");

    let input: DecisionInput = match serde_json::from_str(&input_str) {
        Ok(v) => v,
        Err(e) => {
            let out = DecisionContext {
                success: false,
                result: None,
                explain_tree: None,
                logic_snapshot: None,
                error: Some(format!("Parse error: {}", e)),
            };
            println!("{}", serde_json::to_string(&out).unwrap());
            return;
        }
    };

    let context_str = input.context.to_string();
    match execute_explain(&input.rule, &context_str) {
        Ok(explain) => {
            let logic_snapshot = serde_json::to_value(&explain.logic_snapshot).ok();
            let out = DecisionContext {
                success: true,
                result: Some(explain.result.clone()),
                explain_tree: Some(explain),
                logic_snapshot,
                error: None,
            };
            println!("{}", serde_json::to_string(&out).unwrap());
        }
        Err(e) => {
            let out = DecisionContext {
                success: false,
                result: None,
                explain_tree: None,
                logic_snapshot: None,
                error: Some(format!("Evaluation error: {:?}", e)),
            };
            println!("{}", serde_json::to_string(&out).unwrap());
        }
    }
}
