# Security verification policy

Security checks are release gates, but findings are classified by their effect on
the supported runtime rather than treated as interchangeable scanner output.

## Automated pull-request gates

CI performs these checks with pinned tools and immutable action revisions:

- `pip-audit` checks the locked production dependency export. A known advisory in
  a shipped Python dependency blocks release.
- Gitleaks scans repository history. A verified credential or private key blocks
  release and must be revoked even if it is later removed from Git history.
- Trivy scans the built image for operating-system and Python vulnerabilities,
  secret material, and license findings. High or critical findings block release.
- The image verifier requires UID 10001, the configured `shepherd` user, an
  importable server package, and absence of uv, test/documentation/type-checking
  packages, source trees, lock files, project metadata, and `.env` files.

Scanner failure or database unavailability fails the check; it is not interpreted
as a clean result. CI does not upload raw scan output as a public artifact. Treat
job logs as potentially sensitive and never deliberately test scanners with real
credentials.

## Finding classification

The following findings block a release candidate or stable release:

- any confirmed secret or private key;
- any published advisory reported for a shipped Python dependency;
- any high or critical vulnerability in the final image, whether or not a fix is
  currently available;
- any high or critical license-policy finding;
- a failure of the non-root or runtime-content checks.

Medium and lower image vulnerabilities and license findings require triage but do
not automatically block the initial release. They become blockers when the
affected component is reachable in the supported deployment or the license is
incompatible with distribution. Development-only dependency findings are triaged
separately because those packages are excluded from the image.

An exception requires a linked private security advisory or public issue as
appropriate, affected-version and exploitability analysis, compensating controls,
an owner, and an expiry date. Permanent unreviewed ignore entries are not allowed.
The release notes disclose applicable non-sensitive residual risk.

## Local image verification

Build and inspect the same runtime boundary locally with:

```bash
python3 scripts/dev.py image-check
```

The release workflow must scan the final image digest again because a passing
source or pull-request image scan is not evidence about a separately built image.
