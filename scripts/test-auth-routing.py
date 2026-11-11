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
                      slack_team_id='TAPPROVED', slack_user_id='UTEST', aud='hawk-suite', roles=['suite/member'], iat=int(time.time()),
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
 do_DELETE = do_GET
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
                    if isinstance(headers, list):
                        connection.putrequest(method, path, skip_host=True)
                        connection.putheader('Host', host+'.suite.test:8080')
                        for key, value in headers:
                            connection.putheader(key, value)
                        connection.endheaders()
                    else:
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
            signed = {'Cookie':'AUTHP_ACCESS_TOKEN='+valid}
            for host in ('mod','bot','scout','shop'):
                for value in (None, 'invalid', token(exp=int(time.time())-120), token(aud='wrong'), token(roles=['admin'])):
                    status, location, body = request(host, headers={} if value is None else {'Cookie':'AUTHP_ACCESS_TOKEN='+value})
                    check(status in (302,303,307,401,403), host+' rejects missing/invalid claims')
                    if status < 400: check(location.startswith('https://apps.suite.test/'),host+' uses central login')
                status, _, body = request(host, headers={'Cookie':'AUTHP_ACCESS_TOKEN='+valid+'; app_session=preserved', 'X-Hawk-Identity':'forged'})
                check(status == 200,host+' accepts shared signed cookie')
                parsed = json.loads(body)
                check('X-Hawk-Identity' not in parsed,host+' strips forged identity')
                check('app_session=preserved' in parsed.get('Cookie',''),host+' preserves app login cookie')
                check('AUTHP_ACCESS_TOKEN='+valid in parsed.get('Cookie',''),host+' forwards signed suite session')
            status, _, body = request('shop', '/api/onshape/webhook?secret=fixture', 'POST',
                                      {'X-Hawk-Slack-Team': 'forged', 'X-Hawk-Slack-User': 'forged',
                                       'X-Token-Subject': 'forged', 'X-Token-User-Roles': 'suite/member'})
            check(status == 200, 'Shop forwards anonymous Onshape callback to app secret verification')
            forwarded = {key.lower(): value for key, value in json.loads(body).items()}
            check(not any(key in forwarded for key in ('x-hawk-slack-team', 'x-hawk-slack-user',
                                                       'x-token-subject', 'x-token-user-roles')),
                  'Shop callback strips forged Suite identity')
            for method, path in (('GET', '/api/onshape/webhook'), ('POST', '/api/onshape/webhook/'),
                                 ('POST', '/api/onshape/webhook/extra')):
                check(request('shop', path, method)[0] in (302,303,307,401,403),
                      'Shop gates non-callback request '+method+' '+path)
            identity = {'X-Token-Subject': 'slack:TAPPROVED:UTEST',
                        'X-Hawk-Slack-Team': 'TAPPROVED', 'X-Hawk-Slack-User': 'UTEST',
                        'X-Token-User-Roles': 'suite/member'}
            forged = {key: 'forged' for key in [*identity, 'X-Token-User-Name', 'X-Token-User-Email']}
            for host in ('mod', 'bot', 'scout', 'shop'):
                check(request(host, headers=forged)[0] != 200, host+' rejects forged identity without session')
                status, _, body = request(host, headers={**forged, 'Cookie': 'AUTHP_ACCESS_TOKEN='+valid})
                received = {k.lower(): v for k, v in json.loads(body).items()}
                check(status == 200 and all(received.get(k.lower()) == v for k,v in identity.items()), host+' injects verified identity')
                check('x-token-user-name' not in received and 'x-token-user-email' not in received, host+' clears absent optional claims')
                status, _, body = request(host, headers={'Cookie':'AUTHP_ACCESS_TOKEN='+token(name='Fixture User', email='fixture@example.org')})
                received = {k.lower(): v for k,v in json.loads(body).items()}
                check(received.get('x-token-user-name') == 'Fixture User' and received.get('x-token-user-email') == 'fixture@example.org', host+' forwards profile')
            status, _, body = request('bot', headers=[('Cookie','AUTHP_ACCESS_TOKEN='+valid),
                ('X-Hawk-Slack-User','forged'), ('x-hawk-slack-user','other'),
                ('X-Token-User-Email','forged'), ('x-token-user-email','other')])
            received = {k.lower(): v for k,v in json.loads(body).items()}
            check(status == 200 and received.get('x-hawk-slack-user') == 'UTEST'
                  and 'x-token-user-email' not in received, 'duplicate mixed-case headers cannot survive')
            for host, path in [('shop','/api/health'), ('shop','/health'), ('scout','/health'), ('bot','/health'), ('mod','/slack/oauth_redirect')]:
                status, _, body = request(host, path, headers=forged)
                received = {k.lower(): v for k,v in json.loads(body).items()}
                check(status == 200 and not any(k.lower() in received for k in forged), host+' clears identity on bypass')
            for host in ('mod','bot'):
                for path, method in [('/slack/events','POST'),('/slack/oauth_redirect','GET'),('/health','GET')]:
                    check(request(host,path,method)[0] == 200,host+' forwards exact '+method+' '+path)
                for path, method in [('/slack/events','GET'),('/slack/events/extra','POST'),('/slack/install','GET'),('/auth/slack/callback','GET')]:
                    check(request(host,path,method)[0] != 200,host+' protects '+method+' '+path)
            for path in ('/', '/onshape_connector', '/signin', '/api/kanban/cards', '/api/files/x.png'):
                check(request('shop',path)[0] in (302,303,307,401,403), 'Shop gates anonymous '+path)
                check(request('shop',path,headers=signed)[0] == 200, 'Shop forwards signed '+path)
            check(request('shop','/api/kanban/cards','POST')[0] in (302,303,307,401,403), 'Shop gates anonymous POST')
            for path in ('/api/health', '/health'):
                for method in ('GET', 'HEAD'):
                    check(request('shop',path,method)[0] == 200, 'Shop keeps '+method+' '+path+' public')
                check(request('shop',path,'POST')[0] in (302,303,307,401,403), 'Shop gates POST '+path)
            for path in ('/api/kanban/cards', '/api/mfg/parts/actions', '/api/files/x.png', '/auth/embed/session'):
                for method in ('GET', 'POST'):
                    status, _, body = request('shop', path, method, headers={**forged, 'Cookie': '__Host-hawk-shop-embedded='+valid})
                    received = {k.lower(): v for k, v in json.loads(body).items()} if status == 200 else {}
                    check(status == 200 and all(received.get(k.lower()) == v for k, v in identity.items()),
                          'Shop validates embedded session '+method+' '+path)
                    for value in ('forged', token(exp=int(time.time())-120), token(aud='wrong'), token(iss='https://evil.test/'), token(roles=['admin'])):
                        check(request('shop', path, method, headers={**forged, 'Cookie': '__Host-hawk-shop-embedded='+value})[0] in (401,403),
                              'Shop rejects invalid embedded session '+method+' '+path)
            for value in ('invalid', token(exp=int(time.time())-120)):
                status, _, body = request('shop','/onshape_connector?documentId=fixture', headers={**forged, 'Cookie': '__Host-hawk-shop-embedded='+value, 'Sec-Fetch-Dest':'iframe'})
                received = {k.lower(): v for k, v in json.loads(body).items()} if status == 200 else {}
                check(status == 200 and not any(k.lower() in received for k in forged), 'Expired embedded session can reconnect without identity')
            for path in ('/auth/embed/authorize?challenge=fixture', '/auth/embed/authorize/?challenge=fixture'):
                check(request('shop',path,headers={'Cookie':'__Host-hawk-shop-embedded='+valid})[0] != 200,
                      'Embedded session cannot mint new connection codes '+path)
            for path in ('/_astro/app.js', '/brand/2713-red.svg', '/favicon.svg'):
                status, _, body = request('shop',path,headers=forged)
                received = {k.lower(): v for k, v in json.loads(body).items()}
                check(status == 200 and not any(k.lower() in received for k in forged), 'Shop connection assets clear identity '+path)
            status, _, body = request('shop','/auth/embed/exchange','POST',forged)
            check(status == 200 and not any(k.lower() in {h.lower() for h in json.loads(body)} for k in forged), 'Code exchange reaches Shop without identity')
            for path, method in [('/auth/embed/exchange','GET'),('/auth/embed/exchange/','POST'),('/auth/embed/exchange/extra','POST')]:
                check(request('shop',path,method)[0] != 200, 'Exchange bypass is exact '+method+' '+path)
            for path in ('/onshape_connector?documentId=fixture', '/onshape_connector/'):
                status, _, body = request('shop',path,headers={**forged, 'Authorization': 'Bearer forged', 'Sec-Fetch-Dest':'iframe'})
                received = {k.lower(): v for k,v in json.loads(body).items()} if status == 200 else {}
                check(status == 200 and 'authorization' not in received and not any(k.lower() in received for k in forged),
                      'Shop iframe entry clears identity and reaches only the continuation '+path)
            for path, method in [('/api/kanban/cards','GET'), ('/', 'GET'), ('/onshape_connector/extra','GET'), ('/onshape_connector','POST')]:
                check(request('shop',path,method,{'Sec-Fetch-Dest':'iframe'})[0] in (302,303,307,401,403),
                      'Shop iframe cannot bypass authorization '+method+' '+path)
            status, _, body = request('shop', headers={**signed, 'X-Hawk-Identity':'forged'})
            check(status == 200 and 'X-Hawk-Identity' not in json.loads(body), 'Shop strips forged identity')
            check('https://shop.__DOMAIN__/' in (ROOT/'config/authentication/portal.template').read_text(), 'Shop card retained in native authenticated portal')
            check(request('scout','/api/write','POST',{'Cookie':'AUTHP_ACCESS_TOKEN='+valid})[0] == 503,'Eye writes remain unavailable')
            for path in ('/events/2026casj/picklist/', '/events/2026casj/picklist.json?since=3'):
                check(request('scout',path,headers=signed)[0] == 200,'Eye forwards signed GET '+path)
                check(request('scout',path)[0] in (302,303,307,401,403),'Eye gates anonymous GET '+path)
            for action in ('editPickList','sharePickList','postPickListMessage'):
                for suffix in ('', '/'):
                    path = '/_actions/'+action+suffix
                    status, _, body = request('scout',path,'POST',signed)
                    check(status == 200 and 'AUTHP_ACCESS_TOKEN='+valid in json.loads(body).get('Cookie',''),
                          'Eye forwards signed POST '+path)
                    check(request('scout',path,'POST')[0] in (302,303,307,401,403),
                          'Eye gates anonymous POST '+path)
                    for method in ('GET','HEAD','PUT','PATCH','DELETE','OPTIONS'):
                        check(request('scout',path,method,signed)[0] == 503,
                              'Eye keeps '+method+' '+path+' blocked')
                    for query in ('?_action=x', '?since=3', '?x=', '?x=1&x=2'):
                        check(request('scout',path+query,'POST',signed)[0] == 503,
                              'Eye keeps POST '+path+query+' blocked')
                    check(request('scout',path+'extra','POST',signed)[0] == 503,
                          'Eye keeps POST '+path+'extra blocked')
            # Partners authenticate inside Eye using a revocable share code,
            # not a Suite membership cookie. Exercise every browser request:
            # invite, assets, form actions, polling, and JSON chat.
            partner_cookie = {'Cookie': 'hawk-eye-pick-share=ABCDEFGH'}
            for path in ('/picklist', '/picklist/', '/picklist/?code=ABCD-EFGH',
                         '/picklist/state.json?since=3', '/_astro/PickListBoard.js',
                         '/_astro/styles.css', '/_astro/open-sans.woff2',
                         '/brand/2713-red.svg', '/favicon.svg'):
                for method in ('GET', 'HEAD'):
                    check(request('scout',path,method)[0] == 200,
                          'Eye forwards anonymous '+method+' '+path)
            partner_posts = ['/picklist/?_action=joinPickList',
                             '/picklist/?_action=leavePickList',
                             '/picklist?_action=joinPickList']
            for action in ('joinPickList', 'leavePickList', 'postSharedPickListMessage'):
                partner_posts.extend('/_actions/'+action+suffix for suffix in ('', '/'))
            for path in partner_posts:
                status, _, body = request('scout',path,'POST',{
                    **forged, **partner_cookie, 'Authorization': 'Bearer forged'})
                received = {k.lower(): v for k,v in json.loads(body).items()} if status == 200 else {}
                check(status == 200, 'Eye forwards partner POST '+path)
                check(received.get('cookie') == partner_cookie['Cookie'],
                      'Eye preserves partner cookie '+path)
                check('authorization' not in received and 'x-hawk-identity' not in received
                      and not any(k.lower() in received for k in forged),
                      'Eye strips caller identity on partner POST '+path)
            for path in ('/picklist/', '/picklist/state.json', '/_astro/PickListBoard.js'):
                check(request('scout',path,headers={'Cookie': 'AUTHP_ACCESS_TOKEN=invalid'})[0] == 200,
                      'Eye does not gate partner assets or pages on stale Suite login '+path)
            for path in ('/picklist/?_action=editPickList',
                         '/picklist/?_action=joinPickList&_action=editPickList',
                         '/picklist/?_action=leavePickList&_action=joinPickList',
                         '/picklist/?_action=joinPickList&extra=1',
                         '/picklist/?_action=joinPickListExtra',
                         '/picklist/state.json', '/_astro/PickListBoard.js',
                         '/_actions/postSharedPickListMessage?extra=1',
                         '/_actions/postSharedPickListMessage/extra'):
                check(request('scout',path,'POST')[0] == 503,
                      'Eye keeps unlisted partner POST '+path+' blocked')
            for path in ('/picklist/', '/picklist/state.json', '/_actions/joinPickList',
                         '/_actions/leavePickList', '/_actions/postSharedPickListMessage'):
                for method in ('PUT', 'PATCH', 'DELETE', 'OPTIONS'):
                    check(request('scout',path,method)[0] == 503,
                          'Eye keeps '+method+' '+path+' blocked')
            for action in ('connectScouting','postPartnerMessage','editPickListExtra'):
                for suffix in ('', '/'):
                    path = '/_actions/'+action+suffix
                    check(request('scout',path,'POST',signed)[0] == 503,
                          'Eye keeps POST '+path+' blocked')
            for path in ('/picklist/extra', '/picklist/state.json/extra', '/events/2026casj/picklist/extra'):
                check(request('scout',path,headers=signed)[0] == 503,'Eye keeps GET '+path+' blocked')
            # MCP OAuth (docs/hawk-eye-mcp-oauth.md). Public routes carry no
            # Suite cookie or identity; /mcp keeps only the client's bearer.
            def headers_of(body):
                return {k.lower(): v for k, v in json.loads(body).items()}
            hostile = {**forged, 'Cookie': 'AUTHP_ACCESS_TOKEN='+valid, 'Authorization': 'Bearer hek_at_client'}
            for method in ('POST', 'GET', 'DELETE'):
                status, _, body = request('scout','/mcp',method,hostile)
                received = headers_of(body) if status == 200 else {}
                check(status == 200 and received.get('authorization') == 'Bearer hek_at_client',
                      'Eye forwards '+method+' /mcp with the client bearer token')
                check('cookie' not in received and 'x-hawk-identity' not in received
                      and not any(k.lower() in received for k in forged),
                      'Eye /mcp drops cookies and caller identity on '+method)
            for path, method in [('/mcp','PUT'), ('/mcp','PATCH'), ('/mcp?x=1','POST'),
                                 ('/mcp/','POST'), ('/mcp/extra','POST'), ('/mcpx','POST')]:
                check(request('scout',path,method,hostile)[0] == 503,
                      'Eye keeps '+method+' '+path+' blocked')
            oauth_public = [(p, m) for p in ('/.well-known/oauth-protected-resource',
                                             '/.well-known/oauth-protected-resource/mcp',
                                             '/.well-known/oauth-authorization-server')
                            for m in ('GET', 'HEAD')]
            oauth_public += [(p, 'POST') for p in ('/oauth/register', '/oauth/token', '/oauth/revoke')]
            for path, method in oauth_public:
                status, _, body = request('scout',path,method,hostile)
                check(status == 200, 'Eye forwards anonymous '+method+' '+path)
                if method != 'HEAD':
                    received = headers_of(body)
                    check('authorization' not in received and 'cookie' not in received
                          and 'x-hawk-identity' not in received
                          and not any(k.lower() in received for k in forged),
                          'Eye OAuth endpoint gets no credentials or identity '+path)
            for path, method in [('/oauth/token','GET'), ('/oauth/token?x=1','POST'),
                                 ('/oauth/token/','POST'), ('/oauth/register','PUT'),
                                 ('/oauth/revoke/extra','POST'),
                                 ('/.well-known/oauth-authorization-server','POST'),
                                 ('/.well-known/oauth-protected-resource/other','GET'),
                                 ('/.well-known/openid-configuration','GET')]:
                check(request('scout',path,method,hostile)[0] == 503,
                      'Eye keeps '+method+' '+path+' blocked')
            for path in ('/oauth/authorize?client_id=x&state=y', '/oauth/connections'):
                check(request('scout',path)[0] in (302,303,307,401,403),
                      'Eye gates anonymous '+path)
                status, _, body = request('scout',path,headers=signed)
                received = headers_of(body) if status == 200 else {}
                check(status == 200 and received.get('x-hawk-slack-user') == 'UTEST'
                      and received.get('x-token-subject') == 'slack:TAPPROVED:UTEST'
                      and 'authorization' not in received,
                      'Eye consent gets verified identity '+path)
                # An MCP token is never a Suite session, even beside a valid cookie.
                for extra in ({'Authorization': 'Bearer hek_at_client'},
                              {**signed, 'Authorization': 'Bearer hek_at_client'}):
                    check(request('scout',path,headers=extra)[0] in (302,303,307,401,403),
                          'Eye consent refuses a bearer token as a session '+path)
            for path in ('/oauth/authorize?_action=approveMcpClient', '/oauth/connections?_action=disconnectMcpClient'):
                check(request('scout',path,'POST')[0] in (302,303,307,401,403),
                      'Eye gates anonymous POST '+path)
                check(request('scout',path,'POST',signed)[0] == 200,
                      'Eye forwards signed POST '+path)
            for path, method in [('/oauth/authorize','PUT'), ('/oauth/authorize/extra','GET'),
                                 ('/oauth/connections/revoke','POST')]:
                check(request('scout',path,method,signed)[0] == 503,
                      'Eye keeps '+method+' '+path+' blocked')
            check(b'Test environment' not in request('apps','/auth/login')[2],'production login branding')
            print(f'{count} routing checks passed; no real app sessions or browser iframe behavior tested.')
        finally:
            for cid in reversed(containers):
                subprocess.run(['docker','rm','-f','-v',cid],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            docker('network','rm',name)


if __name__ == '__main__':
    main()
