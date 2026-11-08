"""Disposable Slack-shaped OIDC provider and counting echo backend. No real accounts."""
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PUBLIC = KEY.public_key().public_numbers()
ISSUER = "https://slack.com"
BASE = "http://provider:8080"
CODES = {}
TOKENS = {}
COUNTS = {"echo": 0, "token": 0}


def b64(value):
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def integer(value):
    return b64(value.to_bytes((value.bit_length() + 7) // 8, "big"))


def jwt(claims):
    data = ".".join(b64(json.dumps(v).encode()) for v in (
        {"alg": "RS256", "kid": "fixture"}, claims)).encode()
    return data.decode() + "." + b64(KEY.sign(data, padding.PKCS1v15(), hashes.SHA256()))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass  # Never log codes, cookies, or tokens, including synthetic ones.

    def respond(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/.well-known/openid-configuration":
            return self.respond({
                "issuer": ISSUER, "authorization_endpoint": BASE + "/authorize",
                "token_endpoint": BASE + "/token", "userinfo_endpoint": BASE + "/userinfo",
                "jwks_uri": BASE + "/keys", "scopes_supported": ["openid", "profile", "email"],
                "response_types_supported": ["code"], "subject_types_supported": ["public"],
                "id_token_signing_alg_values_supported": ["RS256"],
                "token_endpoint_auth_methods_supported": ["client_secret_post", "client_secret_basic"],
            })
        if path == "/keys":
            return self.respond({"keys": [{"kty": "RSA", "kid": "fixture", "use": "sig",
                                         "alg": "RS256", "n": integer(PUBLIC.n), "e": integer(PUBLIC.e)}]})
        if path == "/stats":
            return self.respond(COUNTS)
        if path == "/echo":
            COUNTS["echo"] += 1
            return self.respond({"backend": "synthetic-echo"})
        if path == "/userinfo":
            claims = TOKENS.get(self.headers.get("Authorization", "").removeprefix("Bearer "))
            if claims is None:
                return self.respond({"error": "invalid_token"}, 401)
            return self.respond(claims)
        self.respond({"error": "not_found"}, 404)

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        if self.path == "/verify-suite":
            stage = "structure"
            try:
                token = json.loads(body)["token"]
                header, payload, signature = token.split(".")
                decode = lambda value: base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
                meta = json.loads(decode(header))
                stage = "algorithm_and_key"
                if meta.get("alg") != "RS512" or meta.get("kid") != "suite-test" or meta.get("typ") != "JWT":
                    raise ValueError("unexpected signing header")
                public = serialization.load_pem_public_key(Path("/public/suite.pem").read_bytes())
                stage = "signature"
                public.verify(decode(signature), (header + "." + payload).encode(), padding.PKCS1v15(), hashes.SHA512())
                claims = json.loads(decode(payload))
                stage = "issuer_and_audience"
                if claims["iss"] != "https://apps.suite.test/" or claims["aud"] != "hawk-suite":
                    raise ValueError("unexpected trust claims")
                stage = "lifetime"
                if claims["exp"] <= time.time() or claims["nbf"] > time.time():
                    raise ValueError("invalid lifetime")
                return self.respond({"claims": claims})
            except Exception:
                return self.respond({"error": "invalid_suite_token", "stage": stage}, 401)
        if self.path == "/issue":
            scenario = json.loads(body)
            code = secrets.token_urlsafe(24)
            CODES[code] = scenario
            return self.respond({"code": code})
        if self.path != "/token":
            return self.respond({"error": "not_found"}, 404)
        COUNTS["token"] += 1
        params = {k: v[0] for k, v in parse_qs(body.decode()).items()}
        if params.get("client_id") != "fixture-client" or params.get("client_secret") != "fixture-secret":
            return self.respond({"error": "invalid_client"}, 401)
        scenario = CODES.pop(params.get("code"), None)
        if scenario is None or params.get("redirect_uri") != scenario["redirect_uri"]:
            return self.respond({"error": "invalid_grant"}, 400)
        if scenario.get("code_challenge"):
            challenge = b64(hashlib.sha256(params.get("code_verifier", "").encode()).digest())
            if challenge != scenario["code_challenge"]:
                return self.respond({"error": "invalid_grant"}, 400)
        now = int(time.time())
        claims = {"iss": ISSUER, "aud": "fixture-client", "sub": "UFIXTURE",
                  "iat": now, "exp": now + 300, "nonce": scenario["nonce"],
                  "email": "fixture@example.invalid", "email_verified": True, "name": "Fixture User",
                  "https://slack.com/team_id": "TAPPROVED", "https://slack.com/user_id": "UFIXTURE"}
        claims.update(scenario.get("claims", {}))
        for field in scenario.get("omit", []):
            claims.pop(field, None)
        userinfo = dict(claims)
        userinfo.update(scenario.get("userinfo", {}))
        access = secrets.token_urlsafe(24)  # Opaque, like Slack's access token.
        TOKENS[access] = userinfo
        token = jwt(claims)
        if scenario.get("tamper"):
            parts = token.split(".")
            parts[1] = b64(json.dumps({**claims, "name": "Tampered"}).encode())
            token = ".".join(parts)
        self.respond({"ok": True, "access_token": access, "token_type": "Bearer", "id_token": token})


if __name__ == "__main__":
    if sys.argv[1:] == ["keygen"]:
        os.umask(0o077)
        Path("/private/suite.pem").write_bytes(KEY.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        Path("/public/suite.pem").write_bytes(KEY.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    elif sys.argv[1:] == ["mint-suite"]:
        # Test-only signer. The HTTP provider never mounts this private volume.
        key = serialization.load_pem_private_key(Path("/private/suite.pem").read_bytes(), password=None)
        claims = json.load(sys.stdin)
        data = ".".join(b64(json.dumps(v).encode()) for v in (
            {"alg": "RS512", "kid": "suite-test", "typ": "JWT"}, claims)).encode()
        sys.stdout.write(data.decode() + "." + b64(key.sign(data, padding.PKCS1v15(), hashes.SHA512())))
    else:
        ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
