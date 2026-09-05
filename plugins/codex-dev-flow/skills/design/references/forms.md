# Form rules


### FORM-01: Give every input usable labels and instructions
- Authority/scope: activated WCAG Level A norm.
- Applies: content requiring user input.
- Rule: provide labels or instructions sufficient to know what to enter or select. Expose the required relationships programmatically where the applicable semantics require it.
- Exceptions: none beyond the criterion's scope.
- Project override: wording and position follow the project. Labels/instructions may not disappear.
- Isolated check: visible label, accessible name, instruction association, required state, group context, and error linkage.
- Sources: [Labels or Instructions](https://www.w3.org/TR/WCAG22/#labels-or-instructions), [Info and Relationships](https://www.w3.org/TR/WCAG22/#info-and-relationships).

### FORM-02: Do not use placeholder text as the only label or instruction
- Authority/scope: official GOV.UK design-system convention and broad heuristic, not a universal WCAG sentence.
- Applies: text inputs and editable fields.
- Rule: keep the identifying label and necessary hint persistent outside placeholder-only presentation.
- Exceptions: a placeholder may supplement a persistent label when the project convention permits it and contrast remains valid.
- Project override: project styling may vary. The component still needs an understandable visible and accessible identity.
- Isolated check: type a value and verify purpose/instructions remain visible and programmatically associated.
- Sources: [GOV.UK text input](https://design-system.service.gov.uk/components/text-input/), [WCAG Labels or Instructions](https://www.w3.org/TR/WCAG22/#labels-or-instructions).

### FORM-03: Identify supported personal-input purposes
- Authority/scope: activated WCAG 2.2 AA norm.
- Applies: inputs collecting information about the user whose purposes appear in WCAG Input Purposes.
- Rule: expose the recognized purpose through a supported technology such as the applicable autocomplete token.
- Exceptions: arbitrary fields outside the listed purposes.
- Project override: labels may use product terminology. Recognized purpose metadata remains when active.
- Isolated check: inspect metadata and browser autofill recognition.
- Later proof: password manager and browser interoperability.
- Sources: [Identify Input Purpose](https://www.w3.org/TR/WCAG22/#identify-input-purpose).

### FORM-04: Group related choices semantically
- Authority/scope: activated information/relationship outcome. Native grouping is a preferred technique.
- Applies: radio groups, checkbox groups, multi-part values, related controls.
- Rule: expose the group label and option relationships programmatically, using native fieldset/legend or an equivalent conforming mechanism.
- Exceptions: genuinely independent controls.
- Project override: visual grouping may vary. The relationship may not be lost.
- Isolated check: linearized content and accessibility tree announce group and each option clearly.
- Sources: [WCAG Info and Relationships](https://www.w3.org/TR/WCAG22/#info-and-relationships), [WAI form grouping](https://www.w3.org/WAI/tutorials/forms/grouping/).

### FORM-05: Identify detected errors in text
- Authority/scope: activated WCAG Level A norm.
- Applies: automatically detected input errors.
- Rule: identify the field/item and describe the error in text. Do not rely on color alone.
- Exceptions: only errors not automatically detected fall outside this criterion.
- Project override: voice and placement may vary. Identity and textual description remain.
- Isolated check: invalid fixture, field association, error text, accessibility tree, focus behavior, and grayscale.
- Sources: [Error Identification](https://www.w3.org/TR/WCAG22/#error-identification), [GOV.UK error message](https://design-system.service.gov.uk/components/error-message/).

### FORM-06: Offer a useful correction when it is known
- Authority/scope: activated WCAG 2.2 AA norm.
- Applies: an automatically detected error with a known correction suggestion.
- Rule: provide the correction suggestion unless it would jeopardize security or the purpose.
- Exceptions: unknown corrections and the security/purpose exception.
- Project override: wording may vary. The known useful path may not be hidden.
- Isolated check: malformed format, range, and typo fixtures. Verify correction is perceivable and associated.
- Sources: [Error Suggestion](https://www.w3.org/TR/WCAG22/#error-suggestion).

### FORM-07: Preserve recoverable input and context
- Authority/scope: heuristic/design-system convention.
- Applies: validation, network failure, and multi-field forms.
- Rule: avoid clearing valid user input or losing location when an error occurs. Keep recovery actions specific and supported.
- Exceptions: security-sensitive fields or invalid data that must not persist.
- Project override: yes with explicit security/product rationale.
- Isolated check: submit mixed valid/invalid fields and simulated service failures. Verify retained values, focus, and retry behavior.
- Sources: [GOV.UK error summary](https://design-system.service.gov.uk/components/error-summary/), [USWDS form guidance](https://designsystem.digital.gov/components/form/).

### FORM-08: Retain high-impact process obligations for integration
- Authority/scope: activated WCAG 2.2 AA norms at complete-process scope.
- Applies: legal/financial commitments, user-data modification/deletion, submitted tests, repeated information across steps, and authentication.
- Rule: design the necessary review/correction/reversal, reusable prior input, and accessible-authentication affordances, but do not claim proof from an isolated field or button.
- Exceptions: the exact security, essential, invalid-information, and criterion exceptions.
- Project override: no when active.
- Isolated check: component affordances and states only.
- Later proof: complete transaction/authentication/multi-step flow with password managers, recovery, correction, and reversal where applicable.
- Sources: [Error Prevention](https://www.w3.org/TR/WCAG22/#error-prevention-legal-financial-data), [Redundant Entry](https://www.w3.org/TR/WCAG22/#redundant-entry), [Accessible Authentication](https://www.w3.org/TR/WCAG22/#accessible-authentication-minimum), [WCAG complete processes](https://www.w3.org/TR/WCAG22/#complete-processes).
