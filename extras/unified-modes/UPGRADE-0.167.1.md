# Codex CLI 0.167.1 unified-mode candidate

The fixed-version patch targets official commit
`0a94398fa60c4fac0e7659e4213a0a36e613dd46`. Its SHA256 is
`9442de2721b63bd7281cdaab5fda58c96bf25ff0903cded1f0be11ba5c131d83`.
Unknown versions remain fail-closed; this is not permission to apply this patch
to a newer release. The Desktop-bundled runtime is not replaced.

The twelve-file patch preserves Default → Plan → Full Access, the existing
permissions/collaboration behavior and visible Full Access indication. A native
backend-rejection regression verifies that a rejected transition restores the
prior collaboration mode rather than leaving the UI in Plan.

Evidence from the isolated canonical 0.167.1 source: the complete native TUI
suite finished with 5951 passed, zero failed and four original ignores (5955
total). All 58 failures from the first run were retained and individually
matched to final passing cases. Eight separate fixture-only changes were used
for canonical tests: physical synthetic HOME/short socket paths, test-local
timestamps and URI wrapping. One Full Access snapshot was reviewed for the
intentional preserved behavior. These fixture changes are not this production
patch and do not establish release-binary or interactive runtime acceptance.
The canonical original Cargo.lock remained unchanged; release-version package
normalization is recorded separately, with external dependency pins unchanged.

Parent checks on this delivered file: exact patch hash, reverse-apply check
against the actual patched release source, fixed-version selection and rejection
of an unknown version all pass. Existing user edits to unified_modes.py were
not changed. A signed native release binary, installed wrapper/cache identity,
real three-state interaction and end-to-end acceptance remain separate gates.
Do not label the source delivery as an activated upgrade.
