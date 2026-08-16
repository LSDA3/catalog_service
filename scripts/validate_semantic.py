"""The coverage gate.

It exits with an error code if the derived artifact does not cover exactly the
canonical catalog or if some relation is not sound. **An error code means nothing
is deployed**: it is a gate, not a fallback (A3.4).

It validates **shape and integrity; it does not reinterpret the catalog**. It
does not check whether `equivalent` was chosen well — that is a reading of the
text and happens during enrichment — but that what is written is consistent with
itself.

The universe of referential integrity is **the canonical identifiers**, not the
152 raw rows: an `alt_product_id` is an identity alias, not a node.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import yaml  # noqa: E402

import normalization  # noqa: E402

VOCABULARY_VERSION = 4
# `product_type` is NOT here: it is the only **controlled but open** vocabulary,
# because a new product legitimately introduces a new type with its aliases. Of it
# we check that it exists in the file — not that it belongs to a frozen list — and
# that no alias resolves to two different types.
CLOSED_VOCABULARIES = (
    "use_case",
    "functional_family",
    "gift_risk",
    "suitable_relationships",
)
RELATION_TYPE = {"equivalent", "same_function"}


def _entries_of(layer: dict | list) -> dict[str, dict]:
    products = layer["products"] if isinstance(layer, dict) and "products" in layer else layer
    if isinstance(products, list):
        return {entry["product_id"]: entry for entry in products}
    return products


def _at_previous_revision(path_in_repo: str) -> str | None:
    """The content of a file one commit ago, or `None` when there is no history."""
    import subprocess

    try:
        output = subprocess.run(
            ["git", "show", f"HEAD~1:{path_in_repo}"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return output.stdout


def _growth_is_justified(
    vocabulary: dict, identifiers: set[str], used_types: set[str | None]
) -> list[str]:
    """No commit may add more product types than it adds canonical products.

    By construction each new product introduces at most one new type, so more new
    types than new products means the classifier is inventing rather than the
    catalog growing. A newly registered type must also actually be used by the
    resulting semantic layer: otherwise it was only proposed, not justified.
    """
    import json

    import yaml

    previous_vocabulary = _at_previous_revision("data/vocabularies.yaml")
    previous_layer = _at_previous_revision("data/semantic_layer.json")
    if previous_vocabulary is None or previous_layer is None:
        return []

    before_types = set(yaml.safe_load(previous_vocabulary).get("product_type", {}))
    new_types = set(vocabulary.get("product_type", {})) - before_types

    before_layer = json.loads(previous_layer)
    before_products = set(
        before_layer["products"] if isinstance(before_layer, dict) else before_layer
    )
    new_products = identifiers - before_products

    failures: list[str] = []
    if len(new_types) > len(new_products):
        failures.append(
            f"{len(new_types)} new product_type for {len(new_products)} new products: "
            f"{sorted(new_types)}. Each new product introduces at most one type, so "
            "this is invention, not growth"
        )

    unused = new_types - used_types
    if unused:
        failures.append(
            f"new product_type registered but not used by any product: {sorted(unused)}"
        )
    return failures


def validate(csv_path: Path, semantic_layer_path: Path, vocabularies_path: Path) -> list[str]:
    """Return the list of failures. An empty list means the gate opens."""
    failures: list[str] = []

    layer = json.loads(semantic_layer_path.read_text(encoding="utf-8"))
    entries = _entries_of(layer)
    vocabulary = yaml.safe_load(vocabularies_path.read_text(encoding="utf-8"))

    declared_version = (
        layer.get("vocabulary_version") if isinstance(layer, dict) else None
    )
    if declared_version != VOCABULARY_VERSION:
        failures.append(
            f"the layer declares vocabulary_version {declared_version!r} "
            f"and {VOCABULARY_VERSION} was expected"
        )
    if vocabulary.get("version") != VOCABULARY_VERSION:
        failures.append(
            f"the vocabulary declares version {vocabulary.get('version')!r} "
            f"and {VOCABULARY_VERSION} was expected"
        )

    product_types_by_id = {
        product_id: entry.get("product_type") for product_id, entry in entries.items()
    }
    canonical, _ = normalization.canonicalize(
        csv_path, vocabularies_path, product_types_by_id
    )
    identifiers = {product.product_id for product in canonical}

    # Exact set equality: neither missing nor orphan entries.
    missing = sorted(identifiers - set(entries))
    orphans = sorted(set(entries) - identifiers)
    if missing:
        failures.append(f"without a semantic entry: {missing}")
    if orphans:
        failures.append(f"orphan entries, with no canonical product: {orphans}")

    for product_id, entry in sorted(entries.items()):
        for field in (
            "product_type",
            "functional_family",
            "use_case",
            "gift_risk",
            "suitable_relationships",
            "is_standalone_gift",
            "stocking_filler",
        ):
            if field not in entry:
                failures.append(f"{product_id}: missing {field}")

        for field in CLOSED_VOCABULARIES:
            if field not in entry:
                continue
            value_ = entry[field]
            values_ = value_ if isinstance(value_, list) else [value_]
            outside = [v for v in values_ if v not in vocabulary.get(field, {})]
            if outside:
                failures.append(f"{product_id}: {field} outside the vocabulary: {outside}")

        kind_ = entry.get("product_type")
        if not kind_:
            failures.append(f"{product_id}: empty product_type")
        elif kind_ not in vocabulary.get("product_type", {}):
            failures.append(
                f"{product_id}: product_type {kind_!r} is not declared in the vocabulary. "
                "It may grow, but it is declared: it is not used without adding it"
            )

        if not entry.get("use_case"):
            failures.append(f"{product_id}: empty use_case")
        if not entry.get("functional_family"):
            failures.append(f"{product_id}: empty functional_family")
        if not entry.get("gift_risk"):
            failures.append(f"{product_id}: empty gift_risk")
        if not entry.get("suitable_relationships"):
            failures.append(f"{product_id}: empty suitable_relationships")
        for field in ("is_standalone_gift", "stocking_filler"):
            if field in entry and not isinstance(entry[field], bool):
                failures.append(f"{product_id}: {field} is not boolean")

    # An alias cannot resolve to two different types: it would be an ambiguity
    # the service cannot undo when reading a query.
    owners: dict[str, str] = {}
    for kind_, definition in vocabulary.get("product_type", {}).items():
        for aliases_ in (definition.get("aliases", []) if isinstance(definition, dict) else []):
            key_ = aliases_.lower()
            if key_ in owners and owners[key_] != kind_:
                failures.append(
                    f"the alias {aliases_!r} resolves to {owners[key_]!r} and to {kind_!r}"
                )
            owners[key_] = kind_
        if not (definition.get("definicion") if isinstance(definition, dict) else None):
            failures.append(f"product_type {kind_!r} without a definition")
        if (
            isinstance(definition, dict)
            and "gender_specific" in definition
            and definition.get("gender_specific") not in {"male", "female"}
        ):
            failures.append(
                f"product_type {kind_!r} has invalid gender_specific "
                f"{definition.get('gender_specific')!r}"
            )

    pairs_seen: set[tuple[str, str, str]] = set()
    for product_id, entry in sorted(entries.items()):
        for link in entry.get("pairs_with") or []:
            if isinstance(link, dict):
                if "product_id" not in link:
                    failures.append(f"{product_id}: pairs_with without product_id")
                    continue
                other = link["product_id"]
            else:
                other = link
            if other not in identifiers:
                failures.append(f"{product_id}: pairs_with points at {other}, which is not canonical")
            if other == product_id:
                failures.append(f"{product_id}: pairs_with points at itself")
            pair = tuple(sorted((product_id, other)))
            persisted = ("pairs_with", pair[0], pair[1])
            if persisted in pairs_seen:
                failures.append(f"{pair[0]} · {pair[1]}: pairs_with is persisted twice")
            pairs_seen.add(persisted)

        for link in entry.get("alternative_to") or []:
            if not isinstance(link, dict) or "product_id" not in link:
                failures.append(f"{product_id}: alternative_to without product_id")
                continue
            other = link["product_id"]
            if other not in identifiers:
                failures.append(
                    f"{product_id}: alternative_to points at {other}, which is not canonical"
                )
            if other == product_id:
                failures.append(f"{product_id}: alternative_to points at itself")
            if link.get("relation_type") not in RELATION_TYPE:
                failures.append(
                    f"{product_id} → {other}: relation_type "
                    f"{link.get('relation_type')!r} is not valid"
                )
            if (
                other in product_types_by_id
                and link.get("relation_type") == "same_function"
                and product_types_by_id.get(product_id) == product_types_by_id.get(other)
            ):
                failures.append(
                    f"{product_id} · {other}: same_function is already derived from shared "
                    f"product_type {product_types_by_id.get(product_id)!r} and must not be persisted"
                )
            pair = tuple(sorted((product_id, other)))
            persisted = ("alternative_to", pair[0], pair[1])
            if persisted in pairs_seen:
                failures.append(f"{pair[0]} · {pair[1]}: the pair is persisted twice")
            pairs_seen.add(persisted)
            if product_id != pair[0]:
                failures.append(
                    f"{pair[0]} · {pair[1]}: persisted under the larger identifier"
                )

    used_types = {entry.get("product_type") for entry in entries.values()}
    failures.extend(_growth_is_justified(vocabulary, identifiers, used_types))

    disappeared = used_types - set(vocabulary.get("product_type", {})) - {None}
    if disappeared:
        failures.append(
            f"product_type in use that no longer exists in the vocabulary: {sorted(disappeared)}"
        )

    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Coverage gate of the semantic layer")
    parser.add_argument("--csv", required=True)
    parser.add_argument("--semantic", required=True)
    parser.add_argument("--vocabularies", default="data/vocabularies.yaml")
    options = parser.parse_args()

    failures = validate(
        Path(options.csv), Path(options.semantic), Path(options.vocabularies)
    )
    if failures:
        print("The gate does NOT open. Nothing is deployed.\n")
        for failure in failures:
            print(f"  · {failure}")
        return 1

    print("Coverage complete and integrity sound. The gate opens.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
