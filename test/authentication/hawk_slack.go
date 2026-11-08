// Copyright 2026 Hawk Suite contributors
// SPDX-License-Identifier: Apache-2.0

package oauth

import (
	"fmt"
	"regexp"
)

var hawkSlackTeam = regexp.MustCompile(`^T[A-Z0-9]+$`)
var hawkSlackUser = regexp.MustCompile(`^[UW][A-Z0-9]+$`)

// normalizeHawkSlackIdentity binds UserInfo to verified ID-token identity.
// Only the explicitly configured Slack issuer enables this integration.
func (b *IdentityProvider) normalizeHawkSlackIdentity(m map[string]interface{}) error {
	if b.expectedOAuthTokenIssuer() != "https://slack.com" {
		return nil
	}
	id, ok := m["_hawk_slack_identity"].(map[string]interface{})
	delete(m, "_hawk_slack_identity")
	if !ok {
		return fmt.Errorf("verified Slack identity missing")
	}
	team, teamOK := id["team"].(string)
	user, userOK := id["user"].(string)
	sub, subOK := id["sub"].(string)
	if !teamOK || !userOK || !subOK || !hawkSlackTeam.MatchString(team) || !hawkSlackUser.MatchString(user) || sub != user || m["sub"] != sub {
		return fmt.Errorf("invalid Slack identity claims")
	}
	info, ok := m["userinfo"].(map[string]interface{})
	if !ok || info["sub"] != sub || info["https://slack.com/team_id"] != team || info["https://slack.com/user_id"] != user {
		return fmt.Errorf("Slack UserInfo identity mismatch")
	}
	m["sub"] = "slack:" + team + ":" + user
	m["slack_team_id"] = team
	m["slack_user_id"] = user
	// Provider roles are not suite authorization. Reviewed portal transforms
	// grant suite roles by matching this verified workspace marker.
	m["roles"] = []string{"slack/workspace/" + team}
	delete(m, "userinfo")
	return nil
}
