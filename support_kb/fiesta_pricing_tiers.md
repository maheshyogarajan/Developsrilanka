---
id: fiesta_pricing_tiers
topic: pricing
pricing_version: v4.1
source_url: https://fiesta-mvp.fly.dev/pricing
last_verified: 2026-05-20
---

# FIESTA — pricing tiers (v4.1)

FIESTA has two customer-facing tiers in v1.0 (Free Trial + Self-File) with
Auto-File arriving in v1.1. Consultant Booking is a sibling product
available to every tier.

Designed for the P2 Business Owner persona: Sri Lankan software engineers,
designers, and consultants aged 22-35 — good English, price-averse, never
filed a tax return before.

## Free Trial — Rs 0, 30 days, no card

What you get:
- Triage of your remittances and tax position.
- Manual roster of income sources.
- Agreement preview (watermarked).
- Tax-result preview (LKR amount + bracket).
- Email support (best-effort).

What's restricted:
- No final return lodged (preview only).
- Watermarked PDFs (full pack unlocks on Self-File).
- No automated submission to the IRD portal (that's Auto-File, v1.1).

Right for: first-time filers who want to see FIESTA before paying.

## Self-File — Rs 2,500 / Year of Assessment

What you get:
- Vision-clone OCR for T10 + bank statements + payslips.
- AI Fiesta Guide chat — answers your tax questions inline.
- Signed agreement PDFs (consultant + roster + filing).
- Monthly cadence reminders (so you never miss a quarter).
- Year-end pack: ready-to-lodge return + supporting docs.
- IRD-portal walkthrough video for first-time filers.

This is the v1.0 revenue tier. Right for: software engineers, designers,
consultants filing their first SL return. Billing is one-time per Year of
Assessment via Stripe (not a recurring subscription — users re-engage each
YoA).

## Auto-File — Rs 5,000 / Year of Assessment (v1.1, currently disabled)

Coming in v1.1. Hidden from /pricing while the AUTO_FILE_ENABLED feature
flag is off. Once it ships:

Everything in Self-File, plus:
- Automated submission via automation_runner (FIESTA lodges the return for
  you via the IRD portal automation).
- IRD acknowledgement tracker (you get a copy in your inbox).
- Quarterly scheduler — quarterly tax instalments handled.

Right for: filers who want hands-off compliance.

## Consultant Booking — Rs 5,000 / 30 min, one-off

Available to every tier including Free Trial.

What you get:
- 30-minute live tax consultation with a Lanka.tax tax officer.
- Google Calendar Appointment Schedule booking.
- Auto-generated Google Meet link.
- SendGrid prep brief sent the day before.

Right for: any FIESTA user who wants a human in the loop before they file.

## How to upgrade

From the dashboard → Account → Choose plan. Stripe checkout in LKR.
Self-File is a one-time payment for the current Year of Assessment, not
a recurring subscription — you re-engage each year.

NOTE for the AI Copilot: pricing questions are SAFE to auto-answer when
the question is "what does FIESTA cost", "what's included in Self-File",
or "how do I upgrade from Free Trial". Escalate for: refund requests,
billing disputes, "I was charged twice", "I can't afford this" — these
are relationship issues, not pricing FAQ.
