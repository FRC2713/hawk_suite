#!/usr/bin/env python3
"""Probe a pinned AuthCrunch candidate using only disposable, synthetic services.

Every check is required. This is not proof of real Slack compatibility.
"""
import argparse
import hashlib
import importlib.util
import http.cookiejar
import json
from pathlib import Path
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

FIXTURE = Path(__file__).resolve().parents[1] / "test" / "authentication"


def docker(*args):
    result = subprocess.run(["docker", *args], capture_output=True, text=True, encoding="utf-8")
    if result.returncode:
        # Container logs/configs can contain credentials. Do not print output.
        raise RuntimeError(f"docker {args[0]} failed (output suppressed)")
    return result.stdout.strip()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args):
        return None


def browser():
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                      urllib.request.HTTPCookieProcessor(jar), NoRedirect()), jar


def request(client, url, data=None, headers=None):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, body, headers or ({"Content-Type": "application/json"} if body else {}))
    try:
        response = client.open(req, timeout=3)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        return response.status, response.headers, response.read()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    result_file = FIXTURE / "results.json"
    result_file.unlink(missing_ok=True)
    receipt = json.loads((FIXTURE / "candidate.json").read_text())
    spec = importlib.util.spec_from_file_location("auth_build", Path(__file__).with_name("build-authentication.py"))
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    if receipt["source_sha256"] != builder.input_hashes():
        raise RuntimeError("Candidate inputs changed; run scripts/build-authentication.py")
    IMAGE = receipt["image_id"]
    name = "hawk-auth-probe-" + uuid.uuid4().hex[:12]
    provider, gateway = name + "-provider", name + "-gateway"
    volumes = [name + "-data", name + "-config", name + "-private", name + "-public"]
    containers = []
    network_created = False
    failures = []
    checks = []

    def check(label, condition):
        checks.append(label)
        print(("PASS " if condition else "FAIL ") + label, flush=True)
        if not condition:
            failures.append(label)

    def port(container):
        return "http://" + docker("port", container, "8080/tcp").splitlines()[0]

    try:
        docker("info")
        docker("build", "-t", "hawk-auth-oidc-fixture:local", str(FIXTURE))
        docker("network", "create", name)
        network_created = True
        for volume in volumes:
            docker("volume", "create", volume)
        docker("run", "--rm", "-v", volumes[2] + ":/private", "-v", volumes[3] + ":/public",
               "hawk-auth-oidc-fixture:local", "python", "/provider.py", "keygen")
        key_mounts = ["-v", volumes[2] + ":/private:ro", "-v", volumes[3] + ":/public:ro"]
        docker("run", "-d", "--name", provider, "--network", name, "--network-alias", "provider",
               "-v", volumes[3] + ":/public:ro",
               "-p", "127.0.0.1::8080", "hawk-auth-oidc-fixture:local")
        containers.append(provider)
        client, _ = browser()
        provider_url = port(provider)
        for _ in range(60):
            try:
                if request(client, provider_url + "/stats")[0] == 200:
                    break
            except (OSError, urllib.error.URLError):
                pass
            time.sleep(0.25)
        mount = str(FIXTURE / "Caddyfile") + ":/etc/caddy/Caddyfile:ro"
        docker("run", "--rm", "--network", name, "-v", mount, *key_mounts, IMAGE,
               "caddy", "validate", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile")
        check("candidate validates fixture", True)
        modules = docker("run", "--rm", IMAGE, "caddy", "list-modules")
        check("authentication and authorization modules", all(module in modules for module in
              ["http.handlers.authenticator", "http.authentication.providers.authorizer"]))
        build = docker("run", "--rm", IMAGE, "caddy", "build-info")
        check("pinned bundled versions", all(dep in build for dep in [
            "github.com/caddyserver/caddy/v2\tv2.11.4", "github.com/greenpau/caddy-security\tv1.1.64",
            "github.com/greenpau/go-authcrunch\tv1.1.41", "/src/library"]))
        docker("run", "-d", "--name", gateway, "--network", name, "-p", "127.0.0.1::8080",
               "-v", mount, *key_mounts, "-v", volumes[0] + ":/data", "-v", volumes[1] + ":/config", IMAGE)
        containers.append(gateway)
        gateway_url = port(gateway)
        for _ in range(60):
            try:
                if request(client, gateway_url + "/auth/login")[0] == 200:
                    break
            except (OSError, urllib.error.URLError):
                pass
            time.sleep(0.25)
        check("portal responds", request(client, gateway_url + "/auth/login")[0] == 200)

        def stats():
            return json.loads(request(client, provider_url + "/stats")[2])

        def echo(login_client):
            before = stats()["echo"]
            status = request(login_client, gateway_url + "/echo")[0]
            reached = stats()["echo"] > before
            return status == 200 and reached

        def denied(login_client):
            before = stats()["echo"]
            status = request(login_client, gateway_url + "/echo")[0]
            return status in (302, 303, 401, 403) and stats()["echo"] == before

        check("anonymous request never reaches backend", denied(client))

        def login(scenario=None, bad_state=False):
            login_client, jar = browser()
            status, headers, _ = request(login_client, gateway_url + "/auth/oauth2/slack")
            if status not in (302, 303):
                raise RuntimeError("provider redirect missing")
            query = {k: v[0] for k, v in urllib.parse.parse_qs(
                urllib.parse.urlsplit(headers["Location"]).query).items()}
            if not all(query.get(k) for k in ("nonce", "state", "redirect_uri")):
                raise RuntimeError("state/nonce/callback missing")
            data = dict(query)
            data.update(scenario or {})
            code = json.loads(request(client, provider_url + "/issue", data)[2])["code"]
            callback = gateway_url + urllib.parse.urlsplit(query["redirect_uri"]).path
            callback += "?" + urllib.parse.urlencode({"code": code,
                        "state": "unrecognized-state" if bad_state else query["state"]})
            status, headers, _ = request(login_client, callback)
            # Finish portal's local sandbox/redirect steps, never follow external URLs.
            for _ in range(8):
                if status not in (301, 302, 303, 307, 308) or not headers.get("Location"):
                    break
                target = urllib.parse.urljoin(callback, headers["Location"])
                if urllib.parse.urlsplit(target).netloc != urllib.parse.urlsplit(gateway_url).netloc:
                    break
                status, headers, _ = request(login_client, target)
            return login_client, jar

        member, jar = login()
        check("synthetic approved workspace reaches echo", echo(member))
        def suite_claims(cookies):
            token = next((cookie.value for cookie in cookies if cookie.name == "AUTHP_ACCESS_TOKEN"), "")
            status, _, payload = request(client, provider_url + "/verify-suite", {"token": token})
            check("suite JWT independently verified with public key only", status == 200)
            if status != 200:
                raise RuntimeError("Suite JWT verification failed at " + json.loads(payload).get("stage", "unknown")
                                   + "; cookie present: " + str(bool(token)))
            return token, json.loads(payload)["claims"]

        token, claims = suite_claims(jar)
        check("workspace-qualified subject and explicit Slack IDs", claims.get("sub") == "slack:TAPPROVED:UFIXTURE"
              and claims.get("slack_team_id") == "TAPPROVED" and claims.get("slack_user_id") == "UFIXTURE")
        check("suite issuer audience roles and 7-day lifetime", claims.get("iss") == "https://apps.suite.test/"
              and claims.get("aud") == "hawk-suite" and "suite/member" in claims.get("roles", [])
              and "suite/admin" not in claims.get("roles", []) and claims["exp"] - claims["iat"] == 604800
              and type(claims["roles"]) is list and all(type(role) is str for role in claims["roles"])
              and all(type(claims.get(field)) is int for field in ("exp", "iat", "nbf"))
              and claims["nbf"] == claims["iat"] - 60 and bool(claims.get("jti"))
              and all(type(claims.get(field)) is str for field in ("jti", "name", "email")))
        check("provider tokens and raw UserInfo excluded", not set(claims).intersection(
            {"userinfo", "_hawk_slack_identity", "access_token", "id_token", "refresh_token"}))
        for label, overrides, allowed in [
            ("valid bearer", {}, True),
            ("wrong suite issuer", {"iss": "https://other.invalid/"}, False),
            ("wrong suite audience", {"aud": "other-suite"}, False),
            ("missing suite issuer", {"iss": None}, False),
            ("missing suite audience", {"aud": None}, False),
            ("expired suite token", {"exp": 1}, False),
            ("future suite token", {"nbf": int(time.time()) + 3600}, False),
            ("insufficient suite role", {"roles": ["authp/user"]}, False),
        ]:
            payload = {k: v for k, v in {**claims, **overrides}.items() if v is not None}
            signed = subprocess.run(["docker", "run", "--rm", "-i", "-v", volumes[2] + ":/private:ro",
                                     "hawk-auth-oidc-fixture:local", "python", "/provider.py", "mint-suite"],
                                    input=json.dumps(payload), capture_output=True, text=True, encoding="utf-8", check=True).stdout
            fresh, _ = browser()
            before = stats()["echo"]
            status = request(fresh, gateway_url + "/echo", headers={"Authorization": "Bearer " + signed})[0]
            hits = stats()["echo"] - before
            check(label + " gateway trust enforcement", (status == 200 and hits == 1) if allowed
                  else (status in (302, 303, 401, 403) and hits == 0))
        parts = token.split(".")
        parts[1] = parts[1][:-1] + ("A" if parts[1][-1] != "A" else "B")
        check("independent verifier rejects tampered suite JWT", request(client, provider_url + "/verify-suite",
              {"token": ".".join(parts)})[0] == 401)
        _, renamed_jar = login({"claims": {"email": "renamed@example.invalid", "name": "Renamed Fixture"}})
        _, renamed = suite_claims(renamed_jar)
        check("display changes preserve stable subject", renamed["sub"] == claims["sub"])
        for label, scenario in [
            ("wrong workspace", {"claims": {"https://slack.com/team_id": "TOTHER"}}),
            ("wrong workspace with forged member role", {"claims": {"https://slack.com/team_id": "TOTHER", "roles": ["suite/member"]}}),
            ("missing workspace", {"omit": ["https://slack.com/team_id"]}),
            ("malformed workspace", {"claims": {"https://slack.com/team_id": ["TAPPROVED"]}}),
            ("missing Slack user ID", {"omit": ["https://slack.com/user_id"]}),
            ("ID-token user mismatch", {"claims": {"https://slack.com/user_id": "UOTHER"}}),
            ("UserInfo subject mismatch", {"userinfo": {"sub": "UDIFFERENT"}}),
            ("UserInfo workspace mismatch", {"userinfo": {"https://slack.com/team_id": "TOTHER"}}),
            ("UserInfo user mismatch", {"userinfo": {"https://slack.com/user_id": "UOTHER"}}),
            ("wrong nonce", {"claims": {"nonce": "wrong"}}),
            ("wrong issuer", {"claims": {"iss": "https://other.invalid"}}),
            ("wrong audience", {"claims": {"aud": "other-client"}}),
            ("expired ID token", {"claims": {"exp": 1}}),
            ("tampered ID token", {"tamper": True}),
        ]:
            rejected, rejected_jar = login(scenario)
            check(label + " never reaches backend", denied(rejected))
            check(label + " issues no suite access token", not any(c.name == "AUTHP_ACCESS_TOKEN" for c in rejected_jar))
        before = stats()["token"]
        rejected, _ = login(bad_state=True)
        check("unknown state rejected before token exchange", stats()["token"] == before and denied(rejected))
        mismatched, _ = login({"claims": {"https://slack.com/team_id": "TOTHER"},
                               "userinfo": {"sub": "UDIFFERENT", "https://slack.com/team_id": "TAPPROVED"}})
        check("original cross-workspace UserInfo regression denied", denied(mismatched))
        forged_roles, roles_jar = login({"claims": {"roles": ["suite/admin", "mod/admin"]}})
        _, roles_claims = suite_claims(roles_jar)
        check("provider cannot grant privileged suite roles", echo(forged_roles)
              and not {"suite/admin", "mod/admin"}.intersection(roles_claims["roles"]))
        docker("exec", gateway, "caddy", "reload", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile")
        check("reload preserves signed session", echo(member))
        docker("exec", gateway, "sh", "-c", "test -s /config/caddy/autosave.json && echo fixture > /data/probe-marker")
        docker("restart", gateway)
        gateway_url = port(gateway)
        for _ in range(60):
            try:
                if request(client, gateway_url + "/auth/login")[0] == 200:
                    break
            except (OSError, urllib.error.URLError):
                pass
            time.sleep(0.25)
        check("restart preserves signed session", echo(member))
        check("mounted data survives restart", docker("exec", gateway, "cat", "/data/probe-marker") == "fixture")
        evidence_files = [FIXTURE / "Caddyfile", FIXTURE / "provider.py", FIXTURE / "Dockerfile", Path(__file__).resolve()]
        result_file.write_text(json.dumps({"image_id": IMAGE, "passed": not failures,
            "checks": checks, "failures": failures, "real_slack_tested": False,
            "fixture_sha256": {file.name: hashlib.sha256(file.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
                               for file in evidence_files}}, indent=2) + "\n", encoding="utf-8")
        print(f"Results: {len(checks)} checks, {len(failures)} failures. Real Slack login remains untested.")
        return 1 if failures else 0
    finally:
        for container in reversed(containers):
            docker("rm", "-f", container)
        if network_created:
            docker("network", "rm", name)
        for volume in volumes:
            # Only names generated for this invocation; never touch suite volumes.
            subprocess.run(["docker", "volume", "rm", volume], capture_output=True)


if __name__ == "__main__":
    raise SystemExit(main())
