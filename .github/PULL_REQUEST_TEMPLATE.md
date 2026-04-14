<!-- Thanks for contributing. Fill this out honestly. -->

## Summary

<!-- One or two sentences. What does this change and why? -->

## Linked issue

<!-- Closes #123, Refs #456. If there is no linked issue for a non-trivial change, open one first. -->

## Type of change

- [ ] Bug fix
- [ ] Documentation
- [ ] Test coverage
- [ ] Lint / format / refactor
- [ ] Dependency bump
- [ ] New feature (design discussed in linked issue: ___)
- [ ] Security fix (if yes, was this reported privately first? see SECURITY.md)

## Scope and safety

- [ ] This does not weaken consent, the Release Forever valve, or the 150-escape factory reset
- [ ] I added / updated tests (required for `shared/`, crypto, payment, mesh, enforcement)
- [ ] I ran `ruff check . && ruff format --check . && mypy shared && pytest tests/` locally and everything passes
- [ ] I updated `CHANGELOG.md` under `## [Unreleased]` if user-visible

## AI disclosure

- [ ] I did not use an AI assistant for this change
- [ ] I used an AI assistant for part of this change and reviewed every line (specify below)

<!-- If AI-assisted: which tool, for what portion, what did you verify? -->

## Test evidence

<!-- Paste test output, screenshots, or a link to a CI run. "Works on my machine" is not sufficient for enforcement-path changes. -->

## Breaking changes

<!-- Does this change a config field, an endpoint contract, an APK signing key, or anything users will notice? If yes, call it out and explain the migration path. -->
