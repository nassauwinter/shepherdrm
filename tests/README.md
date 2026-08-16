# Test rules

Tests in this directory are written with pytest and should describe observable
behavior rather than implementation details.

## Rules

- Every function whose name starts with `test_` must have a docstring.
- The docstring must state what behavior is being verified and, when useful,
  the important condition or expected result.
- Keep one behavioral intent per test. Split unrelated assertions into separate
  tests so failures identify the broken behavior clearly.
- Prefer Arrange–Act–Assert structure and keep setup local unless a fixture is
  genuinely shared.
- Test public behavior through the narrowest appropriate interface. Avoid
  asserting private implementation details.
- Use descriptive test names; the docstring adds context but does not replace
  a readable name.
- Contract tests belong under `tests/contract/`; unit and integration tests may
  be grouped in similarly named subdirectories as they are added.
- Run the complete suite with `pytest` before submitting changes.

Example:

```python
def test_package_has_version() -> None:
    """The package exposes the expected public version string."""
    assert __version__ == "0.1.0"
```
