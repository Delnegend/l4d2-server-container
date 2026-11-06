#!/usr/bin/env python3
"""Check whether a new Left 4 Dead 2 official dedicated server release exists.

Queries api.steamcmd.net for the latest public build ID and depot manifests of
Left 4 Dead 2 Dedicated Server (App ID 222860). Compares the build ID against
existing `vanilla-*` releases on GitHub to determine if a new vanilla archive
must be packaged and released.

Emits KEY=VALUE lines on stdout for GitHub Actions GITHUB_OUTPUT:
    release_needed=true|false
    build_id=<id>
    game_manifest=<id>
    launcher_manifest=<id>
    existing_tag=<tag>

Usage:
    python3 scripts/check-vanilla.py [--force]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

APP_ID = 222860
# Depot 222861 is the game files (~9.5 GB); depot 222863 is the launcher (~240 MB).
GAME_DEPOT = 222861
LAUNCHER_DEPOT = 222863
TIMEOUT = 30


def fetch_json(url: str, token: str | None = None) -> dict:
    parsed = urllib.parse.urlsplit(url)
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "l4d2-server-container-vanilla-check"},
    )
    if token and parsed.hostname == "api.github.com":
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Accept", "application/vnd.github+json")
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def steam_app_info(app_id: int) -> tuple[str, str, str]:
    """Return (build_id, game_manifest, launcher_manifest) from SteamCMD API."""
    data = fetch_json(f"https://api.steamcmd.net/v1/info/{app_id}")
    app = data["data"][str(app_id)]
    build_id = str(app["depots"]["branches"]["public"]["buildid"])
    game_manifest = str(app["depots"][str(GAME_DEPOT)]["manifests"]["public"]["gid"])
    launcher_manifest = str(app["depots"][str(LAUNCHER_DEPOT)]["manifests"]["public"]["gid"])
    return build_id, game_manifest, launcher_manifest


def get_existing_vanilla_releases() -> list[dict]:
    """Fetch existing vanilla releases with tag and body."""
    # First try `gh` CLI
    try:
        res = subprocess.run(
            ["gh", "release", "list", "--limit", "100", "--json", "tagName,isDraft"],
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode == 0:
            releases = json.loads(res.stdout)
            vanilla = [r for r in releases if r.get("tagName", "").startswith("vanilla-")]
            results = []
            for item in vanilla:
                tag = item["tagName"]
                v_res = subprocess.run(
                    ["gh", "release", "view", tag, "--json", "tagName,body,isDraft"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if v_res.returncode == 0:
                    results.append(json.loads(v_res.stdout))
                else:
                    results.append({"tagName": tag, "body": ""})
            return results
    except FileNotFoundError:
        pass

    # Fallback to GitHub REST API if GITHUB_REPOSITORY is present in the environment
    repo = os.environ.get("GITHUB_REPOSITORY")
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if repo:
        try:
            releases = fetch_json(f"https://api.github.com/repos/{repo}/releases?per_page=100", token)
            return [
                {"tagName": r["tag_name"], "body": r.get("body", ""), "isDraft": r.get("draft", False)}
                for r in releases
                if r.get("tag_name", "").startswith("vanilla-")
            ]
        except Exception:
            pass

    return []


def is_build_released(build_id: str, releases: list[dict]) -> tuple[bool, str | None]:
    """Check if the given build_id is documented in any existing vanilla release."""
    target_markers = [
        f"Build ID: {build_id}",
        f"build_id: {build_id}",
        f"Build ID**: `{build_id}`",
        f"Build ID**: {build_id}",
    ]
    for r in releases:
        body = r.get("body") or ""
        tag = r.get("tagName") or ""
        if any(marker in body for marker in target_markers):
            return True, tag
        # Also check if the tag itself explicitly names this build ID
        if tag.endswith(f"-{build_id}"):
            return True, tag
    return False, None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force release_needed=true regardless of existing releases",
    )
    args = parser.parse_args()

    try:
        build_id, game_manifest, launcher_manifest = steam_app_info(APP_ID)
    except urllib.error.URLError as err:
        sys.exit(f"check-vanilla: failed to fetch Steam app info: {err}")

    releases = get_existing_vanilla_releases()
    already_released, existing_tag = is_build_released(build_id, releases)

    if args.force:
        release_needed = True
    else:
        release_needed = not already_released

    out = {
        "release_needed": str(release_needed).lower(),
        "build_id": build_id,
        "game_manifest": game_manifest,
        "launcher_manifest": launcher_manifest,
        "existing_tag": existing_tag or "",
    }

    # Print friendly diagnostic message to stderr
    if already_released:
        sys.stderr.write(
            f"check-vanilla: upstream build {build_id} already released as {existing_tag}\n"
        )
    else:
        sys.stderr.write(
            f"check-vanilla: upstream build {build_id} is not released yet (release needed)\n"
        )

    # Print machine-readable KEY=VALUE to stdout for GITHUB_OUTPUT
    for key, value in out.items():
        if "\n" in value:
            sys.exit(f"check-vanilla: {key} is multi-line, which GITHUB_OUTPUT cannot carry")
        print(f"{key}={value}")


if __name__ == "__main__":
    main()
