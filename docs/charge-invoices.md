# Invoices screen, charges (fines and other costs), reminders and shop VAT

## Finance → Invoices: every invoice in one place

**Finance → Invoices** lists all invoices: registrations, shop invoices and **charges** (fines and other costs written by hand). Filter by kind (shown when you may see more than one), by status (drafts, open, overdue, paid, cancelled) and search by member, number or description. The cards show what is open and overdue. A row opens the screen of its own kind (the registration's finance page, the shop order, the charge), and nothing is edited in the list itself. Each kind only shows to people who may see it: registrations need the registration finance permission, shop invoices the shop invoices permission (and the shop), charges the charge permission.

## Charges

Staff can write an invoice to a member for any cost that is not a shop item or a membership fee: a fine, a late fee, lost equipment. Start with **New charge** on the Invoices screen. It needs its own permission, **Manage charge invoices (fines and other costs)** (`finance.manage_charges`). The club's Owner role and the platform owner have it without being given it. Give it to other roles under **Settings → Roles**. It needs no shop.

## How it works

1. **Draft.** Choose the member and add lines (a description, a quantity and an amount each). Optionally set a due date, a **text on the invoice** (printed on the PDF and repeated in the email, for example the reason or the match), **payment instructions** for this invoice only (empty means the club's standard instructions from the club settings), and an internal note that only staff see. Nothing is sent. A draft can be changed, or deleted.
2. **Send.** *Send to member* issues the invoice (a number from the shop's invoice sequence, the due date, the club's billing details as they are now). The member, and for a child the parents or guardians, get an inbox message and push notification, plus an email with the PDF attached, each in their own language. It shows on the member app's **Home** in a prominent *Open invoices* card (red edge when something is overdue, with the amount due and a PDF button) and on the **Payments** tab. PDFs download as the invoice number (for example `INV-2026-00004.pdf`). After sending, the lines are fixed: to change one, cancel it and write a new one.
3. **Payments.** Record cash, bank transfer or card payments on the invoice. It turns *partly paid* and then *paid*. Vouchers cannot pay a charge invoice.
4. **Actions.** On a sent invoice the right-hand column has the totals and the actions: download the PDF, send a reminder, cancel. Only an invoice with no payments can be cancelled (delete the payments first), and the member is told. Payments are listed in a table below the invoice.

Charge invoices carry **no VAT**. They are kept apart from the shop: they never appear in the shop's orders, invoices, statistics or pickup lists, and the hidden product behind their lines is not in the catalogue.

## Reminders for overdue invoices

A daily job (Celery task `shop.send_charge_reminders`, 08:00, visible under Control panel → Jobs) reminds the member by email and in the app. Under **Finance → Invoices → Reminders** you set:

- whether reminders are on,
- how many days after the due date the first one goes out (default 3),
- how often to repeat (default every 7 days),
- the most reminders per invoice (default 3),
- the default payment term for an invoice without its own due date (default 14 days, the same setting as the shop's invoice terms).

Nothing is reminded about invoices that are paid or cancelled, or drafts. Staff can also send a reminder at once from an invoice. That counts towards the maximum.

## VAT on shop items

In **Shop → Settings → VAT** (only whoever manages the whole shop can change it):

- **No VAT** (the default): VAT appears nowhere. Products have no VAT rate and invoices are unchanged.
- **Prices include VAT**: shop prices already contain VAT. Each product gets a **VAT rate (%)**, and the invoice shows how much of the price is VAT, per rate (at 21%, a price of 12.10 shows 10.00 plus 2.10 VAT; totals and payments do not change). The VAT lines only appear when the club has a **VAT number** on file (kept on the control panel). A discount is spread over the lines in proportion before the VAT is taken out.

Prices entered *excluding* VAT, with VAT added on top, are not supported. Before switching it on, check that your shop prices really are the amounts members pay. An invoice remembers the setting it was issued under: switching it later never changes invoices already sent.

## Operations

- The release adds database migrations (`shop` 0012 to 0014): they run with the deploy like any other.
- The new permission appears in the roles screen after the migration. The daily reminder job is created by the scheduler the first time it starts (like the other jobs).
- Checked by the automated tests (`api/tests_charges.py`) and the full suite. The screens have not been looked at in a browser yet, and the PDF layout has not been reviewed by eye: send a test invoice to yourself first.
