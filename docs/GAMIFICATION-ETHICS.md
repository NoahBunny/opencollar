# Gamification: the evidence, and what we did with it

This system asks someone to hand over control of their own device. Adding
engagement mechanics on top of that is not the same act as adding them to a
language app, and it deserved research rather than intuition. This document
records what the literature actually says, which design decisions it drove,
and — as importantly — what we declined to build and why.

It is here so the reasoning is auditable. Anyone can check whether the code
matches the claims: the mechanics live in `focuslock-mail.py` (devotion,
gamble) with tests in `tests/test_devotion.py` and `tests/test_gamble_limits.py`.

Related: [THREAT-MODEL.md](THREAT-MODEL.md) for the adversarial picture,
and the Safety section of [../CLAUDE.md](../CLAUDE.md) for the consent floor
that none of this trades against.

---

## 1. The correction that shaped everything

Dopamine does not encode pleasure. It encodes **reward prediction error** —
the gap between what was expected and what happened. Schultz's core finding is
that a *fully predicted* reward produces no phasic dopamine response at all;
the signal is a teaching signal, not a pleasure signal.

- Schultz, *Predictive Reward Signal of Dopamine Neurons*, J. Neurophysiology 80(1), 1998.
  <https://journals.physiology.org/doi/full/10.1152/jn.1998.80.1.1>

**What this ruled out.** The first version of devotion paid exactly `+1` per
task, forever. That is the textbook definition of a fully predicted reward: it
goes inert within weeks. "More points" does not fix it, because predictability
is the problem, not magnitude.

**What it ruled in.** The design question is not *how do we produce dopamine*.
It is **where does the uncertainty come from** — and the answer determines
whether you have built a relationship or a slot machine.

## 2. Why we did not use variable rewards

The standard fix for a flat reward is a variable-ratio payout. It is also the
slot-machine schedule, and the most robust behavioural driver of compulsion:
intermittent reinforcement is markedly more resistant to extinction than any
fixed schedule, which is precisely why the behaviour continues after the
rewards stop.

- Overview of variable-ratio schedules and their use in gambling and app design:
  <https://fourweekmba.com/variable-ratio-schedule/>

This system already contains **one** variable-ratio mechanic, and it is wired
to real money: the double-or-nothing gamble (heads halves the balance, tails
doubles it). That is +25% expected value to the Lion per flip, which is what
makes it safe to offer at all — but only while the number of flips is bounded.
Over enough attempts, variance clears any balance, which would turn a
Lion-favourable bet into an escape hatch.

Hence `GAMBLE_COOLDOWN_S` and `GAMBLE_MAX_PER_DAY`, enforced relay-side and
failing closed (`tests/test_gamble_limits.py`). **We do not intend to loosen
these,** and no second variable-ratio mechanic was added anywhere.

## 3. Where the uncertainty comes from instead: a person

Points accrue deterministically. The variable term is the Lion's
**commendation** — whether it comes, when, and what it says.

This keeps the prediction error that makes the loop live, while:

- removing the gambling structure entirely (no intermittent nothing, no near-miss);
- routing the payoff **through** the relationship rather than around it, which
  serves *relatedness* — the psychological need gamification demonstrably does
  serve, per the meta-analytic evidence below;
- making the loop structurally impossible to self-serve, because the bunny does
  not hold the key that signs a commendation.

It also inverts the documented failure mode in §5: the points cannot displace
the thing they were meant to serve when the payoff *is* the thing.

Implementation: `devotion_commend()` in `focuslock-mail.py`,
`/api/mesh/{id}/commend` (Lion-signed), UI in Lion's Share under ⋮ → Devotion.

## 4. What the evidence supports, and what we took from it

### Streaks work, via loss aversion — but the freeze is load-bearing

Duolingo has run 600+ experiments on the streak feature alone. Streak freeze
cut churn ~21% for at-risk users, and users with freeze access held streaks
**4.5× longer by day 21** (30.6 days vs 18.9). New users are given two freezes
deliberately; roughly seven days is where a streak "locks in" as habit.

- <https://medium.com/@salamprem49/duolingo-streak-system-detailed-breakdown-design-flow-886f591c953f>
- <https://duolingo.deconstructoroffun.com/mechanics/streaks>

**What we built.** A weekly devotion streak with `DEVOTION_FREEZE_CAP = 2`,
granted automatically — never sold, never a reward, because the evidence says
it is structural rather than a courtesy. A new mesh gets the full allowance,
then one per month.

### The streak is weekly, and that is not a stylistic choice

Two reasons, one of them specific to this system.

First, from the field research: one interviewee said plainly that a daily
streak was *"completely unusable for me… Maybe a weekly streak would be better
for me"* (Mogavi et al., §4.3, S1-3).

Second, and more important here: **a daily streak in this system would be
breakable by the Lion.** An imposed lock, a fine, a confiscated evening — and
the bunny loses accumulated standing through no choice of their own. Loss
aversion motivates only while the loss is yours to prevent. A streak another
person can take at will teaches helplessness instead, and the goal-gradient
motivation in §4 collapses with it. Weekly also matches the allowance the
subscription tier already grants.

### A broken streak must not be a bare zero

The documented response to a zeroed counter after a long run is shame and
abandonment, not renewed effort — *"My brother lost his 110-day streak, and now
he is an abandoned account"* (Mogavi et al., §4.1, E1-3).

**What we built.** `broke_from` is retained, so the app says *"A run of 6 weeks
ended. Start another."* rather than displaying a hole where the number was.

### Goal gradient and endowed progress

People accelerate as a goal comes into view (Kivetz et al. tracked 948 café
loyalty members accelerating ~20% as cards filled). Nunes & Drèze's car-wash
experiment is the striking one: a 10-slot card with 2 stamps pre-filled reached
**34% completion vs 19%** for an empty 8-slot card requiring identical purchases.

- Kivetz, Urminsky & Zheng, *The Goal-Gradient Hypothesis Resurrected*, JMR 43(1), 2006.
  <https://www.columbia.edu/~rk566/Session4/Goal-Gradient_Illusionary_Goal_Progress.pdf>

**What we built.** `DEVOTION_OPENING_CREDIT = 2`, given openly as a gift of
**real points**, plus a "N to <next rank>" line rather than only a running
total. The head start is disclosed — the car-wash effect held with the gift
disclosed, and an inflated progress bar the bunny cannot audit would be a lie
told for engagement. We record real points instead.

## 5. What the evidence says harms — the part that changed our minds

We read Mogavi et al.'s qualitative study of gamification *misuse* in Duolingo:
30,000+ forum posts over nine years plus 15 semi-structured interviews.

- Mogavi, Guo, Zhang, Haq, Hui & Ma, *When Gamification Spoils Your Learning: A
  Qualitative Case Study of Gamification Misuse in a Language-Learning App*,
  ACM Learning @ Scale '22. <https://arxiv.org/pdf/2203.16175>

Their harm taxonomy is specific. Coded instances for poor well-being totalled
487, of which **apprehension alone was 275** — the single largest theme in the
study. Then self-recrimination, disruption of daily routines (133), and
physical health problems (105). Alongside those: reduced confidence in one's
own ability (60), lost interest (64), and withdrawal or dropping out (126).

The finding that most directly concerns this project:

> *"Some days I was so obsessed that I was literally seeing real learning as an
> obstacle to my success in gamification."*

**Translated to this system:** a bunny grinding devotion points to move a
counter has substituted the game for the power exchange, which is the entire
point of the system. That is the failure we designed against, and it is why the
reward is the Lion's attention rather than a number.

### Overjustification

Deci, Koestner & Ryan's meta-analysis found that tangible, expected,
performance-contingent rewards substantially undermine intrinsic motivation.
Paying points for something someone already wants to do can make them want it
less.

- Deci, Koestner & Ryan, *Extrinsic Rewards and Intrinsic Motivation in
  Education: Reconsidered Once Again*, Review of Educational Research 71(1), 2001.
  <https://journals.sagepub.com/doi/10.3102/00346543071001001>

**What we did.** Kept the reward informational rather than controlling. No
nagging, no "earn 2 more to unlock!", and **no devotion notifications at all** —
push is the controlling-language vector, and apprehension was the largest
documented harm.

### Gamification's real effect size is small, and lands on autonomy

Meta-analysis of 35 interventions (~2,500 participants): overall effect
Hedges' *g* ≈ 0.257. Gamification enhances intrinsic motivation and perceptions
of autonomy and relatedness, with **minimal impact on competence**. Badges and
leaderboards specifically undermine autonomy where they read as controlling,
and leaderboards deliver negative feedback to whoever is losing.

- <https://link.springer.com/article/10.1007/s11423-023-10337-7>

**What we did.** No leaderboard — in a two-person mesh it is absurd, and a
Lion-vs-bunny comparison would be the actively harmful case. Achievements, if
built, are personal milestones and Lion-authorable, which converts an extrinsic
system into a relational one.

## 6. What we declined to build

Each of these is individually evidenced as harmful, and this system carries
something a learning app does not: **real financial penalties and device
lockouts are already bearing the pressure load.** The gamification is therefore
deliberately the one part of the system with no teeth.

| Declined | Why |
| --- | --- |
| Random point bonuses / multipliers / crits | Variable-ratio is the compulsion schedule (§2) |
| A daily streak | Breakable by the Lion; teaches helplessness (§4) |
| Any leaderboard | Negative feedback to the loser; undermines autonomy (§5) |
| Points convertible to balance by the bunny | A self-service discount, which is the one thing the system exists not to hand over |
| Streak-loss notifications | Apprehension was the single largest documented harm (§5) |
| Inflated / unauditable progress bars | Endowed progress works when disclosed; an unauditable bar is a lie told for engagement (§4) |

This table is a commitment, not a snapshot. If a future change adds any row
back, it should update this document and say why.

## 7. How the boundary is enforced in code

Design intent rots unless something checks it. The load-bearing claims are
pinned by tests:

- `tests/test_devotion.py::TestPointsAreNotMoney` — asserts no balance-shaped
  key (`paywall`, `new_paywall`, `amount_cents`, `credit`) can appear in a claim
  response. If someone wires devotion to the paywall, this fails.
- `TestCommend::test_a_commendation_never_moves_money_or_points` — acknowledgement
  is non-monetary and does not inflate the counter either.
- `TestTheStreakIsWeekly`, `TestTheFreeze` — the streak cadence, the freeze
  grant, and the "remember what it was" behaviour on a break.
- `tests/test_gamble_limits.py::TestItFailsClosed` — the one variable-ratio
  mechanic stays bounded even when its state file is corrupt or unwritable.

## 8. Limitations

This is a design rationale, not a clinical claim. The evidence cited is drawn
from education, consumer loyalty and behavioural neuroscience; **none of it was
gathered in a consensual power-exchange context**, and we are extrapolating.
Mogavi et al. is qualitative and its authors are explicit that identifying
gamification misuse depends largely on whether end-users perceive it — they call
for quantitative follow-up that does not yet exist.

The honest summary is that we used the best available evidence to avoid the
mechanics with documented harm, and chose the conservative option wherever the
evidence was thin. The safety floor — consent, safeword, Release Forever, and an
always-available factory reset — is unaffected by any of this and is not
something engagement design gets to trade against.
