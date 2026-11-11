# Hawk Eye MCP sign-in

Status: built, not deployed. Written 2026-11-11.

The Eye side is on hawk-eye branch `claude/mcp-oauth`. The gateway side is on
this repo's `claude/hawk-suite-oauth-support-4cc654`. Neither is merged.

A workspace member should be able to add `https://scout.<domain>/mcp` to an
MCP client such as Claude or Claude Code, sign in with Slack, read scouting
data, and add scouting reports. Nobody should have to hand them a JWT.

This covers Hawk Eye only. Hawk Shop could follow the same pattern later.
Hawk Mod should never expose MCP, given what its database holds.

## Today

Eye's `/mcp` endpoint is stateless Streamable HTTP. It accepts only an HS256
JWT signed with `JWT_SECRET`, which an operator issues with
`npm run auth:token`. Tokens last at most 24 hours and cannot be revoked one at
a time. GET and DELETE return 405. Eye rejects any `Origin` other than its own.

`authenticate()` in `src/server/auth.ts` returns 503 whenever `JWT_SECRET` is
unset, before it reads the request. Production Eye has no `JWT_SECRET`, so
today every `/mcp` call there would get 503. Its 401 sends only
`WWW-Authenticate: Bearer realm="hawk-eye"`, which gives a client nothing to
discover.

The gateway answers `/mcp` with 503, and `eye-proxy` strips `Authorization` on
every Eye route. Workspace members already get `reports:read` and
`analysis:read` on browser pages through the gateway's identity headers
(`suitePrincipal()` in `src/server/suite-identity.ts`).

Checked against hawk-eye `main` at `19df30b` (2026-11-08), with no open PRs.

## Non-goals

- Granting `analysis:manage` (rebuilds, processing status) through OAuth.
  Tablets and operators keep operator-issued JWTs.
- Opening the REST API. It stays blocked at the proxy.
- Browser-based MCP clients. CORS stays off and Eye's `Origin` check stays.
- A suite-wide authorization server. Revisit that if a second app needs MCP.

## Design

Eye becomes the OAuth authorization server for a single protected resource,
its own `/mcp` endpoint. The gateway still does the Slack sign-in. Eye only
issues tokens to someone the gateway has already identified as a
`suite/member`.

### Flow

1. The client POSTs to `/mcp` without a token. Eye returns 401 with
   `WWW-Authenticate: Bearer resource_metadata="https://scout.<domain>/.well-known/oauth-protected-resource/mcp"`.
2. The client reads that document (RFC 9728). It names
   `https://scout.<domain>` as the authorization server.
3. The client reads `/.well-known/oauth-authorization-server` (RFC 8414) and
   registers at `/oauth/register` (RFC 7591).
4. The client opens the browser at `/oauth/authorize` with PKCE (S256), `state`
   and `resource=https://scout.<domain>/mcp` (RFC 8707). The gateway sends the
   person through Slack sign-in if they have no session.
5. Eye reads the trusted identity headers, shows a consent page, and redirects
   back with a one-use code.
6. The client trades the code at `/oauth/token` for an access token and a
   refresh token, then calls `/mcp` with `Authorization: Bearer`.

### Endpoints

| Path | Methods | Gateway | Purpose |
| --- | --- | --- | --- |
| `/.well-known/oauth-protected-resource` and `/.well-known/oauth-protected-resource/mcp` | GET, HEAD | Public | Resource metadata |
| `/.well-known/oauth-authorization-server` | GET, HEAD | Public | Authorization server metadata |
| `/oauth/register` | POST | Public | Dynamic client registration |
| `/oauth/token` | POST | Public | Code and refresh token exchange |
| `/oauth/revoke` | POST | Public | Token revocation (RFC 7009) |
| `/oauth/authorize` | GET, HEAD, POST | Member | Consent page. Approve and Deny post back with `?_action=` |
| `/oauth/connections` | GET, HEAD, POST | Member | Connected clients. Disconnect posts back with `?_action=` |
| `/mcp` | POST, GET, DELETE | Public, bearer token | MCP. GET and DELETE still return 405 |

"Public" means the gateway does not require a session. Eye still checks every
request. Public routes receive no identity headers.

### Tokens

| Item | Lifetime | Notes |
| --- | --- | --- |
| Authorization code | 60 seconds | One use. Bound to client, redirect URI, PKCE challenge, resource and subject |
| Access token | 1 hour | Opaque, prefixed `hek_at_` |
| Refresh token | Until the grant ends | Opaque, prefixed `hek_rt_`. Rotates on every use. Reusing an old one revokes the whole grant |
| Grant | 7 days from consent | Matches the gateway session. After that the person signs in and approves again |

Eye stores SHA-256 hashes of tokens, never the tokens themselves.

The 7-day limit matters. The gateway checks workspace membership only at
sign-in, so someone removed from Slack keeps a gateway session for up to 7
days today. Grants get the same limit and no longer.

Opaque tokens beat JWTs here for three reasons. Revocation takes effect on the
next request. Eye needs no new signing key. Rotating `JWT_SECRET` stays
independent of OAuth. The lookup is one indexed SQLite read per MCP request.

`/mcp` picks the verifier by prefix. A `hek_at_` token goes to the OAuth table
and never touches `JWT_SECRET`. Anything else goes to the existing JWT check,
so operator tokens keep working where `JWT_SECRET` is set. With OAuth on, every
authentication failure on `/mcp` is a 401 with the `resource_metadata` header,
including a JWT sent while `JWT_SECRET` is unset. A client holding a stale
token can then sign in again instead of hitting a 503.

### Permissions

OAuth scopes are Eye's existing permission names. Any member may request
`reports:read`, `analysis:read` and `reports:write`. A request for anything
else fails with `invalid_scope`. With no scope, Eye grants all three.

That exposes `get_report`, `get_raw_report`, `list_reports`, `list_matches`,
`list_teams` and `upload_report`. Uploaded reports record the member's
`slack:<team>:<user>` subject as the uploader, are immutable like every other
report, and share the per-subject upload rate limit with REST. Browser pages
still never get `reports:write`. Limiting uploads to Slack Owners and Admins
was considered and dropped: Eye would need its own Slack token to read
`users.info`. Eye's tool filtering and per-call permission checks stay as
they are. The principal's `sub` is `slack:<team>:<user>`, and Eye records the
OAuth `client_id` alongside it.

### Client registration

Registration is open, which the MCP clients expect. To keep it safe:

- Only public clients (`token_endpoint_auth_method: none`), so PKCE is the
  only proof.
- Redirect URIs must be `https`, or `http` on a loopback address. Loopback
  matches any port (RFC 8252). Everything else must match exactly.
- Eye rate-limits registration and deletes clients that have no grant after 24
  hours.
- The client name is whatever the client says. The consent page labels it as
  unverified and shows the redirect host in large text.

Client ID metadata documents are the other registration method in the MCP
spec. They make Eye fetch a URL the client chooses, which needs SSRF guards.
Leave them out of the first release.

The MCP SDK's server-side OAuth router is built for Express, and Eye runs on
Astro's Node adapter, so Eye has its own small handlers in `src/server/oauth/`.

### Astro's origin check

`astro.config.mjs` sets `security.checkOrigin: true`. In Astro 7.2.10 that
rejects any POST with a form content type whose `Origin` doesn't match. OAuth
clients must send `/oauth/token` and `/oauth/revoke` as
`application/x-www-form-urlencoded` without an `Origin` header, so the
built-in check blocks every token exchange.

Turn off the built-in check and run the same rule as Eye middleware, with
exactly two exemptions, POST `/oauth/token` and POST `/oauth/revoke`. Those
handlers take no cookies and no session, so they gain nothing from the check.
`/oauth/register` and `/mcp` send JSON and pass the rule unchanged. Tests must
show that every existing action and form still gets 403 cross-site.

### Consent and management

The consent page shows the client name, the redirect host, and the access in
plain words: "read scouting reports and match analysis for all events". It
also says that the client and its AI provider will see the data. Approve and
Deny are Astro form actions, as Eye's AGENTS.md requires. A form action posts
back to the page's own path with an `_action` query, so the gateway allowlist
needs only the page paths. The form carries the pending request's id, which
is random, used once, and bound to the signed-in subject. The origin check
applies.

`/oauth/connections` lists the person's grants with client name, redirect
host, created and last-used times, plus a Disconnect button. Operators get
`npm run oauth:revoke -- --subject <sub>` and `-- --all` through
`docker compose exec`.

### Audit

Eye records grant creation, refresh, reuse detection, revocation and expiry,
and each MCP tool call with `sub` and `client_id`. It stores them in an audit
table in `app.db`.

### Data and backups

New tables in `app.db` hold clients, pending codes, grants, token hashes and
audit rows. The encrypted suite backup already covers `app.db`.

Restoring an older backup brings back grants revoked since that backup, for
at most 7 days. After a restore, run `oauth:revoke -- --all` if that matters.

### Configuration

Eye reads a new `MCP_OAUTH_ENABLED` setting and refuses to start with it on
unless `HAWK_SUITE_TRUST_PROXY=true`. With it off, every OAuth route returns
404 and `/mcp` behaves as it does now.

The authentication overlay sets it. `.env`, `setup.sh` and the base
`Caddyfile` do not change. Without the gateway there is no Slack sign-in to
build on, so `/mcp` stays blocked on non-authenticated installs. No new
secrets are needed.

Eye must list the setting in `optional_env` in `.hawk/app.json` and accept it
in `scripts/deploy-check.mjs`. The suite's `scripts/deploy.py` refuses any app
setting the image contract doesn't declare, so the Eye image has to ship
before the suite overlay sets it.

## Changes in this repo

### Gateway routes

Add two snippets to `config/authentication/Caddyfile.template`. Both strip
cookies, so Eye never sees a gateway session on these routes.

```caddyfile
(eye-oauth-proxy) {
    reverse_proxy hawk-eye:3000 {
        header_up -Authorization
        header_up -Cookie
        header_up -X-Hawk-Identity
    }
}
(eye-mcp-proxy) {
    # The bearer token is the only credential /mcp accepts.
    reverse_proxy hawk-eye:3000 {
        header_up -Cookie
        header_up -X-Hawk-Identity
    }
}
```

In the `scout` block, before the 503 fallback:

```caddyfile
@eyeOAuthMetadata {
    method GET HEAD
    path /.well-known/oauth-protected-resource /.well-known/oauth-protected-resource/mcp /.well-known/oauth-authorization-server
}
route @eyeOAuthMetadata {
    import eye-oauth-proxy
}
@eyeOAuthPublic {
    method POST
    path /oauth/register /oauth/token /oauth/revoke
    expression {http.request.uri.query} == ''
}
route @eyeOAuthPublic {
    import eye-oauth-proxy
}
@eyeOAuthMember {
    method GET HEAD POST
    path /oauth/authorize /oauth/connections
}
route @eyeOAuthMember {
    import eye-protected
}
@eyeMcp {
    method POST GET DELETE
    path /mcp
    expression {http.request.uri.query} == ''
}
route @eyeMcp {
    import eye-mcp-proxy
}
```

`clear-suite-identity` already runs first in the `scout` block, so public
routes cannot carry forged identity headers. The `suite` policy reads the
`Authorization` header before the cookie, so a request carrying an MCP token
is sent to sign-in. A bearer token can never stand in for a session on the
consent page, and `eye-protected` strips `Authorization` before Eye sees it.

Bump `CADDY_CONFIG_REV` in `config/authentication/compose.yaml`, and add
`MCP_OAUTH_ENABLED: "true"` to `hawk-eye` there. `test/test_deployment.py`
asserts the exact overlay environment for each gated app, so update its
expectation for `hawk-eye`.

### Routing tests

`scripts/test-auth-routing.py` covers:

- POST, GET and DELETE `/mcp` reach Eye with the client's `Authorization`
  header intact and with no `Cookie` or identity headers, even when the request
  sends a valid suite cookie and forged `X-Token-*` headers.
- `/mcp` with a query string, `/mcp/`, `/mcp/extra`, `/mcpx`, PUT and PATCH
  return 503.
- The metadata documents and the register, token and revoke endpoints reach
  Eye with no credentials or identity. Query strings, trailing paths, other
  methods and `/.well-known/openid-configuration` return 503.
- `/oauth/authorize` and `/oauth/connections` send anonymous visitors, and
  anyone carrying a bearer token, to sign-in. With a session, Eye gets the
  identity headers and no `Authorization`. Their `?_action=` POSTs follow the
  same rules.
- The REST paths stay 503.

The fixture backend now answers DELETE, so the proxied DELETE can be checked.

### Docs

- `docs/hawk-eye.md`: describe the new routes. Also fix its gateway paragraph,
  which still says Eye checks `AUTHP_ACCESS_TOKEN` with
  `HAWK_SUITE_PUBLIC_KEY`. Eye now trusts the gateway's identity headers.
- `docs/continuous-deployment.md`: update the `scout` row of the route table.
- `CLAUDE.md` and `PLAN.md`: MCP is no longer blocked on authenticated releases.

## Changes in FRC2713/hawk-eye

- Metadata documents, the OAuth endpoints, consent and connections pages.
- The 401 `WWW-Authenticate` header on `/mcp`, and the prefix-based verifier
  that works without `JWT_SECRET`.
- The origin-check middleware replacing `security.checkOrigin`.
- `MCP_OAUTH_ENABLED` in `config.ts`, `.hawk/app.json` and `deploy-check.mjs`.
- Migration `0008` for the new tables, the revoke command, and rate limits.
- Tests for PKCE, code reuse, refresh rotation and reuse detection, the
  `resource` check, scope refusal, grant expiry, and redirect URI matching.
- Update `mcp.md` and `authentication.md`, which say Eye has no OAuth
  discovery or registration.

## Rollout

1. Ship an Eye image with the feature built in and the flag off. Nothing
   changes in production.
2. Open one suite PR with the routes, the overlay setting, tests and docs, and
   promote the Eye image in the same release.
3. Run Deploy. Then check against production:
   - Anonymous `/mcp` returns 401 with the metadata header.
   - A request with only a suite cookie on `/mcp` returns 401.
   - MCP Inspector and Claude complete sign-in and list the five read tools
     plus `upload_report`.
   - Uploading a report through Claude records the member as the uploader.
   - Asking for `analysis:manage` fails.
   - Disconnecting a client stops its next request.
4. To roll back, remove the overlay setting and the routes. Existing grants
   become unreachable and expire within 7 days.

## Risks

Scouting data leaves the suite. A connected client sends what it reads to its
AI provider. Approving a client should be a team decision as much as a
technical one, and the consent text says so.

Client names are unverified. Someone could register a client named "Claude"
that redirects to their own host and send a member the link. The redirect
host on the consent page is the main defense. An allowlist of known redirect
hosts would be stronger and less convenient.

Report fields are untrusted text written by scouts. An agent reading them can
meet prompt injection, and with `reports:write` it could be talked into
uploading fabricated reports. Those can't be deleted; they are attributed to
the member and logged with the client in `oauth_audit`, so they can be found
and superseded. Rebuilds stay out of reach.

Any member can add reports, not only scouts or mentors. Bad data reaches
analysis until someone notices.

## Open questions

- Allow any client, or only a list of known redirect hosts?
- Should pick-list edits ever be MCP tools?
- Should tablets get `reports:write` through a sign-in flow instead of
  operator JWTs? That would be a separate design, probably the device
  authorization grant.
- Should uploads be limited to some members later, for example Slack Owners
  and Admins, or a user group?
- How long should Eye keep audit rows?
