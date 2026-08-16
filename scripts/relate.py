"""Recomputes the relations over the whole catalog. It only runs in CI.

**It is never incremental**, and not out of convenience: a relation needs to know
the whole catalog, and a new product may force revisiting the relations of
products that were already there. If a new chef's knife arrives, the sharpening
stone that points at the old one may have to point at both.

It writes each relation **once**, and **the two fields do not store it the same
way**, because they do not mean the same thing:

- `pairs_with` is stored **from the accessory towards the main product** (A4.7).
  The direction carries meaning — the sharpening stone points at the knife, not
  the other way round — so it is preserved exactly as it arrives and **is never
  reordered by `product_id`**.
- `alternative_to` is symmetric, so it is stored under the **lexicographically
  smaller `product_id`** of the pair (A4.8), together with its `relation_type`.

Making the other end aware of either is the loader's job.

**Nothing invalid is quietly turned into something valid here.** A reference to a
product that does not exist, a product related to itself, a `relation_type`
outside the vocabulary, an `alternative_to` already derived by shared
`product_type`, or the same pair arriving twice with different natures is rejected.
As in `enrich.py`, the model gets a limited chance to correct a rejected answer;
if it keeps breaking a deterministic rule, the build stops without writing.

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
ATTEMPTS = 3


class InvalidRelations(RuntimeError):
    """The model proposed relations the design does not admit."""


def _card(product: normalization.CanonicalProduct, entry: dict) -> dict:
    """What the model needs to see of each product in order to relate it."""
    return {
        "product_id": product.product_id,
        "name": product.name,
        "subcategory": product.subcategory,
        "brand": product.brand,
        "tags": product.tags,
        "product_type": entry.get("product_type"),
        "functional_family": entry.get("functional_family"),
        "price_eur": product.price,
        "description": product.description,
    }


def request_relations(
    catalog: list[dict],
    prompt: str,
    canonical: set[str],
    product_types_by_id: dict[str, str | None],
) -> dict:
    """Ask for the whole relation mesh and accept only an admissible answer.

    The semantic decision stays with the model, but deterministic violations do
    not become data. A rejected answer is returned to the same conversation with
    the exact validation error, following the same bounded correction pattern as
    `enrich.py`. After `ATTEMPTS` tries, the run stops and nothing is written.
    """
    from anthropic import Anthropic

    client_ = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    messages = [{"role": "user", "content": json.dumps(catalog, ensure_ascii=False)}]

    for attempt in range(1, ATTEMPTS + 1):
        response = client_.messages.create(
            model=os.environ.get("RELATE_MODEL", "claude-sonnet-4-5"),
            max_tokens=8192,
            temperature=0,
            system=prompt,
            messages=messages,
        )
        text_ = "".join(block.text for block in response.content if block.type == "text")
        start_, end_ = text_.find("{"), text_.rfind("}")
        proposals = json.loads(text_[start_ : end_ + 1])

        try:
            normalize_relations(proposals, canonical, product_types_by_id)
            return proposals
        except InvalidRelations as refused:
            print(f"      attempt {attempt}: {refused}")
            if attempt == ATTEMPTS:
                raise
            messages.append({"role": "assistant", "content": text_})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "That answer is not admissible:\n"
                        f"{refused}\n\n"
                        "Return the whole JSON again, corrected. Keep every valid relation "
                        "you already found, remove or correct only what violates the stated "
                        "rules, review the whole catalog again, and do not explain anything."
                    ),
                }
            )

    raise InvalidRelations("the relation answer stayed invalid")


def normalize_relations(
    proposals: dict,
    canonical: set[str],
    product_types_by_id: dict[str, str | None] | None = None,
) -> dict[str, dict]:
    """Leave a single write per pair, under the rule each field actually has.

    **The only transformations done here are mechanical**, and each one is
    written in the design:

    - `pairs_with` keeps the direction it arrives with, because that direction
      is the content: it goes from the accessory to the main product (A4.7).
      An exact repetition is dropped; nothing else is touched.
    - `alternative_to` moves to the lexicographically smaller `product_id` of
      the pair, which is where A4.8 says it lives. Choosing the smaller of two
      identifiers decides nothing about meaning.

    **Everything else is an error.** This function does not repair the model: it
    does not drop a reference to a product that does not exist, does not silently
    swallow a product related to itself, does not turn an invalid `relation_type`
    into `same_function`, and does not persist an `alternative_to` between
    products whose shared `product_type` already gives the service that relation
    at run time.
    """
    problems: list[str] = []
    complement_holder: dict[tuple[str, str], str] = {}
    complements: dict[str, list[str]] = {}
    alternatives: dict[tuple[str, str], str] = {}

    for product_id, proposal in sorted(proposals.items()):
        if product_id not in canonical:
            problems.append(
                f"{product_id}: relations proposed for a product that is not canonical"
            )
            continue

        for link in proposal.get("pairs_with") or []:
            other = link.get("product_id") if isinstance(link, dict) else link
            if not isinstance(other, str):
                problems.append(f"{product_id}: pairs_with entry without a product_id")
                continue
            if other == product_id:
                problems.append(f"{product_id}: pairs_with points at itself")
                continue
            if other not in canonical:
                problems.append(
                    f"{product_id}: pairs_with points at {other}, which is not canonical"
                )
                continue
            pair = tuple(sorted((product_id, other)))
            holder = complement_holder.get(pair)
            if holder is None:
                complement_holder[pair] = product_id
                complements.setdefault(product_id, []).append(other)
            elif holder != product_id:
                problems.append(
                    f"{pair[0]} · {pair[1]}: pairs_with arrives in both directions, so "
                    "which one is the accessory is undecided. It is stored once, from "
                    "the accessory towards the main product"
                )

        for link in proposal.get("alternative_to") or []:
            if not isinstance(link, dict) or "product_id" not in link:
                problems.append(f"{product_id}: alternative_to entry without a product_id")
                continue
            other = link["product_id"]
            kind = link.get("relation_type")
            if other == product_id:
                problems.append(f"{product_id}: alternative_to points at itself")
                continue
            if other not in canonical:
                problems.append(
                    f"{product_id}: alternative_to points at {other}, which is not canonical"
                )
                continue
            if kind not in RELATION_TYPE:
                problems.append(
                    f"{product_id} → {other}: relation_type {kind!r} is not valid. "
                    f"It is one of {sorted(RELATION_TYPE)}, and it is not guessed here"
                )
                continue
            if (
                product_types_by_id is not None
                and product_types_by_id.get(product_id) is not None
                and product_types_by_id.get(product_id) == product_types_by_id.get(other)
            ):
                problems.append(
                    f"{product_id} · {other}: alternative_to is already derived from "
                    f"shared product_type {product_types_by_id[product_id]!r} and must "
                    "not be persisted"
                )
                continue
            pair = tuple(sorted((product_id, other)))
            previous = alternatives.get(pair)
            if previous is None:
                alternatives[pair] = kind
            elif previous != kind:
                problems.append(
                    f"{pair[0]} · {pair[1]}: arrives as {previous!r} and as {kind!r}. "
                    "The nature of a relation is read once, in construction, and it "
                    "is not resolved by picking the safer of the two"
                )

    if problems:
        raise InvalidRelations(
            "the proposed relations are not admissible:\n  - " + "\n  - ".join(problems)
        )

    relations: dict[str, dict] = {
        product_id: {"pairs_with": [], "alternative_to": []} for product_id in canonical
    }
    for holder, others in complements.items():
        relations[holder]["pairs_with"] = sorted(others)
    for (smaller, larger), kind in sorted(alternatives.items()):
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
    canonical_ids = {p.product_id for p in canonical}

    catalog = [_card(p, entries.get(p.product_id, {})) for p in canonical]
    prompt = Path(options.prompt).read_text(encoding="utf-8")

    try:
        proposals = request_relations(
            catalog,
            prompt,
            canonical_ids,
            product_types_by_id,
        )
        relations = normalize_relations(
            proposals,
            canonical_ids,
            product_types_by_id,
        )
    except InvalidRelations as refused:
        print("\nThe proposed relations do NOT pass. Nothing is written.\n")
        print(f"  {refused}")
        return 1

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
