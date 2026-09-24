# Form rules

### FORM-01: Give every input usable labels and instructions
- Scope: content requiring user input, no exceptions beyond the criterion's scope. Wording/position follow the project, labels/instructions may not disappear.
- Rule: provide labels or instructions sufficient to know what to enter or select. Expose the required relationships programmatically where the applicable semantics require it.
- Check: visible label, accessible name, instruction association, required state, group context, error linkage.
- Sources: [Labels or Instructions](https://www.w3.org/TR/WCAG22/#labels-or-instructions).

### FORM-02: Do not use placeholder text as the only label or instruction
- Scope: text inputs and editable fields. A placeholder may supplement a persistent label when the project permits and contrast remains valid. Styling may vary, visible and accessible identity may not.
- Rule: keep the identifying label and necessary hint persistent outside placeholder-only presentation.
- Check: type a value, purpose/instructions must remain visible and programmatically associated.
- Sources: [GOV.UK text input](https://design-system.service.gov.uk/components/text-input/).

### FORM-03: Identify supported personal-input purposes
- Scope: inputs collecting user information whose purposes appear in WCAG Input Purposes, other fields excluded. Labels may use product terminology, recognized metadata remains when active.
- Rule: expose the recognized purpose through a supported technology such as the applicable autocomplete token.
- Check: inspect metadata and browser autofill recognition, password-manager interoperability needs later proof.
- Sources: [Identify Input Purpose](https://www.w3.org/TR/WCAG22/#identify-input-purpose).

### FORM-04: Group related choices semantically
- Scope: radio/checkbox groups, multi-part values, related controls, genuinely independent controls excluded. Visual grouping may vary, the relationship may not be lost.
- Rule: expose the group label and option relationships programmatically, using native fieldset/legend or an equivalent conforming mechanism.
- Check: linearized content and accessibility tree announce group and each option clearly.
- Sources: [WAI form grouping](https://www.w3.org/WAI/tutorials/forms/grouping/).

### FORM-05: Identify detected errors in text
- Scope: automatically detected input errors, undetected errors fall outside. Voice/placement may vary, identity and textual description remain.
- Rule: identify the field/item and describe the error in text. Do not rely on color alone.
- Check: invalid fixture, field association, error text, accessibility tree, focus behavior, grayscale.
- Sources: [Error Identification](https://www.w3.org/TR/WCAG22/#error-identification).

### FORM-06: Offer a useful correction when it is known
- Scope: automatically detected errors with a known correction, unknown corrections and the security/purpose exception excluded. Wording may vary, the known path may not be hidden.
- Rule: provide the correction suggestion unless it would jeopardize security or the purpose.
- Check: malformed format, range, typo fixtures, correction perceivable and associated.
- Sources: [Error Suggestion](https://www.w3.org/TR/WCAG22/#error-suggestion).

### FORM-07: Preserve recoverable input and context
- Scope: validation, network failure, multi-field forms. Security-sensitive fields or invalid data that must not persist excepted, override needs explicit rationale.
- Rule: avoid clearing valid user input or losing location when an error occurs. Keep recovery actions specific and supported.
- Check: submit mixed valid/invalid fields and simulated failures, verify retained values, focus, retry.
- Sources: [GOV.UK error summary](https://design-system.service.gov.uk/components/error-summary/).

### FORM-08: Retain high-impact process obligations for integration
- Scope: legal/financial commitments, user-data modification/deletion, submitted tests, repeated information, authentication, exact criterion exceptions apply. No override when active.
- Rule: design the necessary review/correction/reversal, reusable prior input, and accessible-authentication affordances, but do not claim proof from an isolated field or button.
- Check: component affordances and states only, complete transaction/authentication/multi-step flow needs later proof.
- Sources: [Error Prevention](https://www.w3.org/TR/WCAG22/#error-prevention-legal-financial-data).
