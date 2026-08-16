# Test rules

Tests in this directory are written with pytest and should describe observable
behavior rather than implementation details.

## Rules

- Every function whose name starts with `test_` must have a docstring.
- The docstring must state what behavior is being verified and, when useful,
  the important condition or expected result.
- Keep one behavioral intent per test. Split unrelated assertions into separate
  tests so failures identify the broken behavior clearly. Multiple assertions
  are appropriate when they jointly verify one response or behavioral outcome.
- Assert successful prerequisite operations before consuming their response
  bodies or starting dependent operations. This keeps failures close to their
  cause instead of surfacing later as missing fields or unrelated errors.
- Prefer Arrange–Act–Assert structure and keep setup local unless a fixture is
  genuinely shared.
- Test public behavior through the narrowest appropriate interface. Avoid
  asserting private implementation details.
- Use descriptive test names; the docstring adds context but does not replace
  a readable name.
- Contract tests belong under `tests/contract/`; unit and integration tests may
  be grouped in similarly named subdirectories as they are added.
- Run `make check` before submitting changes.

## Test support and fixtures

- Keep suite-wide fixtures and pytest configuration in `tests/conftest.py`.
- Put fixtures used only by one test category in that category's `conftest.py`.
  For example, PostgreSQL and identity fixtures belong in
  `tests/integration/conftest.py` rather than being exposed to unit and contract
  tests.
- Test modules should focus on tests, their decorators, and test-specific
  constants. Put reusable helper functions, data holders, and coordination code
  in a clearly named support module such as `tests/integration/support.py`.
- Do not turn every local setup step into a fixture. Use a fixture when it
  represents shared test context or owns a resource lifecycle; use a support
  function for reusable behavior that does not provide fixture state.
- A fixture that creates database records owns their cleanup. Start its cleanup
  protection before the first committed write, use unique identifiers, and
  delete only records created by that fixture.

## Async tests and parametrization

- Mark async tests with `pytest.mark.anyio`. The suite-wide `anyio_backend`
  fixture selects asyncio, so ordinary asyncio APIs remain available.
- `pytest.mark.parametrize` works normally with async tests. Pytest creates one
  test case per parameter set, and AnyIO awaits each invocation.
- Parametrize cases that perform the same action and expect the same kind of
  outcome for different inputs. Split sequential workflows or unrelated
  behaviors into separately named tests instead of representing their steps as
  parameters.
- Give parameter sets descriptive IDs so collected tests and failures explain
  the condition being exercised.

Example:

```python
@pytest.mark.anyio
@pytest.mark.parametrize(
    ("actor", "expected_status"),
    [("anonymous", 401), ("user", 403)],
    ids=["authentication-required", "administrator-required"],
)
async def test_user_administration_requires_an_administrator(
    identity_environment: IdentityEnvironment,
    identity_client: httpx.AsyncClient,
    actor: str,
    expected_status: int,
) -> None:
    """Non-administrators cannot access user administration."""
    headers = {} if actor == "anonymous" else identity_environment.authorization("user")
    response = await identity_client.get("/v1/users", headers=headers)

    assert response.status_code == expected_status
```

## Integration tests

- Mark tests that require PostgreSQL or another external service with
  `pytest.mark.integration`.
- PostgreSQL tests use `SHEPHERD_TEST_DATABASE_URL` and skip when it is not
  configured. Set it when validating migrations, constraints, transactions,
  audit atomicity, or concurrency behavior.
- Prefer database-observed synchronization for concurrency tests. Do not rely
  on a fixed sleep to assume that another transaction has reached a lock.

Example:

```python
def test_package_has_version() -> None:
    """The package exposes the expected public version string."""
    assert __version__ == "0.1.0"
```
