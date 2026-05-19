# Privacy Policy — FIESTA

**Last updated:** 2026-05-20
**Status:** Draft v0.1 — pending Lanka.tax legal review (returns by 2026-05-27)
**Compliance frame:** Sri Lankan Personal Data Protection Act, No. 9 of 2022 (PDPA)

---

## 1. The short version

- We collect the minimum data needed for the thing you're doing, when you're
  doing it. NIC and TIN are not asked at signup; they're asked when you
  generate your first tax-related document.
- We never sell your data and never share it with marketing or analytics third
  parties.
- We share with payment, OCR, and AI-chat providers only what is necessary for
  the feature you used. They process under written contracts that bind them
  to Sri Lankan PDPA standards.
- You can export, correct, or delete your records. Deletion has a
  legitimate-interest carve-out for years we have already Auto-Filed on your
  behalf (because Sri Lankan law requires us to keep that submission record
  for five years).
- We notify the Data Protection Authority and you within statutory deadlines
  if there is a data breach affecting your personal data.

## 2. Who is the controller

The controller of your personal data is Lanka.tax (Private) Limited, the
operator of FIESTA. Contact: legal@lanka.tax *(provisioning pending)*. Data
Protection Officer: Mahesh Yogarajan, CEO *(interim)*.

This Privacy Policy is written under the Sri Lankan Personal Data Protection
Act, No. 9 of 2022 ("PDPA") and the rights it grants you as a data subject.

## 3. What data we collect, and when

We collect data in three waves:

**(a) At signup (Trial start):**

- Email address.
- A password (stored hashed, never readable to us).
- Your answers to the three triage questions (e.g. "is your client a foreign
  company?"). These don't identify you; they shape what features you see.
- Standard web analytics that don't identify you personally (page paths,
  device class, country at city level only).

We do not ask for NIC, TIN, bank details, or income figures at signup.

**(b) When you generate your first tax document:**

- Your full name (English; optionally Sinhala or Tamil spelling for legal
  documents).
- Your National Identity Card (NIC) number.
- Your Taxpayer Identification Number (TIN), if you have one.
- Your contact phone number (for IRD-portal multi-factor codes if you
  Auto-File).
- Your tax-year income receipts (amount, date, source platform).
- Your service-provider and property-owner counterparties' names, NICs, and
  addresses (because the agreement and the deductibility require they be
  named).

**(c) When you choose Auto-File:**

- Your IRD portal username and password (stored encrypted; see §5 below).

We collect no special-category data (health, religion, biometric, genetic,
sexual orientation, political views, trade-union membership) and have no
business reason to. If you ever volunteer such data in a free-text field (e.g.
chat with the AI assistant), we treat it as non-personal-data input to the
model and do not classify or store it as a special category.

## 4. Why we collect each type — the lawful basis

| Data | Why | PDPA lawful basis |
|---|---|---|
| Email + password | Account access | Contract performance |
| Triage answers | Feature gating | Contract performance |
| Name / NIC / TIN | Tax-document accuracy + IRD compliance | Legal obligation (IRA §120) + Contract performance |
| Counterparty NIC / address | Required by the agreement template | Contract performance + Legitimate interest of the data subject and counterparty |
| Income figures + receipts | Tax computation | Contract performance + Legal obligation |
| IRD credentials | Auto-File submission | Explicit consent (you can revoke any time) |
| Phone (if shared) | IRD MFA | Explicit consent |
| Analytics (de-identified) | Product improvement | Legitimate interest |

## 5. How we store and protect your data

- **Postgres + S3.** Account data and document metadata in a managed Postgres
  database. Raw agreement and invoice PDFs in S3 with server-side encryption
  (SSE).
- **Encryption in transit.** TLS 1.2+ everywhere. HSTS enforced.
- **IRD credentials.** Stored in AWS Secrets Manager with a per-customer KMS
  key. Only the automation runner Lambda decrypts them, only at submission
  time, only after your per-submission confirmation. They never appear in
  application logs.
- **Access control.** Production access is limited to a named list of
  personnel under a documented least-privilege policy. All admin actions are
  audit-logged.
- **Backups.** Daily encrypted backups, 30-day rolling retention.
- **Document upload scanning.** Every document you upload is MIME-checked and
  virus-scanned before storage.

## 6. Data residency

By default, our infrastructure runs on **Fly.io** (application servers,
Singapore region `sin`) and **AWS** (secrets, document storage, automation
runner, Singapore `ap-southeast-1`).

**This means your personal data may be stored outside Sri Lanka.** Under PDPA
Part V, cross-border transfer is permitted when (a) you have given consent,
(b) the transfer is necessary to perform the contract you signed up for, or
(c) the controller imposes binding safeguards on the recipient.

Our position: cross-border processing to AWS Singapore and Fly.io Singapore is
necessary to perform the contract (running the Platform). We impose binding
contractual safeguards on each processor (see §7). When the Sri Lankan Data
Protection Authority issues an adequacy framework, we will align.

## 7. Who processes your data on our behalf

Each of the following is a "processor" under PDPA §15. We have or will have a
written contract with each, binding them to confidentiality, security, breach
notification, and limited-purpose use.

| Processor | What they process | Why |
|---|---|---|
| **Fly.io** (USA company, Singapore region for our infrastructure) | Application servers, web traffic | Hosting |
| **AWS** (Singapore region) | Document storage (S3), secrets (Secrets Manager + KMS), automation runner (Lambda), monitoring (CloudWatch) | Storage + compute |
| **Supabase** (USA) | Postgres database | Database hosting |
| **Stripe** (USA, with regional sub-processors) | Card payment details (we never see them) | Payment processing |
| **Google LLC** (USA — Gemini) | Document images you upload for OCR (e.g. T10 forms, invoices) | OCR. Outputs are returned to us; Google does not use your data to train its general models per its enterprise terms. |
| **Anthropic PBC** (USA — Claude) | AI chat conversations | AI assistant. Same enterprise-terms-no-training position. |
| **SendGrid (Twilio Inc.)** (USA) | Outbound email content | Email delivery for receipts, reminders, agreement co-sign |
| **Salesforce** (USA / Singapore) | Mirror of your structured records, for cross-Lanka.tax visibility if you also use Lanka.tax consultant services | Operational continuity |

We do not share your data with any party not listed above, except (a) where
you direct us to (e.g. you book a consultant — your data is shared with that
consultant only with your explicit consent at booking), (b) where required by
Sri Lankan law (e.g. a Commissioner-General request under IRA Part XI), or
(c) where compelled by court order. If a law-enforcement request comes in, we
attempt to notify you unless we are legally barred.

## 8. Your rights under PDPA

You have the right to:

- **Access** your personal data. Use the export function in your account
  settings, or email us — we respond within 21 business days as required by
  PDPA §11.
- **Rectify** inaccurate data. Edit it in your profile, or ask us to.
- **Erasure** ("right to be forgotten"). Subject to §10 below.
- **Restrict processing** while a dispute about accuracy or legitimate
  interest is being resolved.
- **Object to processing** based on legitimate interest.
- **Withdraw consent** at any time where consent was the legal basis (e.g.
  IRD credentials, phone for MFA). Withdrawal stops future processing,
  doesn't undo past lawful processing.
- **Data portability.** Your year-end pack ZIP is your export format; ask us
  if you need another structured format.
- **Lodge a complaint** with the Sri Lankan Data Protection Authority. Their
  contact is at https://www.dpa.gov.lk/.

We do not charge fees for exercising these rights, unless your requests
become manifestly unfounded or excessive (PDPA permits a reasonable fee in
that case).

## 9. How long we keep your data — the retention table

| Data | Retention | Why |
|---|---|---|
| Account email, password hash | While your account is open + 30 days after deletion | Account recovery + grace |
| Tax records for a year we Auto-Filed | 7 years from end of that tax year | IRA §120 mandates 5 years; +2 years buffer covers assessments + appeals |
| Tax records for Self-File years | 7 years OR earlier on your deletion request | You hold the legal obligation; we keep them for your convenience but defer to your direction |
| IRD credentials | While Auto-File is on + 7 days after off | Submission performance |
| Card details | Never (Stripe holds them) | n/a |
| Audit logs (admin actions) | 5 years | Internal security |
| Server logs (de-identified) | 90 days | Operations |
| AI chat transcripts | 30 days, or longer if you flagged the conversation as a saved note | Debug + service quality; you can delete on demand |

## 10. Deletion — the carve-out

For tax records relating to a year that **we have Auto-Filed on your behalf**,
we retain the submission package and supporting records for 7 years (per §9
above) even if you ask us to delete them earlier. Reason: Sri Lankan tax law
§120 obligates retention for 5 years; the IRD can open assessment proceedings
against your filing during that window; and the submitter (us) may be asked
to produce the records. We rely on the PDPA's legitimate-interest carve-out
for this category.

For everything else — Self-File years, abandoned drafts, expired triage
answers, deleted accounts — we honour your deletion request within 21
business days (PDPA §11) and confirm completion by email.

## 11. Breach notification

If we identify a breach affecting your personal data, we will:

- Notify the Sri Lankan Data Protection Authority within the statutory window
  once it is fixed by the relevant Order under the Act (substantive
  provisions await commencement Order; we apply best practice today, formal
  compliance once in force).
- Notify you by email within 72 hours of the breach being identified, with
  what happened, what data was affected, what we are doing, and what you can
  do.
- Maintain a public incident page where material outages and breaches are
  logged.

We do not notify for trivial events that pose no risk to your rights (e.g.
an attempted login from an unknown IP that we blocked).

## 12. Cookies and tracking

We use:

- A session cookie (required) — `HttpOnly`, `Secure`, `SameSite=Lax`,
  expires when you log out.
- A CSRF token cookie (required).
- No third-party analytics cookies. No advertising trackers. No Facebook
  Pixel. No Google Analytics.

Our internal product analytics is server-side, de-identified, and aggregated.

## 13. Children

FIESTA is for adults (18+) earning foreign income. We do not knowingly collect
data from anyone under 18. If we discover we have, we delete it.

## 14. Changes to this Privacy Policy

We will notify you of material changes (new processor category, change of
data retention period, new lawful basis) at least 14 days in advance by email
and in-app banner. We log every version on the published page so you can see
what changed.

## 15. Contact

Data Protection Officer: Mahesh Yogarajan, CEO *(interim)*
Email: privacy@lanka.tax *(provisioning pending)*
Postal: *(Colombo office address — pending Companies Registry update)*

For PDPA complaints, you can also contact the Data Protection Authority of
Sri Lanka at https://www.dpa.gov.lk/.
