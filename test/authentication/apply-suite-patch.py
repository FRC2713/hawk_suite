"""Apply narrowly scoped integration hooks to the pinned upstream source only."""
from pathlib import Path


def replace(path, old, new):
    file = Path(path)
    text = file.read_text()
    if text.count(old) != 1:
        raise SystemExit("Pinned source integration anchor changed: " + path)
    file.write_text(text.replace(old, new))


replace("pkg/idp/oauth/validator.go",
        '\t\tif err := b.parseTokenClaims(tokenName, claims, data, parsedData); err != nil {',
        '''\t\tif b.expectedOAuthTokenIssuer() == "https://slack.com" && tokenName == "id_token" {
            parsedData["_hawk_slack_identity"] = map[string]interface{}{
                "sub": claims["sub"], "team": claims["https://slack.com/team_id"],
                "user": claims["https://slack.com/user_id"],
            }
        }
\t\tif err := b.parseTokenClaims(tokenName, claims, data, parsedData); err != nil {''')
replace("pkg/idp/oauth/authenticate.go",
        '\t\t\t}\n\n\t\t\tif b.config.IdentityTokenCookieEnabled {',
        '\t\t\t}\n\n\t\t\tif err := b.normalizeHawkSlackIdentity(m); err != nil { return err }\n\n'
        '\t\t\tif b.config.IdentityTokenCookieEnabled {')
# Slack integration accepts authorization-code flow only; the legacy implicit
# branch does not fetch/bind UserInfo and must not bypass normalization.
replace("pkg/idp/oauth/authenticate.go",
        '\t\tcase reqParams.idTokenExists && reqParams.accessTokenExists:',
        '''\t\tcase reqParams.idTokenExists && reqParams.accessTokenExists:
            if b.expectedOAuthTokenIssuer() == "https://slack.com" {
                return errors.ErrIdentityProviderOauthResponseProcessingFailed
            }''')
