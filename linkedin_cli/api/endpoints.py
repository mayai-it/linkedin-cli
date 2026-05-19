"""Voyager API endpoints used by linkedin-cli.

Verified against the LinkedIn web client around late 2025. These URLs are
not officially documented and the `queryId` hashes can change when LinkedIn
ships a new web build — keep this file as the single source of truth.

Voyager `client-version` used in `x-li-track`: 1.13.44236.
"""

from __future__ import annotations

BASE = "https://www.linkedin.com"
VOYAGER = f"{BASE}/voyager/api"

CLIENT_VERSION = "1.13.44236"

# --- Profile -----------------------------------------------------------------

PROFILE_BY_USERNAME = (
    f"{VOYAGER}/identity/dash/profiles"
    "?q=memberIdentity"
    "&memberIdentity={username}"
    "&decorationId=com.linkedin.voyager.dash.deco.identity.profile.TopCardSupplementary-140"
)

# --- Search ------------------------------------------------------------------

# LinkedIn rotates the people-search queryId every time it ships a new web
# bundle, and a stale id makes the endpoint return HTTP 500. We carry a
# small ordered list of known-good ids as fallbacks; the live id we scrape
# from the search results HTML page (see `LinkedInClient`) is tried first.
SEARCH_PEOPLE_QUERY_ID_FALLBACKS: tuple[str, ...] = (
    "voyagerSearchDashClusters.02af92d4df45aef4ee11b7c453545c26",
    "voyagerSearchDashClusters.994bf4e7d2173b92ccdb5935710c3c5d",
)

# Kept for back-compat in case any caller imports the singular form.
SEARCH_PEOPLE_QUERY_ID = SEARCH_PEOPLE_QUERY_ID_FALLBACKS[0]
SEARCH_COMPANIES_QUERY_ID = SEARCH_PEOPLE_QUERY_ID

SEARCH_GRAPHQL = f"{VOYAGER}/graphql"
# The HTML page LinkedIn's own search bar lands on. We scrape it once per
# session to recover the live `voyagerSearchDashClusters.<hash>` queryId,
# which rotates whenever LinkedIn ships a new web bundle and otherwise
# hard-500s the search endpoint.
SEARCH_PEOPLE_RESULTS_PAGE = f"{BASE}/search/results/people/?keywords=test"
SEARCH_CLUSTERS = f"{VOYAGER}/search/dash/clusters"
SEARCH_CLUSTERS_DECORATION = (
    "com.linkedin.voyager.dash.deco.search.SearchClusterCollection-175"
)

# Typeahead is the simple, stable alternative — used by LinkedIn's search bar
# autocomplete. It returns ranked profile suggestions with name, headline,
# and a `targetUrn` we can turn into a profile id.
TYPEAHEAD = f"{VOYAGER}/typeahead/hitsV2"

# Blended search is LinkedIn's classic stable search endpoint — accepts
# explicit filters like `(key:resultType,value:List(PEOPLE))`.
SEARCH_BLENDED = f"{VOYAGER}/search/blended"

# --- Messaging ---------------------------------------------------------------

CONVERSATIONS_QUERY_ID = "messengerConversations.0d5e6781bbee71c3e51c8843c6519f48"
CONVERSATIONS_GRAPHQL = f"{VOYAGER}/voyagerMessagingGraphQL/graphql"

MESSAGES_SEND = f"{VOYAGER}/messaging/conversations"

# --- Connections -------------------------------------------------------------

CONNECTIONS_LIST = (
    f"{VOYAGER}/relationships/dash/connections"
    "?q=search&start={start}&count={count}"
)

INVITATIONS_PENDING = (
    f"{VOYAGER}/relationships/invitationViews"
    "?q=receivedInvitation&start=0&count=40"
)

INVITATIONS_SEND = f"{VOYAGER}/growth/normInvitations"
