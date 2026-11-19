Invoice processing: from PDF to payment decision

A mid-size company receives hundreds of vendor invoices every month, usually as email attachments. Accounts payable staff open each invoice, find its purchase order (PO), compare the amounts, check whether the invoice has already been paid or submitted, and decide what should happen next. The work is repetitive, and a missed discrepancy can lead to an overpayment or paying the same charge twice.

Invoices vary widely. Some are clean, machine-readable PDFs; others are scanned images. Line items may be itemized or bundled, tax may be included or listed separately, and a PO reference may be explicit or absent. Critical details such as invoice number, date, currency, or total may be missing or unreadable. Arithmetic can also be wrong: line items may not add up to the subtotal, or subtotal plus tax may not equal the stated total.

The procurement and payment records have their own rules: approved vendors, PO limits, received amounts, prior invoices, payments already made, and allowed tolerances. A PO may be billed in several installments, so each invoice must be compared with the remaining unbilled balance. A vendor may also submit an invoice for an amount that has already been paid, request more than the agreed PO or contract amount, or send a total that differs from the amount supported by the line items.

Build

Create a process that accepts an invoice and returns a clear, explainable decision. Show the extracted information, the records and rules used for comparison, each validation result, and the reason for the final decision. Let a reviewer correct extracted fields and run the checks again.

The process should:

- Extract the vendor, invoice number, invoice date, currency, PO reference, line items when available, subtotal, tax, and total due.
- Validate required fields, date and currency, and arithmetic across line items, subtotal, tax, and total. Flag missing, ambiguous, or inconsistent information instead of guessing silently.
- Match the invoice to an approved vendor and PO. Show the PO amount, previous billed or paid amounts, and remaining balance when those records are available.
- Check for duplicate files and repeated vendor/invoice-number combinations, including invoices previously submitted or paid.
- Compare the requested amount with the remaining PO or contract balance and configured tolerance. Flag invoices that exceed the agreement, exceed the remaining balance, or request payment for an amount already settled. Account for legitimate partial invoices without treating the original PO total as the remaining balance.
- Return a decision such as approve, review, or duplicate, with specific reasons and an audit trail. Route uncertainty and exceptions to a person; do not initiate payment automatically.

Edge cases to handle

1. Calculation error: line-item extensions or their sum are incorrect.
2. Incorrect invoice total: the stated total does not equal subtotal plus tax, discounts, fees, or other stated adjustments.
3. Previously paid amount requested again: compare the invoice and its charges against payment history and flag a repeat request.
4. Request exceeds the agreed amount: flag charges above the PO/contract cap or above its remaining unbilled balance, even when the invoice arithmetic is internally consistent.
5. Split or partial billing: recognize that multiple valid invoices can draw against one PO, while preventing cumulative billing beyond its limit.
6. Near match or ambiguity: vendor, PO, currency, date, or invoice number is missing or does not uniquely identify a record; request review rather than choosing silently.
7. Duplicate with changed details: detect a repeated invoice number or file even if the amount, filename, or other extracted fields differ.
8. Insufficient source records:if payment history, PO balance, or contract data is unavailable, state that the check could not be completed and route for review.

You decide the extraction and validation approach, the rules and tolerances, and the presentation of the decision. Every outcome should make clear what was checked, what evidence was used, what failed or could not be verified, and what action is recommended.
