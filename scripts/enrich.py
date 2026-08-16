"""Computes the own fields of every product. It only runs in CI.

**Incremental by default**: it classifies only the canonical products that have
no entry. **Full when the criterion changes**: if the commit touched
`data/vocabularies.yaml` or `prompts/enrich.md`, it reclassifies all 150 even
when there is not a single new product (A3.6).

Why that condition is not optional: the products already classified were
classified with the previous criterion. Leaving them untouched mixes two
classifications inside the same file, and **the failure is invisible** — the
artifact is still valid, passes the coverage gate and classifies badly.

This script **does not travel to the container**. The model key is injected only
here.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import normalization  # noqa: E402

VOCABULARIES = "data/vocabularies.yaml"
ENRICH_PROMPT = "prompts/enrich.md"
CLOSED_VOCABULARIES = ("use_case", "functional_family", "gift_risk", "suitable_relationships")
OWN_FIELDS = (
    "product_type",
    "functional_family",
    "use_case",
    "gift_risk",
    "suitable_relationships",
    "is_standalone_gift",
    "stocking_filler",
)


def files_in_commit() -> list[str]:
    """What changed in this commit, according to git."""
    try:
        output = subprocess.run(
            ["git", "diff", "--name-only", "HEAD~1", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        # With no history to compare against, everything is reclassified: it is
        # the safe side. Classifying too much costs cents; classifying too little
        # with a new criterion produces a valid and wrong file.
        return list(CRITERION_FILES)
    return [linea for linea in output.stdout.splitlines() if linea]


def _vocabulary_at(revision: str, path_in_repo: str) -> dict | None:
    """The vocabulary as it was in a previous revision, or `None` if unavailable."""
    import yaml

    try:
        output = subprocess.run(
            ["git", "show", f"{revision}:{path_in_repo}"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return yaml.safe_load(output.stdout)


def criterion_changed(changed: list[str], vocabularies_path: str | Path) -> bool:
    """Whether this commit changed **the criterion** the classifier applies.

    Touching `prompts/enrich.md` always changes it: it is the other half of the
    criterion.

    Touching `data/vocabularies.yaml` **does not always**. There are two kinds of
    change to that file and they mean opposite things:

    - **Registering a new `product_type`** because a new product introduced an
      object the vocabulary did not have. The inventory grows; the meaning of the
      domain does not. None of the 150 already classified products becomes that
      new type just because it now exists, so **nothing is reclassified** and
      `vocabulary_version` does not rise.
    - **Any change of criterion**: a value added, removed or redefined in the
      closed vocabularies, or an existing `product_type` whose definition or
      aliases change or that disappears. The already classified products carry
      values under the previous meaning, so **all 150 are reclassified**.

    Without this distinction the automatic registration would trigger a full
    reclassification on the next run, and `enrich.py` would keep firing itself.
    """
    import yaml

    if ENRICH_PROMPT in changed:
        return True
    if VOCABULARIES not in changed:
        return False

    previous = _vocabulary_at("HEAD~1", VOCABULARIES)
    if previous is None:
        # Nothing to compare against: the safe side is to reclassify.
        return True

    current = yaml.safe_load(Path(vocabularies_path).read_text(encoding="utf-8"))

    if previous.get("version") != current.get("version"):
        return True
    for field in CLOSED_VOCABULARIES:
        if previous.get(field) != current.get(field):
            return True

    previous_types = previous.get("product_type", {})
    current_types = current.get("product_type", {})
    for key, definition in previous_types.items():
        if key not in current_types or current_types[key] != definition:
            return True

    # Only new `product_type` entries were added: the criterion is the same.
    return False


def register_new_types(vocabularies_path: str | Path, proposals: dict[str, dict]) -> list[str]:
    """Write into the vocabulary the types a new product introduced.

    They are written here, in the same run, so the artifact and the vocabulary
    never disagree. **`vocabulary_version` does not rise**: the inventory grows,
    the meaning of the domain does not.
    """
    import yaml

    if not proposals:
        return []

    path_ = Path(vocabularies_path)
    vocabulary = yaml.safe_load(path_.read_text(encoding="utf-8"))
    types_ = vocabulary.setdefault("product_type", {})

    registered = []
    for key, definition in sorted(proposals.items()):
        if key in types_:
            continue
        types_[key] = {
            "definicion": definition.get("definicion", ""),
            "aliases": sorted(definition.get("aliases", [])),
        }
        registered.append(key)

    if registered:
        vocabulary["product_type"] = dict(sorted(types_.items()))
        path_.write_text(
            yaml.safe_dump(vocabulary, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )
    return registered


def vocabulary_for_the_prompt(vocabularies_path: str | Path) -> str:
    """The allowed values and their definitions, from the single source.

    The prompt describes **the criterion**; the vocabulary supplies **the
    values**. If they are not given to the classifier it invents labels that the
    coverage gate will reject afterwards, and the pipeline fails for an avoidable
    reason.

    `product_type` is given as a reference, not as a closed list: it is the only
    vocabulary that grows, and the prompt tells the classifier when to propose a
    new one.
    """
    import yaml

    vocabulary = yaml.safe_load(Path(vocabularies_path).read_text(encoding="utf-8"))
    blocks = [f"# Vocabulary, version {vocabulary.get('version')}"]
    for field in ("use_case", "functional_family", "gift_risk", "suitable_relationships"):
        blocks.append(f"\n## `{field}` — closed vocabulary, choose only from this list\n")
        for key_, definition in vocabulary[field].items():
            text_ = definition.get("definicion", "") if isinstance(definition, dict) else ""
            blocks.append(f"- `{key_}`: {text_}")

    blocks.append(
        "\n## `product_type` — controlled vocabulary that **may grow**\n"
        "Use one of the existing ones if it describes the object. If none fits, "
        "propose a new one in lowercase with underscores, with its definition and "
        "its aliases.\n"
    )
    for key_, definition in vocabulary["product_type"].items():
        text_ = definition.get("definicion", "") if isinstance(definition, dict) else ""
        aliases_ = definition.get("aliases", []) if isinstance(definition, dict) else []
        suffix = f" (aliases_: {', '.join(aliases_)})" if aliases_ else ""
        blocks.append(f"- `{key_}`: {text_}{suffix}")

    return "\n".join(blocks)


def classify(product: normalization.CanonicalProduct, prompt: str) -> dict:
    """Ask the model for the own fields of one product.

    The call happens here and only here, with the key CI injects. The container
    carries neither the key, nor the prompt, nor this file.
    """
    from anthropic import Anthropic

    client_ = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    ficha = {
        "product_id": product.product_id,
        "name": product.name,
        "category": product.category,
        "subcategory": product.subcategory,
        "brand": product.brand,
        "price_eur": product.price,
        "recipient": product.recipient,
        "occasion": product.occasion,
        "tags": product.tags,
        "color": product.color,
        "material": product.material,
        "description": product.description,
    }
    response = client_.messages.create(
        model=os.environ.get("ENRICH_MODEL", "claude-sonnet-4-5"),
        max_tokens=1024,
        system=prompt,
        messages=[{"role": "user", "content": json.dumps(ficha, ensure_ascii=False)}],
    )
    text_ = "".join(bloque.text for bloque in response.content if bloque.type == "text")
    start_, end_ = text_.find("{"), text_.rfind("}")
    entry = json.loads(text_[start_ : end_ + 1])
    own = {field: entry[field] for field in OWN_FIELDS if field in entry}
    # A type the vocabulary does not have yet travels with its definition and its
    # aliases, so it can be registered without inventing anything.
    proposal = entry.get("new_product_type")
    return own, (proposal if isinstance(proposal, dict) and proposal.get("key") else None)


def main() -> int:
    parser = argparse.ArgumentParser(description="Own fields of the products")
    parser.add_argument("--csv", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--vocabularies", default="data/vocabularies.yaml")
    parser.add_argument("--prompt", default="prompts/enrich.md")
    options = parser.parse_args()

    output = Path(options.out)
    layer = json.loads(output.read_text(encoding="utf-8")) if output.exists() else {}
    entries: dict = layer.get("products", {}) if isinstance(layer, dict) else {}

    product_types_by_id = {
        product_id: entry.get("product_type") for product_id, entry in entries.items()
    }
    canonicos, _ = normalization.canonicalize(
        options.csv, options.vocabularies, product_types_by_id
    )

    changed = files_in_commit()
    full_run = criterion_changed(changed, options.vocabularies)
    if full_run:
        print("The commit touches the criterion: every product is reclassified.")
        pending = canonicos
    else:
        pending = [p for p in canonicos if p.product_id not in entries]
        print(f"The criterion has not changed: {len(pending)} new products are classified.")

    if pending:
        prompt = (
            Path(options.prompt).read_text(encoding="utf-8")
            + "\n\n---\n\n"
            + vocabulary_for_the_prompt(options.vocabularies)
        )
        proposals: dict[str, dict] = {}
        for product in pending:
            own, proposal = classify(product, prompt)
            entries[product.product_id] = own
            if proposal:
                proposals[proposal["key"]] = proposal
            print(f"  · {product.product_id} {product.name}")

        registered = register_new_types(options.vocabularies, proposals)
        for key in registered:
            print(f"  + product_type registered: {key}")

    # Orphan entries leave together with the product that justified them.
    current = {p.product_id for p in canonicos}
    entries = {k: v for k, v in entries.items() if k in current}

    result_ = {
        "vocabulary_version": 4,
        "products": dict(sorted(entries.items())),
    }
    # Relations already written are not touched here: relate.py recomputes them.
    output.write_text(
        json.dumps(result_, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"{len(entries)} entries written en {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
