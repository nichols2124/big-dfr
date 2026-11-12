//! Animation physics for the whole bar, built from published values.
//!
//! Expressive uses Material 3's motion tokens (androidx.compose.material3
//! ExpressiveMotionTokens). Smooth uses Apple's SwiftUI spring model, where
//! Spring(duration, bounce) is stiffness (2*pi/duration)^2 and damping ratio
//! 1 - bounce; presets are smooth = 0, snappy = 0.15 (default duration 0.5 s).
//!
//! As in Material, anything that moves or resizes uses a *spatial* spring, and
//! color or opacity uses an *effects* spring, which never overshoots.

use std::f64::consts::TAU;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::time::Instant;

static EXPRESSIVE: AtomicBool = AtomicBool::new(false);
// Bits of the f64 scale for screen transitions (1.0 = the published spring).
static SCREEN_SCALE: AtomicU64 = AtomicU64::new(0x3FF0_0000_0000_0000);

pub fn set_expressive(on: bool) {
    EXPRESSIVE.store(on, Ordering::Relaxed);
}

pub fn expressive() -> bool {
    EXPRESSIVE.load(Ordering::Relaxed)
}

/// `TransitionMs` / 500: how much longer screen transitions take than published.
pub fn set_screen_scale(scale: f64) {
    SCREEN_SCALE.store(scale.max(0.05).to_bits(), Ordering::Relaxed);
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Spring {
    pub stiffness: f64,
    pub ratio: f64,
}

impl Spring {
    /// SwiftUI's `Spring(duration:bounce:)`.
    pub fn apple(duration: f64, bounce: f64) -> Spring {
        let w = TAU / duration;
        Spring { stiffness: w * w, ratio: 1.0 - bounce }
    }

    pub const fn material(ratio: f64, stiffness: f64) -> Spring {
        Spring { stiffness, ratio }
    }

    fn slowed(self, scale: f64) -> Spring {
        Spring { stiffness: self.stiffness / (scale * scale), ratio: self.ratio }
    }

    /// Damping force coefficient, for code that integrates the spring itself.
    pub fn damping(self) -> f64 {
        2.0 * self.ratio * self.stiffness.sqrt()
    }

    /// Displacement and velocity `t` seconds after starting at (y0, v0).
    pub fn solve(self, y0: f64, v0: f64, t: f64) -> (f64, f64) {
        let w = self.stiffness.sqrt();
        let z = self.ratio;
        if z >= 0.9999 {
            let b = v0 + w * y0;
            let e = (-w * t).exp();
            ((y0 + b * t) * e, (b - w * (y0 + b * t)) * e)
        } else {
            let wd = w * (1.0 - z * z).sqrt();
            let b = (v0 + z * w * y0) / wd;
            let (s, c) = (wd * t).sin_cos();
            let e = (-z * w * t).exp();
            let y = e * (y0 * c + b * s);
            (y, e * (-y0 * wd * s + b * wd * c) - z * w * y)
        }
    }

    /// Progress from 0 to 1 after `t` seconds, starting at rest.
    pub fn step(self, t: f64) -> f64 {
        1.0 + self.solve(-1.0, 0.0, t.max(0.0)).0
    }
}

macro_rules! tokens {
    ($($name:ident: $smooth:expr, $expressive:expr;)*) => {$(
        pub fn $name() -> Spring {
            if expressive() { $expressive } else { $smooth }
        }
    )*};
}

// Durations for the Apple column are chosen per size, like Material's rule:
// small things fast, bar-spanning things at SwiftUI's default 0.5 s.
tokens! {
    fast_spatial:    Spring::apple(0.30, 0.15), Spring::material(0.6, 800.0);
    default_spatial: Spring::apple(0.40, 0.0),  Spring::material(0.8, 380.0);
    fast_effects:    Spring::apple(0.15, 0.0),  Spring::material(1.0, 3800.0);
    default_effects: Spring::apple(0.25, 0.0),  Spring::material(1.0, 1600.0);
}

/// Popups (the expanded Control Strip, media, brightness, volume) move on the
/// same spatial/effects springs as everything else on the bar -- the same ones
/// `appear`/`appear_fx` use for a button fading in -- just stretched by
/// `TransitionMs` (1.0 = unscaled, i.e. identical to a button's own motion).
pub fn screen_spatial() -> Spring {
    default_spatial().slowed(f64::from_bits(SCREEN_SCALE.load(Ordering::Relaxed)))
}

pub fn screen_effects() -> Spring {
    default_effects().slowed(f64::from_bits(SCREEN_SCALE.load(Ordering::Relaxed)))
}

/// A value on a spring. Retargeting mid-flight keeps its velocity.
#[derive(Clone, Copy)]
pub struct Animated {
    target: f64,
    y0: f64,
    v0: f64,
    start: Option<Instant>,
    spring: Spring,
}

impl Default for Animated {
    fn default() -> Self {
        Animated { target: 0.0, y0: 0.0, v0: 0.0, start: None, spring: Spring::material(1.0, 1600.0) }
    }
}

impl Animated {
    pub fn settled_at(value: f64) -> Self {
        Animated { target: value, ..Default::default() }
    }

    fn state(&self) -> (f64, f64) {
        match self.start {
            Some(t0) => {
                let (y, v) = self.spring.solve(self.y0, self.v0, t0.elapsed().as_secs_f64());
                (self.target + y, v)
            }
            None => (self.target, 0.0),
        }
    }

    pub fn value(&self) -> f64 {
        self.state().0
    }

    pub fn velocity(&self) -> f64 {
        self.state().1
    }

    /// Head for `target` from wherever it is now, at its current velocity.
    pub fn set(&mut self, target: f64, spring: Spring) {
        if target != self.target {
            let (x, v) = self.state();
            self.launch_with_velocity(x, target, v, spring);
        }
    }

    pub fn launch(&mut self, from: f64, target: f64, spring: Spring) {
        self.launch_with_velocity(from, target, 0.0, spring);
    }

    pub fn launch_with_velocity(&mut self, from: f64, target: f64, velocity: f64, spring: Spring) {
        self.target = target;
        self.y0 = from - target;
        self.v0 = velocity;
        self.spring = spring;
        self.start = Some(Instant::now());
    }

    /// Whether it is still moving; clears itself once at rest.
    pub fn tick(&mut self) -> bool {
        if self.start.is_none() {
            return false;
        }
        let (x, v) = self.state();
        if (x - self.target).abs() < 0.002 && v.abs() < 0.02 {
            self.start = None;
            return false;
        }
        true
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn apple_spring_matches_the_documented_example() {
        // Apple's docs: Spring(duration: 0.5, bounce: 0.3) -> (1.0, 157.9, 17.6)
        let s = Spring::apple(0.5, 0.3);
        assert!((s.stiffness - 157.9).abs() < 0.05, "{s:?}");
        assert!((s.damping() - 17.6).abs() < 0.05, "{s:?}");
    }

    fn peak(s: Spring) -> f64 {
        (0..=2000).map(|i| s.step(i as f64 / 1000.0)).fold(f64::MIN, f64::max)
    }

    #[test]
    fn effects_springs_never_overshoot() {
        for s in [Spring::material(1.0, 3800.0), Spring::material(1.0, 1600.0), Spring::apple(0.25, 0.0)] {
            assert!(peak(s) <= 1.0, "{s:?}");
        }
    }

    #[test]
    fn expressive_fast_spatial_bounces_more_than_snappy() {
        let material = peak(Spring::material(0.6, 800.0));
        let snappy = peak(Spring::apple(0.3, 0.15));
        assert!(material > 1.08 && material < 1.12, "{material}");
        assert!(snappy > 1.0 && snappy < material, "{snappy}");
    }

    #[test]
    fn springs_settle() {
        for s in [Spring::apple(0.5, 0.0), Spring::material(0.8, 200.0), Spring::material(0.6, 800.0)] {
            assert!((s.step(1.5) - 1.0).abs() < 0.001, "{s:?}");
        }
    }

    #[test]
    fn solve_velocity_is_the_derivative() {
        let s = Spring::material(0.6, 800.0);
        let h = 1e-6;
        let (y, v) = s.solve(-1.0, 3.0, 0.05);
        let (y2, _) = s.solve(-1.0, 3.0, 0.05 + h);
        assert!(((y2 - y) / h - v).abs() < 1e-3);
    }

    #[test]
    fn retargeting_keeps_position_and_velocity() {
        let mut a = Animated::default();
        a.launch(0.0, 1.0, Spring::material(0.6, 800.0));
        std::thread::sleep(std::time::Duration::from_millis(20));
        let (x, v) = (a.value(), a.velocity());
        a.set(0.0, Spring::material(0.6, 800.0));
        assert!((a.value() - x).abs() < v.abs() * 0.01 + 0.02);
        assert!(a.velocity() > 0.0, "still moving the old way right after retargeting");
    }
}
