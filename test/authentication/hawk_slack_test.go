// Copyright 2026 Hawk Suite contributors
// SPDX-License-Identifier: Apache-2.0

package oauth

import "testing"

func TestHawkSlackIdentity(t *testing.T) {
	for _, tc := range []struct {
		name   string
		change func(map[string]interface{})
		allow  bool
	}{
		{"valid", func(m map[string]interface{}) {}, true},
		{"userinfo missing", func(m map[string]interface{}) { delete(m, "userinfo") }, false},
		{"userinfo subject mismatch", func(m map[string]interface{}) { m["userinfo"].(map[string]interface{})["sub"] = "UOTHER" }, false},
		{"userinfo workspace mismatch", func(m map[string]interface{}) {
			m["userinfo"].(map[string]interface{})["https://slack.com/team_id"] = "TOTHER"
		}, false},
		{"userinfo user mismatch", func(m map[string]interface{}) {
			m["userinfo"].(map[string]interface{})["https://slack.com/user_id"] = "UOTHER"
		}, false},
		{"id missing", func(m map[string]interface{}) { delete(m, "_hawk_slack_identity") }, false},
		{"team malformed", func(m map[string]interface{}) {
			m["_hawk_slack_identity"].(map[string]interface{})["team"] = []string{"TAPPROVED"}
		}, false},
		{"subject overwritten", func(m map[string]interface{}) { m["sub"] = "UOTHER" }, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			b := &IdentityProvider{config: &Config{Issuer: "https://slack.com"}}
			m := map[string]interface{}{"sub": "UFIXTURE", "roles": []string{"suite/admin"},
				"_hawk_slack_identity": map[string]interface{}{"sub": "UFIXTURE", "team": "TAPPROVED", "user": "UFIXTURE"},
				"userinfo":             map[string]interface{}{"sub": "UFIXTURE", "https://slack.com/team_id": "TAPPROVED", "https://slack.com/user_id": "UFIXTURE"}}
			tc.change(m)
			if (b.normalizeHawkSlackIdentity(m) == nil) != tc.allow {
				t.Fatal("unexpected identity acceptance")
			}
			if tc.allow {
				if m["sub"] != "slack:TAPPROVED:UFIXTURE" || m["slack_team_id"] != "TAPPROVED" || m["slack_user_id"] != "UFIXTURE" {
					t.Fatal("incorrect identity mapping")
				}
				roles := m["roles"].([]string)
				if len(roles) != 1 || roles[0] != "slack/workspace/TAPPROVED" {
					t.Fatal("provider roles retained")
				}
			}
		})
	}
}
