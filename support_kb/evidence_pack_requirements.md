---
id: evidence_pack_requirements
topic: ird_evidence_per_remittance
source_url: https://www.ird.gov.lk/en/publications/PublicNoticeIT202501
last_verified: 2026-05-17
---

# Evidence pack — what IRD asks for per remittance

For each foreign-income remittance reported on your return under
PN/IT/2025/01, you must hold the following evidence for at least 6 years
after the relevant year of assessment:

Mandatory:
1. **Bank credit advice / statement entry** showing:
   - Date the credit landed in your SL bank account.
   - Foreign-currency amount and currency code (USD / GBP / etc.).
   - LKR amount the bank actually credited.
   - The bank's conversion rate used.
   - Sender/payer name and (where available) source country.

2. **CBSL middle rate** for the credit date — printable from
   cbsl.gov.lk. The FIESTA app captures and freezes this rate at
   entry time. Print or screenshot at the same time you log the
   remittance.

3. **Source-of-income evidence**:
   - For services: invoice you raised on the foreign payer, OR contract /
     statement of work, OR a payment-platform statement (Upwork, Fiverr,
     Toptal, etc.).
   - For salary: employer's letter / payroll summary / contract.
   - For pension: pension fund statement.
   - For royalties: royalty statement from the licensee.

Recommended (strongly):
4. **Payer's identity** — for AML / anti-fraud questions IRD sometimes
   asks: company registration document, individual passport copy, or
   platform identity (Upwork client URL, etc.). Not required at filing,
   but extremely useful if IRD audits.

5. **Bank account statement** for the full year showing the credit in
   context (regularity of payments, etc.).

What FIESTA stores for you:
- Foreign amount, currency, LKR-at-bank-rate, LKR-at-CBSL, CBSL rate
  used, rate source, payer name, source country (from importer or
  manual entry), tax year. All frozen at entry — never re-fetched.
- A full audit trail (`audit_log`) of every change you make to any
  remittance. This is the chain of custody for the data.

What FIESTA does NOT store (you must keep these yourself):
- The original bank PDF / CSV. After import, FIESTA discards the source
  file (privacy-by-default). Keep your own copy.
- Invoices you raised. FIESTA's invoice module is separate from the
  remittance ledger; if you didn't use FIESTA to raise the invoice, the
  original lives in your email / accounting system.
- CBSL screenshot. FIESTA caches the rate value but does not screenshot
  the CBSL page. For audit comfort, screenshot once a year.

For the full evidence list see PN/IT/2025/01 paragraph 8(c).
