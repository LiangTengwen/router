//! Protocol with a single-rank capacity authority mounted on each engine.
use super::Execution;
use crate::core::Worker;
use axum::{
    body::Bytes,
    http::StatusCode,
    response::{IntoResponse, Response},
};
use futures::future::{join_all, select_all};
use reqwest::Client;
use serde::Deserialize;
use std::{sync::Arc, time::Duration};
use tokio::time::timeout_at;
use uuid::Uuid;

#[derive(Deserialize)]
struct Quote {
    epoch: Uuid,
    version: u64,
    total: u64,
    free: u64,
    required: u64,
    digest: String,
    stamp: String,
}

/// Proof scoped to a single Router attempt, created before inference transport.
#[derive(Clone, Copy)]
pub(crate) struct Unsent;

pub(crate) fn unsent(mut response: Response) -> Response {
    response.extensions_mut().insert(Unsent);
    response
}

pub(crate) struct Lease {
    pub worker: Arc<dyn Worker>,
    pub payload: Bytes,
    pub id: Uuid,
    pub epoch: Uuid,
    client: Client,
    secret: String,
}
tokio::task_local! {pub(crate) static SENDING: Lease;}
impl Drop for Lease {
    fn drop(&mut self) {
        // The authority only revokes an unredeemed lease. If redemption won
        // the race, only backend completion evidence can return its blocks.
        let req = self
            .client
            .delete(format!("{}/_session/lease/{}", self.worker.url(), self.id))
            .header("x-router-capacity-key", &self.secret)
            .header("x-router-epoch", self.epoch.to_string())
            .timeout(Duration::from_secs(2));
        tokio::spawn(async move {
            let _ = req.send().await;
        });
    }
}

pub(crate) async fn reserve(
    ctx: &Execution,
    client: &Client,
    workers: &[Arc<dyn Worker>],
    payload: Bytes,
    rank_key: &str,
    complete_pool: bool,
    mut recommend: impl FnMut(&[Arc<dyn Worker>]) -> Option<usize>,
) -> Result<Lease, Response> {
    let unavailable = || {
        (
            StatusCode::SERVICE_UNAVAILABLE,
            "no authoritative rank capacity",
        )
            .into_response()
    };
    if workers
        .iter()
        .any(|worker| !ctx.rank_urls.iter().any(|url| url == worker.url()))
    {
        return Err(unavailable());
    }
    if workers.is_empty() {
        return Err(unavailable());
    }
    loop {
        let quotes = join_all(workers.iter().map(|w| async {
            let resp = client
                .post(format!("{}/_session/quote", w.url()))
                .header("x-router-capacity-key", rank_key)
                .header("content-type", "application/json")
                .body(payload.clone())
                .timeout(Duration::from_secs(3))
                .send()
                .await
                .ok()?;
            let q = resp.error_for_status().ok()?.json::<Quote>().await.ok()?;
            (q.total > 0 && q.required > 0 && q.free <= q.total).then_some(q)
        }))
        .await;
        let mut fit: Vec<_> = quotes
            .iter()
            .enumerate()
            .filter_map(|(i, q)| {
                let q = q.as_ref()?;
                (workers[i].is_available() && q.required <= q.free).then_some(i)
            })
            .collect();
        let mut contended = false;
        while !fit.is_empty() {
            let candidates: Vec<_> = fit.iter().map(|&i| workers[i].clone()).collect();
            let selected = recommend(&candidates).ok_or_else(unavailable)?;
            if selected >= fit.len() {
                return Err(unavailable());
            }
            let i = fit.remove(selected);
            let q = quotes[i].as_ref().expect("feasible quote");
            let id = Uuid::new_v4();
            let worker = &workers[i];
            let request = serde_json::json!({"epoch":q.epoch,"id":id,"required":q.required,"digest":q.digest,"stamp":q.stamp});
            let call = || {
                client
                    .post(format!("{}/_session/lease", worker.url()))
                    .header("x-router-capacity-key", rank_key)
                    .json(&request)
                    .timeout(Duration::from_secs(2))
                    .send()
            };
            let reply = match call().await {
                Ok(r) => r,
                Err(_) => call().await.map_err(|_| unavailable())?,
            };
            match reply.status() {
                StatusCode::CREATED | StatusCode::OK => {
                    tracing::info!(request=%ctx.ticket.id, %id,rank=%worker.url(),epoch=%q.epoch,blocks=q.required,"session_rank_claim");
                    return Ok(Lease {
                        worker: worker.clone(),
                        payload,
                        id,
                        epoch: q.epoch,
                        client: client.clone(),
                        secret: rank_key.to_owned(),
                    });
                }
                StatusCode::CONFLICT => contended = true,
                _ => return Err(unavailable()),
            }
        }
        let possible: Vec<_> = quotes
            .iter()
            .enumerate()
            .filter_map(|(i, q)| {
                let q = q.as_ref()?;
                (q.required <= q.total).then_some((i, q))
            })
            .collect();
        if possible.is_empty() {
            return Err(if complete_pool && quotes.iter().all(Option::is_some) {
                (StatusCode::BAD_REQUEST, "request exceeds every rank budget").into_response()
            } else {
                unavailable()
            });
        }
        if !contended && quotes.iter().any(Option::is_none) {
            return Err(unavailable());
        }
        let waits: Vec<_> = possible
            .into_iter()
            .map(|(i, q)| {
                let req = client
                    .get(format!(
                        "{}/_session/wait?version={}&epoch={}",
                        workers[i].url(),
                        q.version,
                        q.epoch
                    ))
                    .header("x-router-capacity-key", rank_key)
                    .timeout(Duration::from_secs(5));
                Box::pin(async move {
                    if req
                        .send()
                        .await
                        .and_then(reqwest::Response::error_for_status)
                        .is_err()
                    {
                        tokio::time::sleep(Duration::from_millis(100)).await
                    }
                })
            })
            .collect();
        tracing::debug!(request=%ctx.ticket.id,"session_rank_park");
        timeout_at(
            ctx.deadline,
            ctx.ticket.park_until(async {
                select_all(waits).await;
            }),
        )
        .await
        .map_err(|_| StatusCode::GATEWAY_TIMEOUT.into_response())?;
    }
}
