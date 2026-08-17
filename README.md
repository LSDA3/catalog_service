# Catalog Service · Product Discovery Agent

An end-to-end product discovery system for a gift shop, built around a deterministic **Catalog Service** and a conversational **Product Discovery Agent** in indigo.ai.

The project takes a raw ecommerce catalog that was never designed for conversational discovery, canonicalizes it, enriches it with a controlled semantic layer, validates that derived data in CI, and exposes the resulting catalog through a typed FastAPI service. indigo.ai sits on top of that service and handles conversation, routing, state and tool orchestration.

The central architectural principle is that **the LLM does not search, clean or classify the raw catalog at request time**. Semantic enrichment happens during the build pipeline. At runtime, the Catalog Service works only with validated, canonical data already loaded in memory and performs deterministic filtering, precedence ordering and relationship resolution.

This separates two jobs that are easy to conflate:

- the **Catalog Service** is responsible for what products exist, what their canonical data means, which constraints they satisfy, how candidates are ordered, and which relationships between products are valid;
- the **Product Discovery Agent** is responsible for understanding the conversation, gathering what is still needed, choosing the appropriate capability, interpreting the service response and explaining recommendations naturally to the customer.

The service is containerized with Docker, deployed on Fly.io and exposed through an authenticated OpenAPI contract. The conversational layer is implemented in indigo.ai through routing, workflows, agents and API capabilities.

**Live service**

- API: `https://indigo-catalog-service.fly.dev`
- OpenAPI: `https://indigo-catalog-service.fly.dev/openapi.json`
- Swagger UI: `https://indigo-catalog-service.fly.dev/docs`

---

## The problem

Gift discovery is not the same problem as conventional catalog search.

A customer rarely arrives with a complete product query. They are more likely to say something like:

> “I need something for my sister. She has just moved house. Around €50, and I need it this week.”

The source catalog, however, was built for ecommerce storage and navigation. It contains product attributes, categories, prices and descriptive text, but it does not directly encode all the concepts needed to reason reliably about a gift conversation.

Several different problems have to be solved at once.

### Hard constraints and preferences are not the same thing

Some conditions define whether a product is actually usable. A delivery deadline or a strict maximum price cannot be treated as a vague preference.

Other signals should influence which valid products appear first without automatically eliminating everything else. The occasion, what the recipient might use the gift for, the intended function of the gift or the relationship to the recipient belong to a different class of reasoning.

Treating every signal as a hard filter produces brittle searches and unnecessary zero-result cases. Treating everything as a soft preference can produce recommendations that simply violate the customer's request.

The system therefore needs to distinguish **selection boundaries** from **signals used to order valid candidates**.

### The source data is not yet a reliable reasoning model

The input file contains real catalog imperfections that have to be absorbed by the integration layer rather than exposed to the conversational system.

The original source contains **152 rows across 17 columns**, but those rows do not correspond one-to-one with the products that should be shown to a customer. Duplicate products appear under different identifiers, category names contain formatting variants, some attributes are missing, some descriptions contain too little information to support a useful recommendation, and commercial recipient labels do not always describe a genuine restriction of the product.

After deterministic canonicalization, the service works with **150 canonical products** rather than 152 independent rows.

This distinction matters. If raw rows were treated as products:

- the same item could appear twice in a recommendation;
- category navigation could fragment across spelling or formatting variants;
- missing values could accidentally be turned into invented facts;
- commercial labels could create inappropriate ranking bias;
- alternate identifiers could fail to resolve to the same real product.

The raw catalog must therefore remain the source of truth while a deterministic integration layer converts it into a stable model suitable for discovery.

### Product attributes are not enough for gift discovery

A conventional ecommerce catalog can tell us that an item is a knife, costs €69, belongs to Kitchen & Dining and ships in two days.

That still does not fully answer questions such as:

- Is it useful for someone who enjoys cooking?
- Does it make sense as a housewarming gift?
- Is it a relatively safe choice when the buyer does not know the recipient very well?
- Is it suitable as a small additional gift rather than the main present?
- Is there another product that performs a similar function?
- Is there something that naturally pairs with it?
- If the customer wants a better version, what should count as a meaningful alternative rather than an unrelated expensive product?

Those are not facts that can be recovered reliably from price and category alone.

The project therefore introduces a **controlled semantic layer** that describes how products participate in gift-discovery decisions. This layer adds structured concepts such as intended use, functional role, gifting context, risk, relationships between products and alternative or complementary options.

The purpose is not to replace the original catalog. It is to make the catalog usable by a reasoning system without requiring the conversational model to infer those concepts from scratch on every request.

### Natural language and catalog language do not match exactly

Customers do not know the internal vocabulary of a catalog.

They may ask for a “chef knife”, a “chef's knife”, a “gyuto”, something “for cooking”, something “for someone who just moved”, or simply “something practical”.

The service therefore needs a controlled mechanism for mapping natural expressions to canonical domain concepts without forcing the customer to know internal field names or exact catalog labels.

This is also why `product_type` is not treated as a question that the agent must ask. If the customer provides a concrete object type, it can be interpreted. If they do not, discovery can proceed from function, use case and context instead.

### Zero results do not all mean the same thing

A particularly important distinction is the difference between:

- a product not existing;
- a relevant product existing but failing a hard constraint;
- the service understanding a criterion but being unable to apply it reliably;
- no valid product remaining after all applicable constraints are evaluated.

Those situations require different responses.

For example, if the shop carries a €149 chef's knife and the customer has a strict €50 limit, the correct answer is not:

> “We don't sell chef's knives.”

The service must be able to preserve the fact that the product exists while explaining that it was **excluded** because it exceeds the customer's budget.

Likewise, if part of a request cannot be mapped safely to the controlled vocabulary, the system should expose that limitation rather than pretending the criterion was applied.

This is why the API distinguishes successful results from structures such as `excluded` and `not_applied`.

### A conversational agent needs stable state, not repeated guesswork

Gift discovery normally takes more than one turn.

A customer may first provide the recipient, then a budget, then a deadline, then add that the person enjoys cooking, later increase the budget, ask to see the second result again, or request something similar but more premium.

The system must preserve what is already known while allowing later information to refine or replace earlier criteria.

It must also avoid:

- asking again for information already provided;
- rerunning identical searches unnecessarily;
- treating every user message as a completely new query;
- losing the relationship between phrases such as “the second one” and products shown earlier.

Conversational state therefore lives explicitly in the orchestration layer rather than being left entirely to the model's implicit memory.

### The LLM should not become the catalog database

A tempting implementation would be to place the CSV in model context or a retrieval system and ask an LLM to find products directly.

That would mix several responsibilities that benefit from being separated:

- data cleaning;
- canonicalization;
- filtering;
- ranking;
- relationship resolution;
- constraint enforcement;
- conversational reasoning;
- natural-language generation.

It would also make core catalog behaviour dependent on probabilistic interpretation at request time.

This project instead treats the LLM as the conversational reasoning layer and the Catalog Service as the authoritative product layer.

The agent can decide **what it needs to ask or search for**. The service decides **what the catalog actually contains and what satisfies the requested conditions**.

---

## The solution

The solution is split into three deliberately separate stages: **build-time semantic preparation, deterministic catalog runtime, and conversational orchestration**.

```text
                         BUILD / CI
                             │
                             ▼
                      raw catalog.csv
                             │
                  deterministic normalization
                             │
                             ▼
                    canonical product set
                             │
               semantic enrichment + relations
                             │
                             ▼
             validated semantic_layer.json
                    + vocabularies.yaml
                             │
                 validation + automated tests
                             │
                             ▼
                         deployment
                             │
                             │
                 ─────────────────────
                             │
                             ▼
                    CATALOG SERVICE
                             │
              catalog + semantic layer loaded
                       into memory
                             │
          deterministic filtering / precedence /
                relationships / API contract
                             │
                             ▼
                     FastAPI on Fly.io
                             │
                             │
                 ─────────────────────
                             │
                             ▼
                         indigo.ai
                             │
                     routing + state
                             │
                  discovery workflows
                             │
                     API capabilities
                             │
               Product Discovery Agent
                             │
                             ▼
                          customer
```

### 1. Build-time semantic preparation

The raw catalog remains versioned and unchanged.

A deterministic normalization layer first converts the source rows into the canonical product universe used everywhere else in the system. The same normalization implementation is shared between CI and runtime so that semantic validation and production cannot silently operate on different interpretations of the catalog.

The semantic pipeline then enriches those canonical products with the information required for gift discovery and builds relationships between them.

The result is stored as versioned derived data rather than recomputed during customer conversations.

The build pipeline validates that the semantic layer remains complete and internally coherent before deployment. If the derived catalog does not satisfy the required invariants, the new version is not deployed.

This means that semantic classification is a **controlled build concern**, not a runtime side effect.

### 2. Deterministic Catalog Service

At process startup, the service:

1. reads the original catalog;
2. applies the same deterministic canonicalization used during construction;
3. loads the validated semantic layer;
4. joins both representations;
5. builds the in-memory catalog consumed by the API.

Once startup is complete, customer requests do not trigger catalog classification or enrichment.

The service performs deterministic operations over data already in memory:

- category discovery;
- category browsing;
- criteria-based product discovery;
- product-detail retrieval;
- related-product resolution;
- hard-constraint enforcement;
- precedence ordering;
- alias resolution;
- excluded-product reporting;
- unresolved-criterion reporting.

The Catalog Service is therefore the authority for product truth.

It does not ask an LLM whether a €149 product satisfies a €50 maximum, whether two identifiers refer to the same canonical product, or whether an out-of-stock product should be returned. Those behaviours are encoded and tested as application logic.

### 3. Conversational orchestration in indigo.ai

indigo.ai provides the conversational layer on top of the API.

It is responsible for routing each turn to the correct conversational path, maintaining discovery state across turns, deciding whether enough information is available to search, calling the Catalog Service when necessary and giving the Product Discovery Agent access to the appropriate capabilities.

A dedicated discovery workflow maintains the accumulated search criteria. When the current turn creates or changes a discovery search and the required price and delivery information are available, the search workflow calls `find_products_by_criteria` and stores the returned envelope in `catalog_response`.

The Product Discovery Agent then starts from that service response rather than recreating the same catalog search independently.

The agent still has direct access to discovery capabilities when the conversation genuinely creates a new need inside its own reasoning — for example, looking for a complement, filling remaining budget, finding a related product or exploring a higher-priced alternative.

This keeps tool use flexible without allowing the LLM to replace the deterministic search path.

### Clear ownership between layers

The resulting responsibility boundary is intentional:

| Concern | Owner |
|---|---|
| Raw catalog | Versioned source data |
| Canonicalization | Catalog Service code |
| Semantic classification | Build pipeline |
| Semantic relationships | Build pipeline |
| Semantic validation | CI |
| Product truth | Catalog Service |
| Constraint enforcement | Catalog Service |
| Candidate ordering | Catalog Service |
| API contract | Catalog Service |
| Conversation routing | indigo.ai |
| Conversational state | indigo.ai workflows |
| Choosing when a capability is needed | Workflows + Product Discovery Agent |
| Interpreting API responses for the customer | Product Discovery Agent |
| Recommendation wording and reasons | Product Discovery Agent |

The separation has an important operational consequence:

> **No customer conversation can change the catalog, classify a product, alter the semantic layer or cause the Catalog Service to invoke an LLM.**

The only LLM activity related to catalog classification happens during the controlled build pipeline. At runtime, the conversational LLM consumes an authenticated API whose behaviour is deterministic and independently testable.

That architecture gives the agent the flexibility needed for natural conversation without giving it authority over facts that should remain deterministic.
