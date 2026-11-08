#!/usr/bin/env python3
"""Exercise the production template with disposable backends and synthetic JWTs.

Requires Docker and Python cryptography. No real credentials or production DNS.
Only TLS termination and the provider metadata URL differ from production.
"""
import base64
import http.client
import json
from pathlib import Path
import subprocess
import tempfile
import time
import uuid
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

ROOT = Path(__file__).resolve().parents[1]


def docker(*args):
    return subprocess.check_output(['docker', *args], stderr=subprocess.PIPE, text=True).strip()


def main():
    name = 'hawk-routing-' + uuid.uuid4().hex[:10]
    containers = []
    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    def token(**overrides):
        claims = dict(sub='slack:TAPPROVED:UTEST', iss='https://apps.suite.test/',
                      aud='hawk-suite', roles=['suite/member'], iat=int(time.time()),
                      nbf=int(time.time())-60, exp=int(time.time())+120, jti=uuid.uuid4().hex)
        claims.update(overrides)
        enc = lambda obj: base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b'=')
        data = enc(dict(alg='RS512', kid='suite-production')) + b'.' + enc(claims)
        return (data + b'.' + base64.urlsafe_b64encode(key.sign(data, padding.PKCS1v15(), hashes.SHA512())).rstrip(b'=')).decode()
    with tempfile.TemporaryDirectory(prefix='hawk-routing-') as temp:
        directory = Path(temp)
        (directory/'private.pem').write_bytes(key.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        (directory/'public.pem').write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo))
        config = (ROOT/'config/authentication/Caddyfile.template').read_text()
        for a, b in {'__DOMAIN__':'suite.test', '__CLIENT_ID__':'123.456',
                     '__CLIENT_SECRET__':'a'*32, '__TEAM_ID__':'TAPPROVED'}.items():
            config = config.replace(a, b)
        config = config.replace('admin localhost:2019', 'admin localhost:2019\n auto_https off')
        config = config.replace('metadata_url https://slack.com/.well-known/openid-configuration',
                                'metadata_url http://provider:8080/.well-known/openid-configuration')
        for host in ('apps','shop','mod','bot','scout'):
            config = config.replace(host+'.suite.test {', 'http://'+host+'.suite.test:8080 {')
        (directory/'Caddyfile').write_text(config)
        (directory/'Caddyfile.maintenance').write_text((ROOT/'config/authentication/Caddyfile.maintenance').read_text().replace('__DOMAIN__','suite.test'))
        for template in ('login.template', 'portal.template'):
            (directory/template).write_bytes((ROOT/'config/authentication'/template).read_bytes())
        (directory/'backend.py').write_text('''from http.server import BaseHTTPRequestHandler, HTTPServer
import json
class Handler(BaseHTTPRequestHandler):
 def log_message(self, *args): pass
 def do_GET(self):
  self.send_response(200); self.end_headers()
  self.wfile.write(json.dumps(dict(self.headers)).encode())
 do_POST = do_GET
 do_HEAD = do_GET
HTTPServer(('0.0.0.0',3000), Handler).serve_forever()
''')
        docker('network', 'create', name)
        try:
            for suffix, args in [
                ('provider', ['--network-alias','provider','hawk-auth-oidc-fixture:local']),
                ('backend', sum((['--network-alias', app] for app in ('hawk-eye','hawk-shop','hawk-mod','hawk-bot')), []) +
                 ['-v',str(directory)+':/fixture:ro','python:3.14-alpine','python','/fixture/backend.py']),
                ('gateway', ['-p','127.0.0.1::8080','-v',str(directory)+':/etc/hawk-auth:ro',
                 '-v',str(ROOT/'config/authentication/assets')+':/srv/auth-assets:ro',
                 'hawk-auth-candidate:local','caddy','run','--config','/etc/hawk-auth/Caddyfile','--adapter','caddyfile'])]:
                cid = name+'-'+suffix
                containers.append(cid)
                docker('run','-d','--name',cid,'--network',name,*args)
            port = int(docker('port', name+'-gateway','8080/tcp').rsplit(':',1)[1])
            def request(host, path='/', method='GET', headers=None):
                connection = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
                try:
                    connection.request(method, path, headers={'Host':host+'.suite.test:8080', **(headers or {})})
                    response = connection.getresponse()
                    return response.status, response.getheader('Location',''), response.read()
                finally:
                    connection.close()
            for attempt in range(40):
                if docker('inspect','--format','{{.State.Running}}',name+'-gateway') != 'true':
                    raise RuntimeError('gateway exited; validate the synthetic template')
                try:
                    if request('apps','/auth/login')[0] == 200: break
                except (OSError, http.client.HTTPException): pass
                time.sleep(.5)
            else:
                raise RuntimeError('gateway did not become ready')
            count = 0
            def check(condition, label):
                nonlocal count
                if not condition: raise AssertionError(label)
                count += 1
                print('PASS '+label)
            docker('exec',name+'-gateway','caddy','validate','--config','/etc/hawk-auth/Caddyfile.maintenance','--adapter','caddyfile')
            check(True,'maintenance configuration validates')
            valid = token()
            for host in ('mod','bot','scout'):
                for value in (None, 'invalid', token(exp=int(time.time())-120), token(aud='wrong'), token(roles=['admin'])):
                    status, location, body = request(host, headers={} if value is None else {'Cookie':'AUTHP_ACCESS_TOKEN='+value})
                    check(status in (302,303,307,401,403), host+' rejects missing/invalid claims')
                    if status < 400: check(location.startswith('https://apps.suite.test/'),host+' uses central login')
                status, _, body = request(host, headers={'Cookie':'AUTHP_ACCESS_TOKEN='+valid+'; app_session=preserved', 'X-Hawk-Identity':'forged'})
                check(status == 200,host+' accepts shared signed cookie')
                parsed = json.loads(body)
                check('X-Hawk-Identity' not in parsed,host+' strips forged identity')
                if host != 'scout': check('app_session=preserved' in parsed.get('Cookie',''),host+' preserves app login cookie')
            for host in ('mod','bot'):
                for path, method in [('/slack/events','POST'),('/slack/oauth_redirect','GET'),('/auth/slack/callback','GET'),('/health','GET')]:
                    check(request(host,path,method)[0] == 200,host+' forwards exact '+method+' '+path)
                for path, method in [('/slack/events','GET'),('/slack/events/extra','POST'),('/slack/install','GET'),('/auth/slack/callback','POST')]:
                    check(request(host,path,method)[0] != 200,host+' protects '+method+' '+path)
            check(request('shop','/auth/onshape/callback')[0] == 200,'Onshape GET callback reaches app')
            for path in ('/', '/onshape_connector', '/auth/onshape', '/api/health'):
                check(request('shop',path)[0] == 200,'Shop delegates '+path+' to existing app authentication')
            status, _, body = request('shop', headers={'Cookie':'app_session=preserved', 'X-Hawk-Identity':'forged'})
            check(status == 200 and 'app_session=preserved' in json.loads(body).get('Cookie',''), 'Shop app cookie preserved without gateway login')
            check('X-Hawk-Identity' not in json.loads(body),'Shop strips forged identity')
            check('https://shop.__DOMAIN__/' in (ROOT/'config/authentication/portal.template').read_text(), 'Shop card retained in native authenticated portal')
            check(request('scout','/api/write','POST',{'Cookie':'AUTHP_ACCESS_TOKEN='+valid})[0] == 503,'Eye writes remain unavailable')
            check(b'Test environment' not in request('apps','/auth/login')[2],'production login branding')
            print(f'{count} routing checks passed; no real app sessions or browser iframe behavior tested.')
        finally:
            for cid in reversed(containers):
                subprocess.run(['docker','rm','-f','-v',cid],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            docker('network','rm',name)


if __name__ == '__main__':
    main()
