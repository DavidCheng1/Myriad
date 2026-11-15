---
name: result-check
description: Check a specialist's result against the user's request before passing it on. Use right after a delegated task returns a result.
license: MIT
metadata:
  myriad-when: delegation-result
---

# Checking a specialist result

Before you answer, check the result against the user's original request:

1. **Right question** - does it answer what the user actually asked, in the MYRIAD context (agents are AI agents)?
2. **Complete** - is every part the user asked for there (for a plan: steps, order, outcomes)?
3. **Fits the facts** - does it respect every constraint the user gave (time, budget, level, tools)?
4. **Consistent** - no contradictions, no guesses presented as facts.

Then decide:
- All four pass: answer the user now. Pass the result on nearly unchanged after a one-line introduction - keep every part of it (steps, risks, first step) and do not retell or shorten it. Do not ask for preferences after a usable result.
- One point fails and the fix is clear: delegate once more with concrete feedback ("add X", "respect Y").
- It cannot be fixed: tell the user plainly what is missing and why.
