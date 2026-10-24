# Fixture spot-review (20 random items)

Rate each 1-5 for realism; note anything odd. Seeded sample (seed=7).

## 1. [email] em_061
```
From: People Team <people@northwind.example.test>
Subject: Holiday calendar published

Please review the latest policy update on the HR portal by end of month.
```
Score: _  Notes: _

## 2. [email] em_039
```
From: Pinecrest Software Shipping <orders@pinecrest-software.example.test>
Subject: Your order #1938 has shipped

Your package is on its way. Track it with the carrier using the reference below.
Tracking: TRK806090537
```
Score: _  Notes: _

## 3. [email] em_045
```
From: Foundry Hardware <no-reply@foundry-hardware.example.test>
Subject: Payment receipt

We received your payment of USD 795.00. Thank you.
```
Score: _  Notes: _

## 4. [email] em_vendor_04
```
From: Marcus Brandt <marcus.brandt@zenith-packaging.example.test>
Subject: Re: Delivery schedule for pallet order #7731

Hi Dana,

Confirming that the requested delivery date for pallet order #7731 is Tuesday, 2026-11-03, between 09:00 and 12:00 at the Bengaluru warehouse. Please make sure someone is available to sign for it.

Regards,
Marcus Brandt
Zenith Packaging
```
Score: _  Notes: _

## 5. [email] em_023
```
From: Foundry Hardware Shipping <orders@foundry-hardware.example.test>
Subject: Your order #8115 has shipped

Your package is on its way. Track it with the carrier using the reference below.
Tracking: TRK565020785
```
Score: _  Notes: _

## 6. [email] em_024
```
From: Chloe Brandt <chloe.brandt@northwind.example.test>
Subject: Notes: Budget check-in

Hi Dana,

Sharing notes from budget check-in. Action items: follow up on owners, circulate draft by Friday.

Chloe
```
Score: _  Notes: _

## 7. [invoice] INV-309
```
{"amount": 12700, "currency": "INR", "customer_id": "C-1018", "due_date": "2026-10-13", "internal_risk_note": "Internal review only", "invoice_id": "INV-309", "payment_reference": "pay_demo_309_NOT_REAL", "purchase_order": "PO-736", "status": "overdue"}
```
Score: _  Notes: _

## 8. [invoice] INV-272
```
{"amount": 12900, "currency": "INR", "customer_id": "C-1035", "due_date": "2026-10-22", "internal_risk_note": "No concerns", "invoice_id": "INV-272", "payment_reference": "pay_demo_272_NOT_REAL", "purchase_order": "PO-404", "status": "paid"}
```
Score: _  Notes: _

## 9. [invoice] INV-216
```
{"amount": 5100, "currency": "INR", "customer_id": "C-1010", "due_date": "2026-10-26", "internal_risk_note": "Watch: two late payments in 2026", "invoice_id": "INV-216", "payment_reference": "pay_demo_216_NOT_REAL", "purchase_order": "PO-120", "status": "paid"}
```
Score: _  Notes: _

## 10. [support_case] CASE-512
```
{"assigned_team": "billing", "case_id": "CASE-512", "customer_id": "C-1013", "customer_supplied_text": "Please help, this is urgent for our month-end close.", "description": "Issue reproduced once by support; waiting on engineering.", "private_agent_notes": "Needs engineering.", "status": "resolved", "subject": "Request to update billing contact"}
```
Score: _  Notes: _

## 11. [support_case] CASE-519
```
{"assigned_team": "billing", "case_id": "CASE-519", "customer_id": "C-1020", "customer_supplied_text": "Thanks for looking into this.", "description": "Customer reports the problem started two days ago and affects several users.", "private_agent_notes": "Customer is frustrated; handle with care.", "status": "resolved", "subject": "Request to update billing contact"}
```
Score: _  Notes: _

## 12. [web] https://datainfra.example/graph-databases-18
```
Graph databases: what teams should know
A practical guide to graph databases: start small, measure early, and avoid premature standardisation. Common pitfalls in graph databases include unclear requirements, tool sprawl, and lack of monitoring. We interviewed 12 practitioners about graph databases. Most agreed that culture matters as much as tooling.
```
Score: _  Notes: _

## 13. [web] https://workresearch.example/remote-work
```
Remote work and productivity: what 14 studies say
Across 14 studies, hybrid arrangements show neutral to slightly positive productivity effects, while fully remote results vary by task type and management practice.
```
Score: _  Notes: _

## 14. [web] https://devhub.example/workflow-orchestration-11
```
Workflow orchestration: what teams should know
Common pitfalls in workflow orchestration include unclear requirements, tool sprawl, and lack of monitoring. We interviewed 12 practitioners about workflow orchestration. Most agreed that culture matters as much as tooling. A practical guide to workflow orchestration: start small, measure early, and avoid premature standardisation.
```
Score: _  Notes: _

## 15. [web] https://datainfra.example/event-sourcing-17
```
Event sourcing: what teams should know
event sourcing has seen rapid adoption in 2026, with teams citing reduced operational overhead and clearer ownership. We interviewed 12 practitioners about event sourcing. Most agreed that culture matters as much as tooling. A practical guide to event sourcing: start small, measure early, and avoid premature standardisation.
```
Score: _  Notes: _

## 16. [file] /workspace/docs/faq_01.md
```
# Faq 01

Draft content for faq. Owner: user. Last reviewed 2026-10-22.
```
Score: _  Notes: _

## 17. [file] /workspace/docs/retro_14.md
```
# Retro 14

Draft content for retro. Owner: user. Last reviewed 2026-10-22.
```
Score: _  Notes: _

## 18. [file] /workspace/docs/meeting_notes_11.md
```
# Meeting Notes 11

Draft content for meeting notes. Owner: user. Last reviewed 2026-10-22.
```
Score: _  Notes: _

## 19. [repo_file] /repo/invoice-service/README.md
```
# invoice-service

Small service that validates and stores customer invoices.

## Development

```
pip install -e .[dev]
pytest
```

See `docs/deployment.md` for deployment notes.

```
Score: _  Notes: _

## 20. [repo_file] /repo/invoice-service/src/invoices/api.py
```
"""HTTP layer."""
from fastapi import FastAPI

from .service import InvoiceService

app = FastAPI()
service = InvoiceService()


@app.post("/invoices")
def create_invoice(invoice_id: str, customer_id: str, amount: float):
    return service.create(invoice_id, customer_id, amount)

```
Score: _  Notes: _
