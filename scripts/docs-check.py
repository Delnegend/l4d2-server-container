#!/usr/bin/env python3
"""Validate the documentation against the code it describes.

Docs drift silently: a renamed target, a dropped environment variable or a
retired image tag all keep the prose looking plausible. This asserts the
handful of invariants that are cheap to check and expensive to get wrong.

    python3 scripts/docs-check.py

Standard library only, so it runs anywhere.
"""

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DOCS = [ROOT / "README.md"] + sorted((ROOT / "docs").glob("*.md"))

failures: list[str] = []


def fail(msg: str) -> None:
    failures.append(msg)


def read(path: pathlib.Path) -> str:
    return path.read_text()


# ---------------------------------------------------------------------------
# 1. Every relative markdown link resolves, including its #anchor
# ---------------------------------------------------------------------------
def headings(path: pathlib.Path) -> set[str]:
    found = set()
    for line in read(path).splitlines():
        if line.startswith("#"):
            text = line.lstrip("#").strip()
            found.add(re.sub(r"[^\w\s-]", "", text.lower()).replace(" ", "-"))
    return found


anchors = {path: headings(path) for path in DOCS}

for path in DOCS:
    for _label, link in re.findall(r"\[([^\]]+)\]\(([^)]+)\)", read(path)):
        if link.startswith(("http://", "https://")):
            continue
        if link.startswith("#"):
            if link[1:] not in anchors[path]:
                fail(f"{path.relative_to(ROOT)}: anchor '{link}' has no matching heading")
            continue
        target_path, _, anchor = link.partition("#")
        target = (path.parent / target_path).resolve()
        if not target.exists():
            fail(f"{path.relative_to(ROOT)}: link target does not exist -> {link}")
        elif anchor and target in anchors and anchor not in anchors[target]:
            fail(f"{path.relative_to(ROOT)}: '{link}' - no such heading in {target_path}")


# ---------------------------------------------------------------------------
# 1b. Every document is indexed in the README, and nothing points at a doc that
#     does not exist (the link check above covers the reverse direction).
# ---------------------------------------------------------------------------
readme = read(ROOT / "README.md")
for path in DOCS:
    if path.name == "README.md":
        continue
    if f"docs/{path.name}" not in readme:
        fail(f"README.md: docs/{path.name} is not in the documentation index")


# ---------------------------------------------------------------------------
# 2. The environment variable table is .env.example, and the entrypoint reads it
# ---------------------------------------------------------------------------
entrypoint = read(ROOT / "entrypoint.py")
envexample = set(re.findall(r"^([A-Z_]+)=", read(ROOT / ".env.example"), re.M))
config_doc = read(ROOT / "docs/configuration.md")
documented = set(re.findall(r"^\| `([A-Z_]+)` \|", config_doc, re.M))

for var in sorted(envexample - documented):
    fail(f"docs/configuration.md: {var} is in .env.example but not documented")
for var in sorted(documented - envexample):
    fail(f"docs/configuration.md: {var} is documented but not in .env.example")
for var in sorted(documented):
    if var not in entrypoint:
        fail(f"docs/configuration.md: {var} is documented but entrypoint.py never reads it")

# ---------------------------------------------------------------------------
# 3. Targets: every --target and stage name in the docs exists in the Dockerfile
# ---------------------------------------------------------------------------
dockerfile = read(ROOT / "Dockerfile")
stages = set(re.findall(r"^FROM .* AS (\w+)", dockerfile, re.M))

for path in DOCS:
    text = read(path)
    for target in re.findall(r"--target (\w+)", text):
        if target not in stages:
            fail(f"{path.relative_to(ROOT)}: --target {target} is not a Dockerfile stage")
    # Deliberately no word-list check: `base` and `fetch` are ordinary English,
    # so a prose mention cannot be told from a stale claim. `--target` is the
    # check that actually bites.


# Every stage must be documented, so a new one cannot appear unnoticed.
arch = read(ROOT / "docs/architecture.md")
for stage in sorted(stages):
    if f"`{stage}`" not in arch:
        fail(f"docs/architecture.md: Dockerfile stage '{stage}' is not documented")


# ---------------------------------------------------------------------------
# 4. Build argument defaults in the docs match the Dockerfile
# ---------------------------------------------------------------------------
# Long pins - a depot manifest id, a commit sha, a checksum - are documented
# truncated with an ellipsis, so a documented value is checked as a prefix of
# the declared default rather than against it whole. Allowing digits in the
# name, and matching the combined `A` / `B` rows, is what brings GAME_MANIFEST,
# L4D_PLUGINS_REF and L4DTOOLZ_BUILD under this check at all - and those are
# precisely the pins scripts/apply-bump.py rewrites.
declared = dict(re.findall(r"^ARG ([A-Z0-9_]+)=(.*)$", dockerfile, re.M))
build_arg_row = re.compile(
    r"^\| (`[A-Z0-9_]+`(?: / `[A-Z0-9_]+`)*) \| (`[^`]+`(?: / `[^`]+`)*) \|", re.M
)

for names_cell, values_cell in build_arg_row.findall(arch):
    names = re.findall(r"`([^`]+)`", names_cell)
    values = re.findall(r"`([^`]+)`", values_cell)
    if len(names) != len(values):
        fail(f"docs/architecture.md: build arg row '{names_cell}' does not have one value per arg")
        continue
    for name, value in zip(names, values):
        if name not in declared:
            fail(f"docs/architecture.md: build arg {name} is not declared in the Dockerfile")
        elif not declared[name].startswith(value.removesuffix("…")):
            fail(
                f"docs/architecture.md: {name} default '{value}' "
                f"is not a prefix of Dockerfile '{declared[name]}'"
            )


# ---------------------------------------------------------------------------
# 5. Tag policy: semver + latest only, no per-target tags anywhere
# ---------------------------------------------------------------------------
# The version is derived from the Conventional Commits since the last tag, so
# the check is that the image tag comes from that output and not from
# something typed. The push itself lives in the shared docker-build-push
# action (exactly two tags: the exact version + latest), so here we check the
# call site hands it the right inputs and overrides nothing.
workflow = read(ROOT / ".github/workflows/release.yaml")

build_step = re.search(
    r"- name: Build and push server\n(.*?)(?=\n      - name: |\Z)",
    workflow,
    re.S,
)
if not build_step or "uses: Delnegend/actions/docker-build-push@" not in build_step.group(1):
    fail("release.yaml: must build via the shared docker-build-push action")
else:
    body = build_step.group(1)
    if not re.search(r"uses: Delnegend/actions/docker-build-push@[0-9a-f]{40} # v\d+", body):
        fail("release.yaml: docker-build-push pin must be a full SHA with a version comment")
    image_input = re.search(r"^\s+image:\s*(.+?)\s*$", body, re.M)
    if not image_input or image_input.group(1).strip() != "${{ env.IMAGE }}":
        fail("release.yaml: composite image input must be exactly '${{ env.IMAGE }}'")
    if "needs.prepare.outputs.version_strict" not in body:
        fail("release.yaml: image version must come from the semver-derived version_strict output")
    if "cache: registry" not in body:
        fail("release.yaml: must use the registry cache backend")
    for banned in ("tags:", "outputs:", "cache-from:", "cache-to:"):
        if re.search(rf"^\s+{re.escape(banned)}", body, re.M):
            fail(f"release.yaml: must not set '{banned}' — tags and cache live in the shared action")

# Match the `uses:` line, not the bare name: the workflow explains semver-action
# in prose, so a substring test on "semver-action" passes even if the step that
# actually computes the version has been deleted.
if "uses: ietf-tools/semver-action" not in workflow:
    fail("release.yaml: the version must come from the semver-action step, not a manual input")
# Scope the search to the dispatch block: composite `with:` inputs below may
# legitimately contain a `version:` key, which is not a hand-typed version.
dispatch = workflow.split("\njobs:")[0]
declared_inputs = re.search(r"inputs:(.*)", dispatch, re.S)
if declared_inputs and re.search(r"^\s+version:", declared_inputs.group(1), re.M):
    fail("release.yaml: declares a 'version' input, so someone can type the version by hand")

# A lowercase comparison is useless against `ghcr.io/${{ github.repository }}`:
# the literal text is already lowercase and the capital D only appears after
# expansion. So the value must be a literal with no expressions, and lowercase.
image = re.search(r"^\s*IMAGE:\s*(.+?)\s*$", workflow, re.M)
if not image:
    fail("release.yaml: no IMAGE: line")
else:
    value = image.group(1)
    if "${{" in value:
        fail(
            f"release.yaml: IMAGE '{value}' interpolates an expression; spell "
            f"the image reference out in full lowercase instead"
        )
    elif value != value.lower():
        fail(
            f"release.yaml: IMAGE '{value}' is not lowercase; buildx rejects a "
            f"mixed-case image reference and fails the release on it"
        )

for path in DOCS:
    for tag in re.findall(r"[\w.]+:(?:[\d.]+-(?:qol|coop8|base))", read(path)):
        fail(f"{path.relative_to(ROOT)}: advertises a retired tag '{tag}'")

# ---------------------------------------------------------------------------
# 6. The compression claim in the docs matches the shared build action
# ---------------------------------------------------------------------------
# zstd layers live in Delnegend/actions/docker-build-push, which this repo
# cannot read in CI; the call-site check above guarantees no override. What
# is checkable here is that the docs still describe the implemented policy.
if "zstd level 4" not in arch:
    fail("docs/architecture.md: does not mention the zstd level 4 the shared build uses")

# ---------------------------------------------------------------------------
if failures:
    for f in failures:
        print(f"FAIL {f}")
    print(f"\n{len(failures)} problem(s)")
    sys.exit(1)

print(f"docs-check: {len(DOCS)} files, all invariants hold")
