// Copyright 2026 Hawk Suite contributors
// SPDX-License-Identifier: Apache-2.0

package oauth

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"go.uber.org/zap"
)

// This regression is copied into the pinned upstream package by Dockerfile.patch-test.
func TestHawkUserInfoSubjectBinding(t *testing.T) {
	for _, tc := range []struct {
		name   string
		sub    interface{}
		status int
		allow  bool
	}{
		{"same subject", "U_FIXTURE", http.StatusOK, true},
		{"different subject", "U_OTHER", http.StatusOK, false},
		{"missing subject", nil, http.StatusOK, false},
		{"empty subject", "", http.StatusOK, false},
		{"malformed subject", []string{"U_FIXTURE"}, http.StatusOK, false},
		{"unsuccessful response", "U_FIXTURE", http.StatusUnauthorized, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.WriteHeader(tc.status)
				json.NewEncoder(w).Encode(map[string]interface{}{
					"sub": tc.sub, "https://slack.com/team_id": "T_APPROVED",
				})
			}))
			defer server.Close()
			provider := &IdentityProvider{
				config: &Config{Driver: "generic"}, logger: zap.NewNop(),
				scopeMap:               map[string]interface{}{"openid": true},
				userInfoFields:         map[string]interface{}{"all": true},
				userInfoRolesFieldName: "https://slack.com/team_id", userInfoURL: server.URL,
			}
			user := map[string]interface{}{"sub": "U_FIXTURE"}
			err := provider.fetchUserInfo(map[string]interface{}{"access_token": "synthetic"}, user)
			if (err == nil) != tc.allow {
				t.Fatal("unexpected UserInfo acceptance result")
			}
			_, hasRoles := user["roles"]
			_, hasInfo := user["userinfo"]
			if tc.allow != hasRoles || tc.allow != hasInfo {
				t.Fatal("unexpected propagation of UserInfo claims")
			}
		})
	}
}
