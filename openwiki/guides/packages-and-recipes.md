---
type: guide
title: Pipeline Packages and Signed Recipes
description: Immutable release formats, trust checks, activation, rollback, and configuration validation.
status: active
tags:
  - packages
  - recipes
  - configuration
---

# Pipeline packages and signed recipes

## Pipeline packages

Pipeline releases are ZIP archives no larger than 2 GB. They contain `manifest.json`, `pipeline/`, and `config.schema.json`, with optional `models/` and `preprocessing/` content. The manifest pins the entry point, code checksum, compatible platform range, required capabilities, dependencies, and Ed25519 approval.

Archive inspection rejects path escapes, duplicate paths, symbolic links, encrypted entries, incompatible platform versions, missing entry points, checksum mismatches, and invalid signatures. The application additionally pins the configured owner's public key. Approval never overwrites an existing destination.

Registration copies the exact archive into immutable hash-addressed local storage. Activation and rollback are allowed only in `idle`, use unique request IDs, and write audit records.

## Recipe releases

Recipes are separate signed JSON releases limited to 512 KB. Each release pins:

- recipe ID and semantic version;
- exact pipeline name, version, and code checksum;
- configuration JSON;
- owner approval signature.

Registration loads `config.schema.json` from the exact registered package and validates the configuration with `jsonschema 4.26.0`. External schema references are forbidden; local `#` references are allowed. A recipe signed by another key or targeting an unregistered/different pipeline is rejected.

Recipe activation is allowed only when its exact pipeline package is active and the supervisor is `idle`. Previous approved versions are retained for one-command rollback.

Routes:

- `POST /api/v1/packages/register`
- `POST /api/v1/packages/{name}/{version}/activate`
- `POST /api/v1/packages/rollback`
- `POST /api/v1/recipes/register`
- `POST /api/v1/recipes/{id}/{version}/activate`
- `POST /api/v1/recipes/rollback`
- `GET /api/v1/recipes/active`
