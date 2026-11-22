//! FIFO shared by Program and Session scheduling. Each owner supplies its own key.
use std::collections::VecDeque;

#[derive(Debug)]
pub(crate) struct GroupQueue<T>(VecDeque<T>);

impl<T> Default for GroupQueue<T> {
    fn default() -> Self {
        Self(VecDeque::new())
    }
}

impl<T> GroupQueue<T> {
    pub fn push_back(&mut self, value: T) {
        self.0.push_back(value);
    }
    pub fn front(&self) -> Option<&T> {
        self.0.front()
    }
    pub fn take_front(&mut self, matches: impl FnOnce(&T) -> bool) -> Option<T> {
        if matches(self.front()?) {
            self.0.pop_front()
        } else {
            None
        }
    }
    pub fn cancel(&mut self, matches: impl FnMut(&T) -> bool) -> Option<bool> {
        let index = self.0.iter().position(matches)?;
        self.0.remove(index);
        Some(index == 0)
    }
    pub fn is_empty(&self) -> bool {
        self.0.is_empty()
    }
    pub fn len(&self) -> usize {
        self.0.len()
    }
    pub fn iter_mut(&mut self) -> impl Iterator<Item = &mut T> {
        self.0.iter_mut()
    }
}
