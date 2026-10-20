### 2. Capture the current-target baseline and the contract delta

Run the target on realistic requests. List each contract change the user asks for as the contract delta. Sort every behavior the target has today into kept, when the delta leaves it in place, or retired, naming the change that replaces it. Write the baseline report with kept behaviors as preserved regressions.

### Shared stages

Run stages 3 to 13 with these differences:

- 6: Write the contract as the current contract with the delta and stage 5's decisions applied. Add each change design introduces to the delta, and move each behavior it replaces from the preserved regressions to retired.
- 7: Present each change beside the behavior it replaces, get the user's explicit answer to every change, and record a confirmation whose text names each change it accepts.
- 8: New capability cases cover each change.
- 9: Build the candidate from a copy of the target.
