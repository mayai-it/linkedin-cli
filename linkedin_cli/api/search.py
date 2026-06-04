"""People and company search via Voyager's REST search-clusters endpoint.

The GraphQL form (`/voyager/api/graphql?queryId=…`) started returning 500
in late 2025; the REST endpoint at `/voyager/api/search/dash/clusters`
remains stable. Both return LinkedIn's normalized JSON, so results come
back as URN references into a top-level `included[]` array.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

from linkedin_cli.api.client import LinkedInAPIError, LinkedInClient
from linkedin_cli.api.endpoints import (
    BASE,
    SEARCH_CLUSTERS,
    SEARCH_CLUSTERS_DECORATION,
    SEARCH_GRAPHQL,
)
from linkedin_cli.models.profile import SearchHit


def search_people(
    client: LinkedInClient,
    query: str,
    *,
    company: str | None = None,
    title: str | None = None,
) -> list[SearchHit]:
    """People search via the GraphQL search-clusters endpoint.

    `queryId` rotates whenever LinkedIn ships a new web bundle and a stale
    id makes the endpoint hard-500. We walk an ordered list of candidates
    (live-scraped, then hardcoded fallbacks) and stop on the first one that
    doesn't 500. The winner is cached on the client for subsequent calls
    in the same session.
    """
    keywords_parts = [query]
    if title:
        keywords_parts.append(title)
    if company:
        keywords_parts.append(f"at {company}")
    keywords = " ".join(p for p in keywords_parts if p).strip()

    candidates = client.get_search_people_query_ids()
    last_500: LinkedInAPIError | None = None

    for query_id in candidates:
        try:
            payload = client.get_json(_build_search_url(keywords, query_id))
        except LinkedInAPIError as exc:
            if exc.status == 500:
                last_500 = exc
                continue
            raise
        client.cache_search_people_query_id(query_id)
        return _parse_people_hits(payload)

    # Every candidate 500'd — re-raise the last one with the full list of
    # ids we tried so the caller can see exactly what was attempted.
    if last_500 is None:
        raise LinkedInAPIError("no queryId candidates available for people search")
    raise LinkedInAPIError(
        f"all {len(candidates)} queryId candidates returned 500: "
        f"{', '.join(candidates)}",
        status=500,
        body=last_500.body,
    )


def _build_search_url(keywords: str, query_id: str) -> str:
    # queryParameters uses the restli `List((key:K,value:List(V)))` shape.
    # The shorthand `(resultType:List(PEOPLE))` returns 200 but with a
    # GraphQL validation error: `field name 'resultType' that is not
    # defined for input object type dash_search_SearchQueryQueryParametersInput`.
    return (
        f"{SEARCH_GRAPHQL}"
        "?includeWebMetadata=true"
        f"&variables=(query:(keywords:{_qparam(keywords)},"
        "flagshipSearchIntent:SEARCH_SRP,"
        "queryParameters:List((key:resultType,value:List(PEOPLE)))))"
        f"&queryId={query_id}"
    )


def search_companies(client: LinkedInClient, query: str) -> list[dict[str, Any]]:
    """Companies search via the REST cluster endpoint (still works for COMPANIES)."""
    url = _build_clusters_url(query, "COMPANIES")
    payload = client.get_json(url)
    return _parse_company_hits(payload)


def _build_clusters_url(keywords: str, result_type: str, count: int = 10) -> str:
    """Build the REST search-clusters URL Voyager's web client uses.

    Note: `query` and `requestContext` are *restli tuples*, not JSON, so we
    embed them as raw `(key:value,…)` text and URL-escape only the user-
    supplied keywords. The decorationId / origin params come straight from
    the web client and must be present or Voyager 400s.
    """
    return (
        f"{SEARCH_CLUSTERS}"
        "?q=all"
        f"&query=(keywords:{_qparam(keywords)},"
        "flagshipSearchIntent:SEARCH_SRP,"
        f"queryParameters:List((key:resultType,value:List({result_type}))))"
        f"&decorationId={SEARCH_CLUSTERS_DECORATION}"
        f"&count={count}"
        "&origin=SWITCH_SEARCH_VERTICAL"
        "&requestContext=(mboxId:empty)"
    )


def _qparam(value: str) -> str:
    # Voyager wants raw values inside the (a:b,c:d) tuple — we URL-escape
    # commas/colons/parens so they don't break the structure.
    return quote(value, safe="")


# ---------------------------------------------------------------------------
# response parsing — normalized JSON with URN refs into `included[]`
# ---------------------------------------------------------------------------


def _build_included_index(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for entry in payload.get("included", []) or []:
        urn = entry.get("entityUrn")
        if isinstance(urn, str):
            index[urn] = entry
    return index


def _resolve(node: Any, included: dict[str, dict[str, Any]]) -> Any:
    if isinstance(node, str) and node in included:
        return included[node]
    return node


def _deep_resolve(
    node: Any,
    included: dict[str, dict[str, Any]],
    _visited: set[str] | None = None,
) -> Any:
    """Recursively resolve LinkedIn's `*key` URN refs into inline objects.

    Voyager's normalized JSON marks references with a `*` key prefix:
      - `*foo: "urn:li:..."` → resolve URN, expose under `foo`
      - `*foo: ["urn:li:..", ...]` → resolve each URN, expose as list under `foo`
      - Non-prefixed keys are kept verbatim (e.g. trackingUrn stays a URN).
    After this walk, callers can read `obj["entityResult"]` directly instead
    of branching on `*entityResult` / `entityResult` everywhere.
    """
    if _visited is None:
        _visited = set()

    if isinstance(node, dict):
        out: dict[str, Any] = {}
        for k, v in node.items():
            if isinstance(k, str) and k.startswith("*"):
                target_key = k[1:]
                if isinstance(v, str):
                    out[target_key] = _resolve_urn(v, included, _visited)
                elif isinstance(v, list):
                    out[target_key] = [
                        _resolve_urn(u, included, _visited) if isinstance(u, str) else u
                        for u in v
                    ]
                else:
                    out[target_key] = _deep_resolve(v, included, _visited)
            else:
                out[k] = _deep_resolve(v, included, _visited)
        return out

    if isinstance(node, list):
        return [_deep_resolve(item, included, _visited) for item in node]

    if isinstance(node, str):
        # Non-prefixed strings stay as-is, even if they look like URNs:
        # the `*` convention is what marks "this should be resolved".
        return node

    return node


def _resolve_urn(
    urn: str,
    included: dict[str, dict[str, Any]],
    visited: set[str],
) -> Any:
    """Resolve a single URN and recursively expand its referenced object."""
    if urn in visited:
        # Cycle — return the bare URN so the caller has something concrete.
        return urn
    target = included.get(urn)
    if target is None:
        return urn
    visited.add(urn)
    try:
        return _deep_resolve(target, included, visited)
    finally:
        visited.discard(urn)


# EntityResultViewModel URNs are *composite*: the outer URN wraps the actual
# target URN (e.g. a profile) plus search-context tags. Example:
#   urn:li:fsd_entityResultViewModel:(urn:li:fsd_profile:ACoAAA...,SEARCH_SRP,DEFAULT)
# The composite URN itself is rarely in `included[]`, but the *inner* URN
# (the profile, company, etc.) almost always is.
_COMPOSITE_INNER_URN_RE = re.compile(r"^urn:li:[^:]+:\((urn:li:[^,)]+)")


def _extract_inner_urn(composite: str) -> str:
    match = _COMPOSITE_INNER_URN_RE.match(composite)
    return match.group(1) if match else ""


def _resolve_search_item_entity(
    raw_item: dict[str, Any],
    included: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    """Resolve a SearchItem wrapper to its underlying profile/entity dict.

    SearchItem shape:
        {
          "item": { "*entityResult": "urn:li:fsd_entityResultViewModel:(...)", ... },
          "position": N,
          "$type": "com.linkedin.voyager.dash.search.SearchItem"
        }

    Resolution:
      1. Unwrap the outer `item` key.
      2. Read `*entityResult` (or `entityResult`) — usually a composite URN.
      3. Try a direct lookup in `included[]` (covers the rare case where
         the composite URN *is* indexed).
      4. Otherwise extract the inner URN from the composite and look that
         up — that's where the profile lives.
      5. Run the result through `_deep_resolve` so any nested `*key` refs
         get expanded too.
    """
    _item = raw_item.get("item")
    actual_item: dict[str, Any] = _item if isinstance(_item, dict) else raw_item

    entity_ref = actual_item.get("*entityResult") or actual_item.get("entityResult")
    entity: Any = None

    if isinstance(entity_ref, dict):
        entity = entity_ref
    elif isinstance(entity_ref, str):
        entity = included.get(entity_ref)
        if entity is None:
            inner = _extract_inner_urn(entity_ref)
            if inner:
                entity = included.get(inner)

    if entity is None:
        # Last-ditch: some shapes hang the underlying URN ref directly on
        # the SearchItem under `*item` or `*entity`.
        for key in ("*item", "item", "*entity", "entity"):
            ref = actual_item.get(key) if key in actual_item else raw_item.get(key)
            if isinstance(ref, dict):
                entity = ref
                break
            if isinstance(ref, str):
                entity = included.get(ref) or included.get(_extract_inner_urn(ref))
                if entity is not None:
                    break

    if not isinstance(entity, dict):
        return None
    return _deep_resolve(entity, included)


def _find_cluster_container(payload: dict[str, Any]) -> dict[str, Any]:
    """Find the `searchDashClustersByAll`-equivalent container.

    Real-world response wraps it as `data.data.searchDashClustersByAll`,
    but older shapes use `data.searchDashClustersByAll` or expose
    `*elements` at the top level. Returns the first non-empty hit.
    """
    candidates: list[dict[str, Any]] = []

    data = payload.get("data") if isinstance(payload.get("data"), dict) else None
    if data:
        inner = data.get("data") if isinstance(data.get("data"), dict) else None
        for source in (inner, data):
            if not isinstance(source, dict):
                continue
            holder = source.get("searchDashClustersByAll")
            if isinstance(holder, dict):
                candidates.append(holder)
        candidates.append(data)

    candidates.append(payload)
    for c in candidates:
        if c.get("*elements") or c.get("elements"):
            return c
    return candidates[0] if candidates else {}


def _cluster_items(
    payload: dict[str, Any],
    included: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Walk clusters → items → entityResult, returning fully-resolved entities.

    Every item is run through `_deep_resolve` so that `*entityResult`,
    `*image`, `*detailData.*miniProfile`, and similar URN refs are inlined
    *before* `_entity_to_hit` sees them — that way the parser only has to
    read flat, non-prefixed keys.
    """
    if included is None:
        included = _build_included_index(payload)

    container = _find_cluster_container(payload)
    refs = container.get("*elements")
    if isinstance(refs, list) and refs:
        clusters: list[Any] = [included.get(urn, {}) for urn in refs]
    else:
        clusters = container.get("elements") or []

    out: list[dict[str, Any]] = []
    for cluster in clusters:
        cluster = _resolve(cluster, included)
        if not isinstance(cluster, dict):
            continue
        item_refs = cluster.get("*items")
        if isinstance(item_refs, list) and item_refs:
            items = [included.get(urn, {}) for urn in item_refs]
        else:
            items = cluster.get("items") or []
        for raw_item in items:
            if isinstance(raw_item, str):
                raw_item = included.get(raw_item, {})
            if not isinstance(raw_item, dict):
                continue
            entity = _resolve_search_item_entity(raw_item, included)
            if isinstance(entity, dict):
                out.append(entity)
    return out


def _parse_people_hits(payload: dict[str, Any]) -> list[SearchHit]:
    included = _build_included_index(payload)
    hits: list[SearchHit] = []
    seen: set[str] = set()

    for entity in _cluster_items(payload, included):
        hit = _entity_to_hit(entity, included)
        if not hit or not hit.name:
            continue
        dedup = hit.profile_id or hit.public_id or hit.name
        if dedup in seen:
            continue
        seen.add(dedup)
        hits.append(hit)

    # Fallback: cluster walk didn't surface any people, but the response
    # had a non-zero total — scan included[] for any miniProfile entries
    # that carry firstName/publicIdentifier. Useful when LinkedIn changes
    # the cluster shape (we still get the underlying data).
    if not hits:
        for entry in included.values():
            if not _looks_like_miniprofile(entry):
                continue
            hit = _miniprofile_to_hit(entry)
            if hit.name and hit.profile_id not in seen:
                seen.add(hit.profile_id)
                hits.append(hit)

    return hits


def _parse_company_hits(payload: dict[str, Any]) -> list[dict[str, Any]]:
    included = _build_included_index(payload)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entity in _cluster_items(payload, included):
        title = _text(entity.get("title") or entity.get("*title"), included)
        if not title:
            continue
        urn = entity.get("trackingUrn") or entity.get("entityUrn") or ""
        if urn and urn in seen:
            continue
        seen.add(urn)
        out.append(
            {
                "name": title,
                "description": _text(
                    entity.get("primarySubtitle") or entity.get("*primarySubtitle"),
                    included,
                ),
                "urn": urn,
                "url": entity.get("navigationUrl") or "",
            }
        )
    return out


def _entity_to_hit(
    entity: dict[str, Any],
    included: dict[str, dict[str, Any]],
) -> SearchHit | None:
    """Build a SearchHit from a resolved search entity.

    Handles two shapes the dash search response actually produces:

    1. **Profile** — `firstName`, `lastName`, `occupation`, `publicIdentifier`
       are present directly on the entity (what you get when the composite
       `entityResult` URN's inner ref points at a `Profile` object).
    2. **EntityResultViewModel** — `title`, `primarySubtitle`, `navigationUrl`
       on the entity, with a possible nested miniProfile reachable via
       `image.attributes[]…miniProfile`.

    We try Profile fields first since that's the common case for the
    composite-URN-resolved-to-profile path, then fall back to
    EntityResultViewModel fields and miniProfile chasing.
    """
    # --- Profile-shaped first --------------------------------------------
    first = (entity.get("firstName") or "").strip()
    last = (entity.get("lastName") or "").strip()
    name = " ".join(p for p in [first, last] if p)
    headline = (entity.get("occupation") or entity.get("headline") or "")
    if isinstance(headline, dict):
        headline = headline.get("text", "")
    public_id = entity.get("publicIdentifier") or ""

    # --- Fall back to EntityResultViewModel-shaped ------------------------
    if not name:
        name = _text(entity.get("title"), included)
    if not headline:
        headline = _text(entity.get("primarySubtitle"), included)
    nav_url = entity.get("navigationUrl") or ""
    if not public_id:
        public_id = _public_id_from_url(nav_url)

    location = _text(entity.get("secondarySubtitle"), included)

    # If we still don't have a name, look for a nested miniProfile reachable
    # via the entity's image attribute chain (the older response shape).
    if not name or not public_id or not headline:
        mini = _find_referenced_miniprofile(entity, included)
        if mini:
            if not name:
                m_first = (mini.get("firstName") or "").strip()
                m_last = (mini.get("lastName") or "").strip()
                name = " ".join(p for p in [m_first, m_last] if p)
            if not headline:
                headline = mini.get("occupation") or ""
            if not public_id:
                public_id = mini.get("publicIdentifier") or ""

    if not name:
        return None

    return SearchHit(
        profile_id=_clean_profile_urn(
            entity.get("entityUrn")
            or entity.get("objectUrn")
            or entity.get("trackingUrn")
            or ""
        ),
        public_id=public_id,
        name=name,
        headline=str(headline) if headline else "",
        location=location,
        profile_url=_clean_profile_url(nav_url, public_id),
    )


def _clean_profile_urn(urn: str) -> str:
    """Reduce a composite EntityResultViewModel URN to the inner profile URN.

    Composite shape:
      urn:li:fsd_entityResultViewModel:(urn:li:fsd_profile:ACoAAA...,SEARCH_SRP,DEFAULT)
    becomes:
      urn:li:fsd_profile:ACoAAA...
    Non-composite URNs are returned unchanged.
    """
    inner = _extract_inner_urn(urn)
    return inner or urn


def _clean_profile_url(nav_url: str, public_id: str) -> str:
    """Return a clean `https://www.linkedin.com/in/<public_id>` URL.

    `navigationUrl` in dash responses always carries a `?miniProfileUrn=…`
    tracking query — we strip everything after `?`. If `navigationUrl` is
    missing entirely we synthesize one from `public_id`.
    """
    if nav_url:
        base = nav_url.split("?", 1)[0].rstrip("/")
        if base.startswith("http"):
            return base
        return f"{BASE}{base}"
    if public_id:
        return f"{BASE}/in/{public_id}"
    return ""


def _looks_like_miniprofile(entry: dict[str, Any]) -> bool:
    type_str = str(entry.get("$type", ""))
    has_profile_type = "MiniProfile" in type_str or type_str.endswith(".Profile")
    has_name_fields = bool(entry.get("firstName") and entry.get("publicIdentifier"))
    return has_profile_type or has_name_fields


def _miniprofile_to_hit(mini: dict[str, Any]) -> SearchHit:
    first = mini.get("firstName") or ""
    last = mini.get("lastName") or ""
    name = " ".join(p for p in [first, last] if p).strip()
    public_id = mini.get("publicIdentifier") or ""
    return SearchHit(
        profile_id=mini.get("entityUrn") or mini.get("objectUrn") or "",
        public_id=public_id,
        name=name,
        headline=mini.get("occupation") or "",
        profile_url=f"{BASE}/in/{public_id}/" if public_id else "",
    )


def _find_referenced_miniprofile(
    entity: dict[str, Any],
    included: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Find a miniProfile-like object reachable from this entity.

    `entity` has already been `_deep_resolve`'d so every URN ref is inlined.
    We just walk a few common paths in the dash response (image →
    attributes → detailData → miniProfile) looking for the first dict that
    looks like a profile.
    """
    image = entity.get("image")
    if not isinstance(image, dict):
        return {}
    for attr in image.get("attributes", []) or []:
        if not isinstance(attr, dict):
            continue
        for key in ("miniProfile", "sourceType", "detailData"):
            node = attr.get(key)
            if isinstance(node, dict):
                if _looks_like_miniprofile(node):
                    return node
                # one level deeper, e.g. detailData.profilePicture.miniProfile
                for inner_key in ("miniProfile", "profile"):
                    inner = node.get(inner_key)
                    if isinstance(inner, dict) and _looks_like_miniprofile(inner):
                        return inner
                # Also handle detailData.<arbitrary>.miniProfile
                for v in node.values():
                    if isinstance(v, dict):
                        inner = v.get("miniProfile")
                        if isinstance(inner, dict) and _looks_like_miniprofile(inner):
                            return inner
    return {}


def _text(node: Any, included: dict[str, dict[str, Any]] | None = None) -> str:
    """Coerce a Voyager text node into a plain string.

    Handles:
      - inline `{"text": "..."}`
      - bare strings
      - URN refs into `included[]` (resolved when `included` is provided)
    """
    if included is not None and isinstance(node, str) and node in included:
        node = included[node]
    if isinstance(node, dict):
        return str(node.get("text") or "")
    if isinstance(node, str):
        # A plain non-URN string. URNs start with `urn:li:`; treat anything
        # else as a literal value.
        return "" if node.startswith("urn:li:") else node
    return ""


def _public_id_from_url(url: str) -> str:
    if "/in/" not in url:
        return ""
    return url.split("/in/", 1)[1].split("/", 1)[0].split("?", 1)[0]
