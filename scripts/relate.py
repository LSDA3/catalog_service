"""Recomputes the relations over the whole catalog. It only runs in CI.

**It is never incremental**, and not out of convenience: a relation needs to know
the whole catalog, and a new product may force revisiting the relations of
products that were already there. If a new chef's knife arrives, the sharpening
stone that points at the old one may have to point at both.

It writes each relation **once**, under the lexicographically smaller
`product_id` of the pair. Making the other end aware of it is the loader's job.

This script **does not travel to the container**.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import normalization  # noqa: E402

RELATION_TYPE = {"equivalent", "same_function"}


def _card(product: normalization.CanonicalProduct, entry: dict) -> dict:
    """What the model needs to see of each product in order to relate it."""
    return {
        "product_id": product.product_id,
        "name": product.name,
        "product_type": entry.get("product_type"),
        "functional_family": entry.get("functional_family"),
        "price_eur": product.price,
        "description": product.description,
    }


def request_relations(catalog: list[dict], prompt: str) -> dict:
    from anthropic import Anthropic

    client_ = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    response = client_.messages.create(
        model=os.environ.get("RELATE_MODEL", "claude-sonnet-4-5"),
        max_tokens=8192,
        system=prompt,
        messages=[{"role": "user", "content": json.dumps(catalog, ensure_ascii=False)}],
    )
    text_ = "".join(block.text for block in response.content if block.type == "text")
    start_, end_ = text_.find("{"), text_.rfind("}")
    return json.loads(text_[start_ : end_ + 1])


def normalize_relations(proposals: dict, canonical: set[str]) -> dict[str, dict]:
    """Leave a single write per pair, under the smaller identifier.

    What arrives may carry the same pair in both directions or repeated: here it
    is reduced to the shape the coverage gate demands, without scoring anything
    and without inventing a relation that was not proposed.
    """
    pairs: dict[tuple[str, str], str] = {}
    complements: set[tuple[str, str]] = set()

    for product_id, proposal in proposals.items():
        if product_id not in canonical:
            continue
        for link in proposal.get("pairs_with") or []:
            other = link["product_id"] if isinstance(link, dict) else link
            if other in canonical and other != product_id:
                complements.add(tuple(sorted((product_id, other))))
        for link in proposal.get("alternative_to") or []:
            if isinstance(link, dict):
                other = link.get("product_id")
                kind = link.get("relation_type", "same_function")
            else:
                other, kind = link, "same_function"
            if other not in canonical or other == product_id:
                continue
            if kind not in RELATION_TYPE:
                kind = "same_function"  # when in doubt, the safe label
            pair = tuple(sorted((product_id, other)))
            # If the two ends disagree, the more conservative one wins.
            previous = pairs.get(pair)
            pairs[pair] = (
                "equivalent" if previous == "equivalent" and kind == "equivalent" else
                kind if previous is None else
                ("equivalent" if previous == kind == "equivalent" else "same_function")
            )

    relations: dict[str, dict] = {
        product_id: {"pairs_with": [], "alternative_to": []} for product_id in canonical
    }
    for smaller, larger in sorted(complements):
        relations[smaller]["pairs_with"].append(larger)
    for (smaller, larger), kind in sorted(pairs.items()):
        relations[smaller]["alternative_to"].append(
            {"product_id": larger, "relation_type": kind}
        )
    return relations


def main() -> int:
    parser = argparse.ArgumentParser(description="Relations of the whole catalog")
    parser.add_argument("--csv", required=True)
    parser.add_argument("--semantic", required=True)
    parser.add_argument("--vocabularies", default="data/vocabularies.yaml")
    parser.add_argument("--prompt", default="prompts/relate.md")
    options = parser.parse_args()

    path_ = Path(options.semantic)
    layer = json.loads(path_.read_text(encoding="utf-8"))
    entries: dict = layer["products"]

    product_types_by_id = {
        product_id: entry.get("product_type") for product_id, entry in entries.items()
    }
    canonical, _ = normalization.canonicalize(
        options.csv, options.vocabularies, product_types_by_id
    )

    catalog = [_card(p, entries.get(p.product_id, {})) for p in canonical]
    prompt = Path(options.prompt).read_text(encoding="utf-8")
    proposals = request_relations(catalog, prompt)

    relations = normalize_relations(proposals, {p.product_id for p in canonical})
    for product_id, entry in entries.items():
        entry["pairs_with"] = relations[product_id]["pairs_with"]
        entry["alternative_to"] = relations[product_id]["alternative_to"]

    layer["products"] = dict(sorted(entries.items()))
    path_.write_text(json.dumps(layer, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    written = sum(
        len(e["pairs_with"]) + len(e["alternative_to"]) for e in entries.values()
    )
    print(f"{written} relations written over {len(entries)} products")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
