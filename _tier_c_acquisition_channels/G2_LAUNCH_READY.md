# G2 Launch — Copy-Paste Ready Drafts (2026-05-24)

**CEO chose all 4 channels** (overrode "pick 2 of 5"; diaspora returnees deferred).

Each channel has ONE recommended opening post (the highest-fit draft from `CHANNELS_v1.md`) with UTM tags appended so the `/admin/analytics` dashboard (live now) breaks out conversion per channel cleanly.

**UTM convention:** `utm_source=<channel-slug>&utm_medium=<surface>&utm_campaign=launch_2026_05`.

**After posting:** monitor `/admin/analytics` for channel-specific funnel. Compare landing_view → signup_completed → payment_completed by `utm_source`.

---

## 1. Lanka Developers (Discord + lankadevelopers.lk forum)

**Where to post first:** lankadevelopers.lk forum, General or Off-topic sub-forum. Tag a mod first for permission to share a tool.

**Cross-link:** LK Developers Discord #general (1-line teaser + forum URL).

**UTM URL:** `https://fiesta-mvp.fly.dev/?utm_source=lanka_devs&utm_medium=discord_forum&utm_campaign=launch_2026_05`

**Copy:**

> **Title:** Built a free Sri Lanka tax calculator for devs earning USD — would love your feedback
>
> Hey Lanka Devs,
>
> Since the April 2025 Inland Revenue amendment, anyone of us earning USD from foreign clients and remitting via an SL bank pays a flat 15% final tax — but if you DON'T remit via bank (Payoneer card, kept offshore, etc.) you fall under progressive rates that hit 36% at the top bracket. Most devs I've spoken to are still defaulting to the wrong path and overpaying.
>
> I built **FIESTA** (https://fiesta-mvp.fly.dev/?utm_source=lanka_devs&utm_medium=discord_forum&utm_campaign=launch_2026_05) — a free calculator + filing helper for SL residents with foreign income. It takes your monthly USD earnings, your remittance pattern, and your deductions, and shows you the optimal structure under the new rules. Sample case: a dev earning $2,500/mo from a US client saves **Rs 298,980/yr** vs. the naive approach.
>
> It's MVP — Sri Lanka–specific, free to use, no signup wall on the calculator. I'd love your honest feedback before I roll it wider. What's confusing? What did I get wrong? Hit me here or via the in-app feedback.

---

## 2. Sri Lankan Freelancers FB group

**Where to post:** https://www.facebook.com/groups/FreelancesLK/ — needs admin approval for promo posts. Frame as "I built this, here's the savings, would love feedback" not pure marketing.

**UTM URL:** `https://fiesta-mvp.fly.dev/?utm_source=sl_freelancers&utm_medium=facebook&utm_campaign=launch_2026_05`

**Copy:**

> Anyone earning USD on Upwork, Fiverr, or direct client contracts in Sri Lanka — you've probably heard about the **15% final tax on foreign income remitted via SL banks** that kicked in April 2025. What's less obvious: if you DON'T remit via a SL bank (Payoneer card, kept offshore in USD, etc.), you actually fall into the progressive bracket that tops out at **36%**. Most freelancers I know are picking the wrong path without realising.
>
> I built a free calculator + filing tool — **FIESTA** (https://fiesta-mvp.fly.dev/?utm_source=sl_freelancers&utm_medium=facebook&utm_campaign=launch_2026_05) — that walks you through both scenarios with your actual monthly earnings. Example: $2,500/mo from a US client → optimal structure saves Rs 298,980 vs. the naive default.
>
> Sri Lanka-specific, no signup wall on the calc. Try it, tell me what's confusing or wrong. Working on this in the open and want feedback from people actually living this.

---

## 3. Fiverr Sri Lanka FB group + Fiverr Community SL Club

**Where to post:** https://www.facebook.com/groups/FiverrSriLanka/ — group admin approval likely needed. Fiverr Community SL Club secondary.

**UTM URL:** `https://fiesta-mvp.fly.dev/?utm_source=fiverr_sl&utm_medium=facebook&utm_campaign=launch_2026_05`

**Copy:**

> Fiverr sellers in SL — quick heads up on something that's likely costing you money.
>
> Most of us get paid in USD via Payoneer, then either (a) cash out to an SL bank account, or (b) use the Payoneer card directly. Since April 2025, **option (a) caps your tax at 15%** (final tax). **Option (b) doesn't — you fall into progressive rates up to 36%.** Counter-intuitive, but the bank remittance is actually the cheaper path for most sellers.
>
> I built **FIESTA** (https://fiesta-mvp.fly.dev/?utm_source=fiverr_sl&utm_medium=facebook&utm_campaign=launch_2026_05) — free calculator that takes your monthly $ earnings + remittance pattern and shows the difference. A typical $2,500/mo seller saves Rs 298,980/yr by structuring this correctly.
>
> Free, no signup wall on the calc. Let me know what's confusing or if I got anything wrong for your specific situation (smaller sellers, mixed payout methods, etc.).

---

## 4. IT Twitter / X (#lka)

**Where to post:** Single thread on CEO's main X account, tagged #lka #SriLanka #TaxSL. Pin for 7 days.

**UTM URL:** `https://fiesta-mvp.fly.dev/?utm_source=it_twitter&utm_medium=x_post&utm_campaign=launch_2026_05`

**Copy (thread, 4 tweets):**

> **1/4** Sri Lankan devs/freelancers earning USD: if you're remitting via an SL bank, you cap at 15% final tax. If you're NOT (Payoneer card, kept offshore), you're in the progressive bracket that hits 36% at the top. Most are choosing wrong. #lka #SriLanka
>
> **2/4** Worked example: dev earning $2,500/mo from a US client. Naive path (mixed): ~Rs 999K tax. Optimal structure under the April 2025 amendment: ~Rs 700K. Savings: **Rs 298,980/yr** (~30%).
>
> **3/4** Built a free calculator + filing tool that walks through both scenarios with your actual numbers: https://fiesta-mvp.fly.dev/?utm_source=it_twitter&utm_medium=x_post&utm_campaign=launch_2026_05
>
> **4/4** MVP, Sri Lanka-specific, no signup wall on the calc. Looking for feedback from people earning forex who've actually had to navigate this. What's confusing? What did I miss? #TaxSL #lka

---

## Tracking post-launch

`/admin/analytics` (admin login required) → "Per-channel breakout" card.

Within 48-72h of posting, expect:
- `landing_view` counts per `utm_source`
- `signup_started` → `signup_completed` ratios per channel
- First `payment_completed` events from the strongest-fit channel

If a channel produces zero traffic in 72h, that's a signal (post got hidden, mod rejected, audience wrong). If a channel produces traffic but no conversions, the funnel is leaking — look at which step.

**Top-2 prediction (from CHANNELS_v1.md):** Lanka Devs + SL Freelancers FB will out-perform Fiverr SL + IT Twitter on absolute signups, but Twitter has highest viral upside if any influential SL dev RTs.
