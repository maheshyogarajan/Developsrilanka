# Terms of Service — FIESTA

**Last updated:** 2026-05-20
**Status:** Draft v0.1 — pending Lanka.tax legal review (returns by 2026-05-27)
**Operator:** Lanka.tax (Private) Limited

---

## 1. Who we are and what this is

FIESTA is a software platform operated by Lanka.tax (Private) Limited, a company
incorporated in Sri Lanka. We help you organise your foreign-income records,
generate tax documents, and (optionally) submit your annual return to the
Sri Lankan Inland Revenue Department (the "Platform").

These Terms of Service ("Terms") form the agreement between you (the "Customer",
"you") and us (Lanka.tax (Private) Limited, "we", "us") when you use the
Platform. By creating an account or paying for a subscription, you accept these
Terms in full. If you do not accept them, do not use the Platform.

The Terms reference our Privacy Policy, which is a separate document. The
Privacy Policy explains how we collect, store, share, and retain your personal
data.

## 2. Your account and what you confirm at signup

By creating an account, you agree:

- You are at least 18 years old and legally capable of entering into this
  agreement under Sri Lankan law.
- The information you enter — your name, NIC, TIN, income figures, counterparty
  details, agreements, invoices, and receipts — is accurate and reflects real
  transactions you have undertaken or are about to undertake.
- You will not use FIESTA to fabricate transactions, backdate documents, or
  otherwise create paper trails for transactions that did not occur. Doing so
  is fraud and is your personal liability, not ours.
- You are solely responsible for the contents of any tax return you file,
  whether you Self-File or Auto-File. FIESTA helps you organise records and
  compute likely tax; the final return is your declaration to the Sri Lankan
  Inland Revenue Department.

## 3. What FIESTA does for you

FIESTA provides software tools that help you:

- Record foreign-income receipts and apply the correct Central Bank of Sri Lanka
  exchange rate.
- Document business expenses across categories the Inland Revenue Department
  typically permits (platform fees, hardware, software, internet, workspace
  rent, professional services, etc.).
- Generate service agreements and rental agreements between you and your
  counterparties, using a clause library and templates reviewed by Sri Lankan
  counsel.
- Maintain a monthly invoice and payment cadence with reminders.
- Produce a year-end pack (filled return, evidence ledger, agreement PDFs,
  computation worksheet).
- Walk you through the IRD online portal screens if you Self-File, or submit on
  your behalf if you Auto-File and we have your IRD credentials and explicit
  per-submission confirmation.

## 4. What FIESTA does NOT do

- **Not legal advice.** Nothing in the Platform — including AI-generated chat
  responses, agreement templates, compliance scoring, or expense suggestions —
  constitutes legal, tax, or accounting advice tailored to your circumstances.
  The Platform applies general rules to your data; it does not weigh facts the
  way a qualified advisor would. Where you need individualised advice, consult
  a licensed Sri Lankan tax practitioner. The Platform's consultant-booking
  surface gives you one route; you are free to use any other.
- **No guaranteed tax outcome.** Compliance scores, savings projections, and
  "expected refund / tax owed" figures are estimates based on your inputs and
  applicable tax rates. They are not predictions of how the
  Commissioner-General of Inland Revenue will assess your return. Final tax
  liability is determined by the IRD.
- **No audit defence.** If the IRD opens an assessment, you are responsible for
  engaging your own representation. FIESTA can export your records to assist,
  but the Platform does not act as your representative before the IRD.
- **No human-staffed support line.** FIESTA is software-first. Customer
  questions are handled by AI chat and the consultant-booking surface. We do
  not offer a phone hotline or general-purpose human-staffed customer service.

## 5. Your IRD credentials and Auto-File

If you choose Auto-File, you give the Platform your IRD portal credentials so
we can log in on your behalf and submit your return. We:

- Store your credentials encrypted in AWS Secrets Manager with a per-customer
  KMS key, accessible only by the automation runner that submits your return.
- Use them only for the submission you have approved.
- Require you to confirm each year's submission individually before the runner
  executes. No silent submissions.
- Will surrender, rotate, or delete your credentials within seven (7) calendar
  days of your written request.

You can revoke Auto-File at any time. After revocation, the next year's return
defaults to Self-File and your credentials are deleted from the secrets store
within seven days.

## 6. Documents you generate

When you generate a service agreement, rental agreement, or invoice through the
Platform, you and your counterparty (the service provider or property owner
you name) are the parties to that agreement. FIESTA is not a party. The
Platform supplies the template; you and your counterparty sign and rely on it.

If the agreement is later challenged — by the IRD, by a counterparty, in court
— that is a matter between the parties. The Platform's role is to produce the
document with the clauses you selected.

## 7. Fees, refunds, and cancellation

Pricing is shown on the Pricing page and confirmed at checkout. Current tiers:

- Free Trial: Rs 0, 30 days.
- Self-File: Rs 2,500 per tax year.
- Auto-File: Rs 5,000 per tax year (includes Self-File scope).
- Consultant booking: Rs 5,000 per 30-minute session, optional, independent of
  subscription tier.

Payments are processed by Stripe. We do not store your card details.

**Refunds.** If you have not generated any tax-related document (agreement,
invoice, year-end pack) and have not submitted a return through Auto-File, you
can request a full refund within 14 days of payment by emailing the address in
§13. After that window, or after document generation/submission, fees are
non-refundable because the work has been done.

**Cancellation.** You can cancel your subscription at any time from your
account settings. Cancellation stops auto-renewal (which is OFF by default —
you must explicitly opt in). Your records remain accessible read-only until
the retention window in §10 expires.

## 8. Your obligations

You agree:

- To keep your account credentials confidential and to notify us immediately
  at the address in §13 if you suspect unauthorised access.
- Not to use the Platform to process anyone else's tax data without their
  consent (e.g. a spouse, a parent, an employee).
- Not to attempt to reverse engineer, scrape at scale, automate against, or
  otherwise misuse the Platform.
- Not to upload content that is illegal, infringing, malicious, or unrelated
  to your tax records (e.g. arbitrary files, malware, copyrighted material you
  don't have rights to).

## 9. Our obligations and limitations

We commit to:

- Operate the Platform with commercially reasonable care, security, and
  availability. We target 99% monthly uptime but do not guarantee it.
- Apply current Sri Lankan tax law as we understand it to your computations,
  and update the Platform within a reasonable time after amendments are
  gazetted. The Platform does not guarantee that all tax-law changes are
  reflected on day one of amendment.
- Notify you of material changes to these Terms or to our Privacy Policy at
  least 14 days in advance, by email and in-app banner.

The Platform is provided "AS IS" and "AS AVAILABLE". To the maximum extent
permitted by Sri Lankan law:

- We make no warranties, express or implied, regarding fitness for a
  particular purpose, accuracy of tax computations beyond best effort,
  uninterrupted availability, or absence of errors.
- Our aggregate liability for any claim relating to the Platform is capped at
  the fees you paid to us in the 12 months preceding the claim. We are not
  liable for indirect, consequential, or punitive damages, including lost
  profits, lost data beyond our backup obligations, regulatory penalties
  imposed on you, or third-party claims arising from your tax return.
- This cap does not apply to liability that cannot be excluded under Sri
  Lankan law (e.g. fraud, gross negligence, or breach that PDPA requires us
  to remedy).

## 10. Data retention and account termination

We retain your records for **seven (7) years** after your last paid tax year,
to give you headroom over the **five-year** statutory retention period set by
section 120 of the Inland Revenue Act No. 24 of 2017. Two-year buffer covers
the assessment-and-appeal window if the IRD opens a late inquiry.

You can:

- Export your records at any time (year-end pack ZIP).
- Request earlier deletion under the Privacy Policy §6, subject to the
  legitimate-interest retention carve-out described there for any year that
  has already been Auto-Filed.

We can terminate your account if you breach §8 (Your obligations) materially
or repeatedly. We give you 30 days' notice and a chance to cure where the
breach is curable. We give no notice in cases of fraud, abuse of the Platform,
or court order. On termination by either side, your data is treated under the
same retention rules as voluntary cancellation.

## 11. Changes to the Platform and these Terms

We can change the Platform's features, pricing, and these Terms. We will
notify you in advance (14 days, by email + in-app) of changes that materially
reduce your rights or raise the price of your existing tier. Other changes
(new features, internal infrastructure, clarifications) take effect on
publication. Continued use after a change is your acceptance of the change.

## 12. Governing law and disputes

These Terms are governed by the laws of Sri Lanka. Disputes are subject to the
exclusive jurisdiction of the courts of Colombo, Sri Lanka. Before filing a
court claim, you agree to give us 30 days' written notice of your complaint at
the address in §13 so we can try to resolve it directly.

Nothing in this clause limits your rights as a consumer under Sri Lankan law
or your right to lodge a complaint with the Sri Lankan Data Protection
Authority under the PDPA.

## 13. Contact

Lanka.tax (Private) Limited
Email: legal@lanka.tax *(provisioning pending)*
Postal: *(Colombo office address — pending Companies Registry update)*
Data Protection Officer: Mahesh Yogarajan, CEO *(interim, pending dedicated appointment)*

## 14. Severability and order of precedence

If any clause of these Terms is held unenforceable, the rest stays in force.
The Privacy Policy referenced in §10, §13 and elsewhere is a separate
document; in case of conflict between this ToS and the Privacy Policy on a
privacy-specific question, the Privacy Policy controls.
