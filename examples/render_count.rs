//! CPU-only parity check: Router chat rendering + tokenization vs the Engine.
//!
//! Reads a JSONL fixture of `{messages, kwargs, hf_tokens}` rows and prints the
//! token count produced by `EngineFrontend::prepare`, so a deployment can
//! verify tokenizer/template equivalence without a GPU.

use std::time::Duration;

use serde_json::json;
use vllm_router_rs::backend::EngineFrontend;
use vllm_router_rs::protocols::spec::ChatCompletionRequest;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let fixture = std::env::var("FIXTURE")?;
    let frontend = EngineFrontend::with_request_timeout(Duration::from_secs(120));
    let text = std::fs::read_to_string(fixture)?;
    let mut mismatches = 0usize;
    let mut checked = 0usize;
    for line in text.lines().filter(|line| !line.trim().is_empty()) {
        let row: serde_json::Value = serde_json::from_str(line)?;
        let body = json!({
            "model": "pd-model",
            "messages": row["messages"],
            "chat_template_kwargs": row["kwargs"],
        });
        let request: ChatCompletionRequest = serde_json::from_value(body)?;
        let prepared = frontend
            .prepare(request)
            .await
            .map_err(|error| anyhow::anyhow!(error))?;
        let router_tokens = prepared.tokenized.token_ids.len();
        let hf_tokens = row["hf_tokens"].as_u64().unwrap_or(0) as usize;
        checked += 1;
        if router_tokens != hf_tokens {
            mismatches += 1;
            println!(
                "MISMATCH router={router_tokens} hf={hf_tokens} messages={}",
                row["messages"].as_array().map(|v| v.len()).unwrap_or(0)
            );
        } else {
            println!("ok router={router_tokens} hf={hf_tokens}");
        }
    }
    println!("checked={checked} mismatches={mismatches}");
    if mismatches > 0 {
        std::process::exit(1);
    }
    Ok(())
}
