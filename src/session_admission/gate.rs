use super::Settings;
use crate::core::group_queue::GroupQueue;
use parking_lot::Mutex;
use std::{
    collections::{HashMap, VecDeque},
    sync::Arc,
};
use tokio::sync::Notify;
use uuid::Uuid;

#[derive(Clone, Debug, Hash, Eq, PartialEq)]
pub(super) struct SessionKey {
    pub subject: [u8; 32],
    pub id: String,
}

#[derive(Default, Debug)]
struct Lane {
    requests: GroupQueue<(Uuid, Arc<Notify>)>,
    running: bool,
    parked: bool,
}

#[derive(Default, Debug)]
struct State {
    lanes: HashMap<SessionKey, Lane>,
    ready: VecDeque<SessionKey>,
    active: usize,
    per_subject: HashMap<[u8; 32], usize>,
}

impl State {
    fn schedule(&mut self, settings: &Settings) {
        while self.active < settings.active {
            let Some(index) = self.ready.iter().position(|key| {
                self.per_subject.get(&key.subject).copied().unwrap_or(0) < settings.subject_limit()
            }) else {
                break;
            };
            let key = self.ready.remove(index).expect("ready lane");
            let lane = self.lanes.get_mut(&key).expect("queued lane");
            lane.running = true;
            self.active += 1;
            *self.per_subject.entry(key.subject).or_default() += 1;
            lane.requests.front().expect("waiting head").1.notify_one();
        }
    }

    fn give_back(&mut self, key: &SessionKey) {
        self.active -= 1;
        let count = self
            .per_subject
            .get_mut(&key.subject)
            .expect("active subject");
        *count -= 1;
        if *count == 0 {
            self.per_subject.remove(&key.subject);
        }
    }
}

#[derive(Debug)]
pub(super) struct Gate {
    state: Mutex<State>,
    settings: Arc<Settings>,
}

#[derive(Debug)]
pub(crate) struct Ticket {
    gate: Arc<Gate>,
    key: SessionKey,
    pub id: Uuid,
    wake: Arc<Notify>,
}

impl Gate {
    pub fn new(settings: Arc<Settings>) -> Arc<Self> {
        Arc::new(Self {
            settings,
            state: Mutex::default(),
        })
    }

    pub fn enqueue(self: &Arc<Self>, key: SessionKey) -> Option<Arc<Ticket>> {
        let mut state = self.state.lock();
        if !state.lanes.contains_key(&key)
            && (self
                .settings
                .sessions
                .is_some_and(|max| state.lanes.len() >= max)
                || self.settings.sessions_per_subject.is_some_and(|max| {
                    state
                        .lanes
                        .keys()
                        .filter(|k| k.subject == key.subject)
                        .count()
                        >= max
                }))
        {
            return None;
        }
        let lane = state.lanes.entry(key.clone()).or_default();
        if lane
            .requests
            .len()
            .saturating_sub(usize::from(lane.running || lane.parked))
            >= self.settings.queued_per_session
        {
            return None;
        }
        let ticket = Arc::new(Ticket {
            gate: self.clone(),
            key: key.clone(),
            id: Uuid::new_v4(),
            wake: Arc::new(Notify::new()),
        });
        let first = lane.requests.is_empty();
        lane.requests.push_back((ticket.id, ticket.wake.clone()));
        if first {
            state.ready.push_back(key);
        }
        state.schedule(&self.settings);
        Some(ticket)
    }
}

impl Ticket {
    pub async fn granted(&self) {
        self.wake.notified().await;
    }

    /// The head stays in its lane while another session uses the active slot.
    pub async fn park_until(&self, changed: impl std::future::Future<Output = ()>) {
        {
            let mut state = self.gate.state.lock();
            let lane = state.lanes.get_mut(&self.key).expect("live ticket");
            debug_assert!(lane.running && lane.requests.front().unwrap().0 == self.id);
            lane.running = false;
            lane.parked = true;
            state.give_back(&self.key);
            state.schedule(&self.gate.settings);
        }
        changed.await;
        {
            let mut state = self.gate.state.lock();
            state.lanes.get_mut(&self.key).expect("parked lane").parked = false;
            state.ready.push_back(self.key.clone());
            state.schedule(&self.gate.settings);
        }
        self.granted().await;
    }
}

impl Drop for Ticket {
    fn drop(&mut self) {
        let mut state = self.gate.state.lock();
        let lane = state.lanes.get_mut(&self.key).expect("retained ticket");
        if lane.requests.cancel(|item| item.0 == self.id) != Some(true) {
            return;
        }
        let active = lane.running;
        lane.running = false;
        lane.parked = false;
        let empty = lane.requests.is_empty();
        if active {
            state.give_back(&self.key);
        }
        state.ready.retain(|key| key != &self.key);
        if empty {
            state.lanes.remove(&self.key);
        } else {
            state.ready.push_back(self.key.clone());
        }
        state.schedule(&self.gate.settings);
    }
}

#[cfg(all(test, feature = "cpu", feature = "ready"))]
mod tests {
    use super::*;
    use futures::FutureExt;

    fn setup(active: usize, subject_limit: usize) -> Arc<Gate> {
        let config = serde_json::json!({"active":active,"active_per_subject":subject_limit,"resident_requests":16,"resident_bytes":65536,"queued_per_session":1,"rank_capacity":false});
        Gate::new(Arc::new(serde_json::from_value(config).unwrap()))
    }
    fn key(id: &str, subject: u8) -> SessionKey {
        SessionKey {
            id: id.to_owned(),
            subject: [subject; 32],
        }
    }
    #[tokio::test]
    async fn rotates_sessions_and_cancels_waiters() {
        let gate = setup(1, 1);
        let a = key("A", 0);
        let a1 = gate.enqueue(a.clone()).unwrap();
        a1.granted().await;
        let a2 = gate.enqueue(a.clone()).unwrap();
        assert!(gate.enqueue(a.clone()).is_none());
        let b = gate.enqueue(key("B", 0)).unwrap();
        drop(a1);
        assert!(a2.granted().now_or_never().is_none());
        b.granted().await;
        drop(b);
        a2.granted().await;
        let cancelled = gate.enqueue(a.clone()).unwrap();
        drop(cancelled);
        drop(a2);
        assert!(gate.state.lock().lanes.is_empty());
    }
    #[tokio::test]
    async fn parked_head_keeps_fifo_and_frees_the_slot() {
        let gate = setup(1, 1);
        let a1 = gate.enqueue(key("A", 0)).unwrap();
        a1.granted().await;
        let a2 = gate.enqueue(key("A", 0)).unwrap();
        let b = gate.enqueue(key("B", 0)).unwrap();
        let changed = Notify::new();
        let mut park = Box::pin(a1.park_until(changed.notified()));
        assert!(park.as_mut().now_or_never().is_none());
        b.granted().await;
        changed.notify_one();
        assert!(park.as_mut().now_or_never().is_none());
        drop(b);
        park.await;
        assert!(a2.granted().now_or_never().is_none());
        drop(a1);
        a2.granted().await;
        drop(a2);
        assert_eq!(gate.state.lock().active, 0);
    }
    #[tokio::test]
    async fn same_id_in_different_subjects_and_abandoned_park() {
        let gate = setup(2, 1);
        let a = gate.enqueue(key("same", 1)).unwrap();
        a.granted().await;
        let next = gate.enqueue(key("next", 1)).unwrap();
        let b = gate.enqueue(key("same", 2)).unwrap();
        b.granted().await;
        assert!(next.granted().now_or_never().is_none());
        assert!(a
            .park_until(std::future::pending())
            .now_or_never()
            .is_none());
        drop(a);
        next.granted().await;
        drop(next);
        drop(b);
        assert!(gate.state.lock().per_subject.is_empty());
    }
}
