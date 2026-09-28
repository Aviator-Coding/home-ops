#!/usr/bin/env python3
"""Guard: the ai/litellm-pgvector vector store stays wired end to end.

Captain request 2026-09-27: "create a pgvector database for litellm and connect
to it", embedded (captain decision the same day) by OpenRouter's
qwen/qwen3-embedding-8b for both ingest and query. The pieces live in four places
that nothing else keeps in agreement, and every way they can drift apart fails
SILENTLY - the pods stay Ready and only searches go wrong:

1. The image. BerriAI publishes no litellm-pgvector image, so
   .github/docker/litellm-pgvector/ builds one under a CONTENT-ADDRESSED tag
   (upstream commit + hash of the build directory). The HelmRelease must name
   exactly the tag that directory builds to, or a Dockerfile change ships
   nothing (old tag still deployed) or the pod pulls a tag no build produced.
   .github/workflows/build-litellm-pgvector.yaml calls `--print-tag` here, so
   the builder and this gate share one algorithm.
2. The vector width. Upstream's Prisma schema hardcodes vector(1536);
   Qwen3-Embedding-8B natively emits 4096, which no pgvector index can take
   (HNSW/IVFFlat cap `vector` at 2000), so the store requests 2000 through the
   `dimensions` parameter. schema.sql and EMBEDDING__DIMENSIONS must agree, or
   every insert is rejected, and the width must stay <= 2000 while schema.sql
   builds an HNSW index on it, or the Job fails and the store never exists.
3. The embedding model. The server embeds each search query through LiteLLM
   with its own virtual key. EMBEDDING__MODEL must be `openai/<name>` (the
   LiteLLM SDK needs a provider prefix to talk to the proxy), <name> must be the
   only model that key may call, and a LiteLLMModel must serve it. A query
   embedded by a different model returns confident nonsense, not an error.
4. The registry. LiteLLM's vector_store_registry is a LIST, which the
   operator's typed `vectorStoreRegistry` field (type: object) cannot express,
   so it rides extraConfig. Its api_key must use a private env var, never
   PG_VECTOR_API_KEY - that name is PGVectorStoreConfig's fallback, and
   exporting it lets any virtual key reach any store id by passing
   custom_llm_provider in the body. Its id must be one schema.sql seeds.

Every check is a pure parse of the repo (no cluster, no network), and each
negative test feeds the checker a mutated copy to prove it can go red.
"""

from __future__ import annotations

import copy
import hashlib
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
DOCKER_DIR = ROOT / ".github" / "docker" / "litellm-pgvector"
BUILD_WF = ROOT / ".github" / "workflows" / "build-litellm-pgvector.yaml"
APP_DIR = ROOT / "kubernetes" / "apps" / "base" / "ai" / "litellm-pgvector" / "app"
HELMRELEASE = APP_DIR / "helmrelease.yaml"
SCHEMA_SQL = APP_DIR / "resources" / "schema.sql"
OVERLAY = ROOT / "kubernetes" / "apps" / "main" / "ai" / "litellm-pgvector.yaml"
OVERLAY_KUSTOMIZATION = ROOT / "kubernetes" / "apps" / "main" / "ai" / "kustomization.yaml"
LITELLM_DIR = ROOT / "kubernetes" / "apps" / "base" / "ai" / "litellm" / "app"
PROXY = LITELLM_DIR / "litellmproxy.yaml"
LITELLM_ES = LITELLM_DIR / "externalsecret.yaml"
VIRTUAL_KEY = LITELLM_DIR / "virtualkeys" / "litellm-pgvector.yaml"
MODELS_DIR = LITELLM_DIR / "models"

IMAGE_REPOSITORY = "ghcr.io/aviator-coding/litellm-pgvector"
FALLBACK_ENV_NAMES = ("PG_VECTOR_API_KEY", "PG_VECTOR_API_BASE")


class Failure(Exception):
    pass


def require(cond: bool, msg: str) -> None:
    if not cond:
        raise Failure(msg)


def load_docs(path: Path) -> list[dict[str, Any]]:
    return [d for d in yaml.safe_load_all(path.read_text()) if isinstance(d, dict)]


def one_doc(path: Path, kind: str) -> dict[str, Any]:
    docs = [d for d in load_docs(path) if d.get("kind") == kind]
    require(len(docs) == 1, f"{path.relative_to(ROOT)}: expected one {kind}, found {len(docs)}")
    return docs[0]


# --- image tag -----------------------------------------------------------


def build_context_files() -> list[Path]:
    """Files git would ship in the build context (tracked + untracked-not-ignored)."""
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "--", "."],
        cwd=DOCKER_DIR,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split("\n")
    files = sorted({DOCKER_DIR / line for line in out if line})
    return [f for f in files if f.is_file()]


def upstream_commit(dockerfile_text: str) -> str:
    match = re.search(r"^ARG LITELLM_PGVECTOR_COMMIT=([0-9a-f]{40})$", dockerfile_text, re.M)
    require(match is not None, "Dockerfile: no full-SHA `ARG LITELLM_PGVECTOR_COMMIT=` pin")
    return match.group(1)


def image_tag(files: list[Path] | None = None) -> str:
    """<upstream commit[:7]>-<sha256 over every context file's path and bytes>[:12]."""
    files = build_context_files() if files is None else files
    require(any(f.name == "Dockerfile" for f in files), "build context has no Dockerfile")
    digest = hashlib.sha256()
    for f in files:
        rel = f.relative_to(DOCKER_DIR).as_posix().encode()
        data = f.read_bytes()
        digest.update(b"%d:%s\0%d:" % (len(rel), rel, len(data)))
        digest.update(data)
    commit = upstream_commit((DOCKER_DIR / "Dockerfile").read_text())
    return f"{commit[:7]}-{digest.hexdigest()[:12]}"


def helmrelease_container(hr: dict[str, Any]) -> dict[str, Any]:
    containers = hr["spec"]["values"]["controllers"]["litellm-pgvector"]["containers"]
    return containers["app"]


def check_image(hr: dict[str, Any], expected_tag: str) -> str:
    image = helmrelease_container(hr)["image"]
    require(
        image.get("repository") == IMAGE_REPOSITORY,
        f"image repository {image.get('repository')!r} != {IMAGE_REPOSITORY!r}",
    )
    require(
        image.get("tag") == expected_tag,
        f"HelmRelease tag {image.get('tag')!r} but .github/docker/litellm-pgvector builds "
        f"{expected_tag!r} - set values.controllers.litellm-pgvector.containers.app.image.tag "
        f"to {expected_tag!r}",
    )
    return expected_tag


def test_helmrelease_names_the_tag_the_build_context_produces() -> dict[str, Any]:
    tag = check_image(one_doc(HELMRELEASE, "HelmRelease"), image_tag())
    return {"tag": tag}


def test_tag_changes_with_any_context_byte() -> dict[str, Any]:
    files = build_context_files()
    base = image_tag(files)
    # Hash a mutated copy of the constraints file and prove the tag moves.
    target = next(f for f in files if f.name == "constraints.txt")
    original = target.read_bytes()
    try:
        target.write_bytes(original + b"\n# mutation\n")
        mutated = image_tag(files)
    finally:
        target.write_bytes(original)
    require(mutated != base, "tag did not change when constraints.txt changed")
    require(image_tag(files) == base, "tag is not stable across two runs")
    return {"base": base, "mutated": mutated}


def build_workflow_steps() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    wf = yaml.safe_load(BUILD_WF.read_text())
    return wf, wf["jobs"]["build"]["steps"]


def find_step(
    steps: list[dict[str, Any]], *, id_: str | None = None, uses_prefix: str | None = None
) -> dict[str, Any] | None:
    for step in steps:
        if id_ is not None and step.get("id") == id_:
            return step
        if uses_prefix is not None and str(step.get("uses", "")).startswith(uses_prefix):
            return step
    return None


def run_command_lines(run_script: str) -> list[str]:
    """Non-blank lines of a `run:` script that are not entirely a `#` comment."""
    return [line.strip() for line in run_script.splitlines() if line.strip() and not line.strip().startswith("#")]


def check_build_workflow(wf: dict[str, Any], steps: list[dict[str, Any]]) -> dict[str, Any]:
    env = wf.get("env", {})
    require(
        env.get("IMAGE_NAME") == IMAGE_REPOSITORY.removeprefix("ghcr.io/"),
        f"workflow env.IMAGE_NAME {env.get('IMAGE_NAME')!r} != {IMAGE_REPOSITORY.removeprefix('ghcr.io/')!r}",
    )

    tag_step = find_step(steps, id_="tag")
    require(tag_step is not None, "no step with id: tag")
    lines = run_command_lines(tag_step.get("run", ""))
    require(
        any("python3 scripts/ci/litellm-pgvector-test.py --print-tag" in line for line in lines),
        "the tag step must run this script's --print-tag as a command, not just mention it in a comment",
    )
    require(
        any("GITHUB_OUTPUT" in line and re.search(r"\btag=", line) for line in lines),
        "the tag step must write its result to GITHUB_OUTPUT as 'tag'",
    )

    build_step = find_step(steps, uses_prefix="docker/build-push-action@")
    require(build_step is not None, "no docker/build-push-action step")
    with_ = build_step.get("with", {})
    require(
        with_.get("context") == ".github/docker/litellm-pgvector",
        f"build-push context {with_.get('context')!r} != .github/docker/litellm-pgvector",
    )
    expected_tags = "${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}:${{ steps.tag.outputs.tag }}"
    require(
        with_.get("tags") == expected_tags,
        f"build-push tags {with_.get('tags')!r} must be env.REGISTRY/env.IMAGE_NAME tagged with the tag step's output",
    )
    return {"image": f"{env.get('REGISTRY')}/{env.get('IMAGE_NAME')}"}


def test_build_workflow_uses_this_algorithm() -> dict[str, Any]:
    wf, steps = build_workflow_steps()
    return check_build_workflow(wf, steps)


def test_build_workflow_checker_refuses_wrong_wiring() -> dict[str, Any]:
    wf, steps = build_workflow_steps()

    wrong_tag = copy.deepcopy(steps)
    find_step(wrong_tag, uses_prefix="docker/build-push-action@")["with"]["tags"] = (
        "${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}:latest"
    )

    commented_out = copy.deepcopy(steps)
    find_step(commented_out, id_="tag")["run"] = (
        "set -euo pipefail\n"
        "# python3 scripts/ci/litellm-pgvector-test.py --print-tag\n"
        'echo "tag=deadbeef" >> "$GITHUB_OUTPUT"\n'
    )

    refusals = {}
    for label, mutated_steps in {
        "build step tags a literal instead of the computed tag": wrong_tag,
        "print-tag invocation only appears in a comment": commented_out,
    }.items():
        try:
            check_build_workflow(wf, mutated_steps)
        except Failure as exc:
            refusals[label] = str(exc)
        else:
            raise Failure(f"checker accepted {label}")
    return {"refused": sorted(refusals)}


# --- vector width + embedding model -------------------------------------


def sql_code(sql: str) -> str:
    """schema.sql without `--` comments (its header discusses upstream's vector(1536))."""
    return re.sub(r"--[^\n]*", "", sql)


def schema_width(sql: str) -> int:
    widths = {int(w) for w in re.findall(r"\bvector\((\d+)\)", sql_code(sql))}
    require(len(widths) == 1, f"schema.sql: expected one vector(N) width, found {sorted(widths)}")
    return widths.pop()


# pgvector's documented HNSW/IVFFlat ceiling for the `vector` type.
INDEXABLE_VECTOR_DIMS = 2000


def check_embedding_contract(hr: dict[str, Any], sql: str, key: dict[str, Any], model_names: set[str]) -> dict[str, Any]:
    env = helmrelease_container(hr).get("env", {})
    dims = int(env.get("EMBEDDING__DIMENSIONS", "0"))
    width = schema_width(sql)
    require(dims == width, f"EMBEDDING__DIMENSIONS={dims} but schema.sql declares vector({width})")
    if re.search(r"USING\s+(hnsw|ivfflat)\b", sql_code(sql), re.I):
        require(
            width <= INDEXABLE_VECTOR_DIMS,
            f"vector({width}) exceeds pgvector's {INDEXABLE_VECTOR_DIMS}-dim index limit; the HNSW index would fail",
        )
    model = env.get("EMBEDDING__MODEL", "")
    require(model.startswith("openai/"), f"EMBEDDING__MODEL {model!r} lacks the openai/ provider prefix")
    name = model.removeprefix("openai/")
    allowed = key["spec"].get("models", [])
    require(allowed == [name], f"litellm-pgvector key may call {allowed}, must be exactly [{name!r}]")
    require(name in model_names, f"no LiteLLMModel serves {name!r}")
    return {"dims": dims, "model": name}


def model_names() -> set[str]:
    names = set()
    for path in MODELS_DIR.glob("*.yaml"):
        for doc in load_docs(path):
            if doc.get("kind") == "LiteLLMModel":
                names.add(doc["spec"]["modelName"])
    return names


def test_embedding_width_and_model_agree() -> dict[str, Any]:
    return check_embedding_contract(
        one_doc(HELMRELEASE, "HelmRelease"),
        SCHEMA_SQL.read_text(),
        one_doc(VIRTUAL_KEY, "LiteLLMVirtualKey"),
        model_names(),
    )


def test_embedding_checker_refuses_upstream_width() -> dict[str, Any]:
    hr = one_doc(HELMRELEASE, "HelmRelease")
    key = one_doc(VIRTUAL_KEY, "LiteLLMVirtualKey")
    names = model_names()
    refusals = {}
    upstream_sql = re.sub(r"vector\(\d+\)", "vector(1536)", sql_code(SCHEMA_SQL.read_text()))
    native_hr = copy.deepcopy(hr)
    helmrelease_container(native_hr)["env"]["EMBEDDING__DIMENSIONS"] = "4096"
    native_sql = re.sub(r"vector\(\d+\)", "vector(4096)", sql_code(SCHEMA_SQL.read_text()))
    wide_key = copy.deepcopy(key)
    wide_key["spec"]["models"] = [*key["spec"]["models"], "chat-local"]
    bare = copy.deepcopy(hr)
    env = helmrelease_container(bare)["env"]
    env["EMBEDDING__MODEL"] = env["EMBEDDING__MODEL"].removeprefix("openai/")
    for label, args in {
        "upstream vector(1536)": (hr, upstream_sql, key, names),
        "unindexable native 4096": (native_hr, native_sql, key, names),
        "widened allow-list": (hr, SCHEMA_SQL.read_text(), wide_key, names),
        "no provider prefix": (bare, SCHEMA_SQL.read_text(), key, names),
    }.items():
        try:
            check_embedding_contract(*args)
        except Failure as exc:
            refusals[label] = str(exc)
        else:
            raise Failure(f"checker accepted {label}")
    return {"refused": sorted(refusals)}


# --- registry -----------------------------------------------------------


def registry_entries(proxy: dict[str, Any]) -> list[dict[str, Any]]:
    spec = proxy["spec"]
    require("vectorStoreRegistry" not in spec, "use extraConfig.vector_store_registry; the typed field is object-only")
    entries = spec.get("extraConfig", {}).get("vector_store_registry")
    require(isinstance(entries, list) and entries, "extraConfig.vector_store_registry must be a non-empty list")
    return entries


def check_registry(proxy: dict[str, Any], es: dict[str, Any], sql: str, hr: dict[str, Any]) -> dict[str, Any]:
    template = es["spec"]["target"]["template"]["data"]
    proxy_env = {e["name"] for e in proxy["spec"].get("env", [])}
    for name in FALLBACK_ENV_NAMES:
        require(name not in template and name not in proxy_env, f"{name} must never reach the proxy")
    seeded = set(re.findall(r"VALUES\s*\(\s*'([^']+)'", sql_code(sql)))
    port = hr["spec"]["values"]["service"]["app"]["ports"]["http"]["port"]
    expected_base = f"http://litellm-pgvector.ai.svc.cluster.local:{port}"
    ids = []
    for entry in registry_entries(proxy):
        params = entry["litellm_params"]
        if params.get("custom_llm_provider") != "pg_vector":
            continue
        api_key = params.get("api_key", "")
        require(api_key.startswith("os.environ/"), f"store {params.get('vector_store_id')}: api_key must be os.environ/")
        var = api_key.removeprefix("os.environ/")
        require(var not in FALLBACK_ENV_NAMES, f"api_key uses the provider fallback name {var}")
        require(var in template, f"{var} is not provided by the litellm ExternalSecret")
        require(params.get("api_base") == expected_base, f"api_base {params.get('api_base')!r} != {expected_base!r}")
        require(params["vector_store_id"] in seeded, f"store {params['vector_store_id']!r} is not seeded by schema.sql")
        ids.append(params["vector_store_id"])
    require(ids, "no pg_vector store registered")
    return {"stores": ids}


def test_registry_is_private_seeded_and_reachable() -> dict[str, Any]:
    return check_registry(
        one_doc(PROXY, "LiteLLMProxy"),
        one_doc(LITELLM_ES, "ExternalSecret"),
        SCHEMA_SQL.read_text(),
        one_doc(HELMRELEASE, "HelmRelease"),
    )


def test_registry_checker_refuses_fallback_name() -> dict[str, Any]:
    proxy = one_doc(PROXY, "LiteLLMProxy")
    es = one_doc(LITELLM_ES, "ExternalSecret")
    hr = one_doc(HELMRELEASE, "HelmRelease")
    sql = SCHEMA_SQL.read_text()
    refusals = []
    fallback = copy.deepcopy(proxy)
    for entry in fallback["spec"]["extraConfig"]["vector_store_registry"]:
        entry["litellm_params"]["api_key"] = "os.environ/PG_VECTOR_API_KEY"
    exported = copy.deepcopy(es)
    exported["spec"]["target"]["template"]["data"]["PG_VECTOR_API_KEY"] = "{{ .x }}"
    typed = copy.deepcopy(proxy)
    typed["spec"]["vectorStoreRegistry"] = {"default": {}}
    for label, args in {
        "fallback api_key name": (fallback, es, sql, hr),
        "PG_VECTOR_API_KEY exported": (proxy, exported, sql, hr),
        "unseeded store id": (proxy, es, sql.replace("'default'", "'renamed'"), hr),
        "typed vectorStoreRegistry": (typed, es, sql, hr),
    }.items():
        try:
            check_registry(*args)
        except Failure:
            refusals.append(label)
        else:
            raise Failure(f"checker accepted {label}")
    return {"refused": refusals}


def test_overlay_is_wired() -> dict[str, Any]:
    ks = one_doc(OVERLAY, "Kustomization")
    deps = {d["name"] for d in ks["spec"].get("dependsOn", [])}
    for dep in ("litellm", "cloudnative-pg", "onepassword-store"):
        require(dep in deps, f"litellm-pgvector overlay must dependsOn {dep}")
    require(ks["spec"]["path"] == "./kubernetes/apps/base/ai/litellm-pgvector/app", "overlay path")
    kinds = {h["kind"] for h in ks["spec"].get("healthChecks", [])}
    require("Deployment" in kinds, "overlay must health-check the Deployment, not the HelmRelease")
    require(not ks["spec"].get("wait", False), "wait: true would make Flux ignore healthChecks")
    resources = yaml.safe_load(OVERLAY_KUSTOMIZATION.read_text())["resources"]
    require("./litellm-pgvector.yaml" in resources, "overlay not listed in apps/main/ai/kustomization.yaml")
    return {"dependsOn": sorted(deps)}


def main() -> int:
    if sys.argv[1:] == ["--print-tag"]:
        print(image_tag())
        return 0
    tests = [
        test_helmrelease_names_the_tag_the_build_context_produces,
        test_tag_changes_with_any_context_byte,
        test_build_workflow_uses_this_algorithm,
        test_build_workflow_checker_refuses_wrong_wiring,
        test_embedding_width_and_model_agree,
        test_embedding_checker_refuses_upstream_width,
        test_registry_is_private_seeded_and_reachable,
        test_registry_checker_refuses_fallback_name,
        test_overlay_is_wired,
    ]
    failed = 0
    for test in tests:
        name = test.__name__
        try:
            evidence = test()
        except Failure as exc:
            failed += 1
            print(f"[FAIL] {name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"[FAIL] {name}: unexpected {type(exc).__name__}: {exc}")
        else:
            print(f"[PASS] {name} {evidence}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
