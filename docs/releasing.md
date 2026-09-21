# Publishing a release

Publishing is driven only by a protected Git tag beginning with `v`. The tag
must equal the package version in `pyproject.toml`, or that version followed by
a prerelease suffix. For example, `v0.1.0-rc.1` and `v0.1.0` are valid for this
repository's current package version.

Before publishing, configure the GitHub `release` environment to require the
maintainer approval appropriate to this repository. Protect the `v*` tag
namespace with a repository ruleset so only authorized maintainers can create
release tags. Store the Docker Hub account name in the `DOCKERHUB_USERNAME`
Actions secret and a scoped Docker Hub access token in the `DOCKERHUB_TOKEN`
Actions secret. Never place either value in repository variables or tracked
files.

The Release workflow builds and pushes the version tag and a source-revision
tag to Docker Hub, then pulls the resulting immutable digest. It runs the
runtime-boundary and production deployment/recovery checks against that digest,
performs the final image vulnerability and secret scan, creates a provenance
attestation, generates an SPDX SBOM, and only then creates the GitHub release.
Prerelease tags create GitHub prereleases. Stable releases do not currently
publish a mutable `latest` tag.

After the workflow succeeds, use the digest shown in the GitHub release for a
clean-host acceptance run. Record the result in the External clean-host record
section of [release acceptance](release-acceptance.md).
