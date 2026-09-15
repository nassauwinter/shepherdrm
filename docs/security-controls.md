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
  secret material, and license findings. Fixable high or critical vulnerabilities,
  secret findings, and forbidden licenses block release.
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
- any fixable high or critical vulnerability in the final image;
- any forbidden license, which Trivy classifies as critical;
- a failure of the non-root or runtime-content checks.

Unfixed vulnerabilities and restricted licenses require review before a release.
They become blockers when the affected component is reachable in the supported
deployment or the license is incompatible with distribution. Trivy maps its
opinionated restricted-license category to high severity, so severity alone does
not establish incompatibility. Medium and lower findings follow the same
reachability and compatibility review. Development-only dependency findings are
triaged separately because those packages are excluded from the image.

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
