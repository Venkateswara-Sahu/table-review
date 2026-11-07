## Summary

What problem does this change solve, and for whom?

## Changes

- 

## Testing

List the commands you ran and their result.

```powershell
uv run --frozen --extra extraction --group dev ruff check src evaluation tests scripts
uv run --frozen --group dev pytest tests/domain tests/integration -q
```

## Checklist

- [ ] The change is focused and does not include unrelated refactoring.
- [ ] Tests cover the new behaviour or the bug fix.
- [ ] Documentation is updated where behaviour, configuration or limits changed.
- [ ] No private report data, credentials, keys or local deployment files are included.
- [ ] Accuracy, privacy and hosting claims are consistent with the recorded evidence.
