import json
import sys

services = json.load(open(sys.argv[1]))['services']
mod = services['hawk-mod']['environment']
bot = services['hawk-bot']['environment']
assert mod['SLACK_SIGNING_SECRET'] == 'mod-secret'
assert bot['SLACK_SIGNING_SECRET'] == 'bot-secret'
assert mod['TOKEN_ENCRYPTION_KEY'] == 'mod-key'
assert bot['TOKEN_ENCRYPTION_KEY'] == 'bot-key'
assert mod['GOOGLE_SERVICE_ACCOUNT_KEY_BASE64'] == 'mod-google'
assert bot['GOOGLE_SERVICE_ACCOUNT_KEY_BASE64'] == 'bot-google'
assert mod['REPORT_TIME'] == '09:15'
assert mod['MANAGED_USERGROUPS'] == 'S12345'
assert mod['LIFECYCLE_SHEET_ID'] == 'ci-sheet'
assert 'LIFECYCLE_SHEET_ID' not in bot
assert not set(mod).intersection({'DIGEST_CRON', 'QUARTERLY_CRON', 'STUDENT_USERGROUP', 'ADULT_USERGROUP'})
assert services['hawk-shop']['environment']['APP_URL'] == 'https://shop.ci.example.org'
assert services['hawk-shop']['environment']['ONSHAPE_WEBHOOK_SECRET'] == 'ci-webhook-secret'
assert services['hawk-shop']['environment']['ONSHAPE_WEBHOOK_BASE_URL'] == 'https://shop.ci.example.org'
assert services['hawk-shop']['environment']['SLACK_BOT_TOKEN'] == 'shop-slack'
assert 'SLACK_BOT_TOKEN' not in mod and 'SLACK_BOT_TOKEN' not in bot
# Three Slack apps, three signing secrets: Shop's must never be hawk-mod's.
assert services['hawk-shop']['environment']['SLACK_SIGNING_SECRET'] == 'shop-secret'
assert len({
    services['hawk-shop']['environment']['SLACK_SIGNING_SECRET'],
    mod['SLACK_SIGNING_SECRET'],
    bot['SLACK_SIGNING_SECRET'],
}) == 3
for app in ('hawk-shop', 'hawk-mod', 'hawk-bot', 'hawk-eye'):
    assert '@sha256:' in services[app]['image']

eye = services['hawk-eye']
assert eye['environment'] == {
    'APP_URL': 'https://scout.ci.example.org', 'HOST': '0.0.0.0',
    'PORT': '3000', 'DATA_DIR': '/data', 'LOG_LEVEL': 'info',
    'TBA_API_KEY': 'ci-tba', 'MATCH13_API_KEY': 'ci-match13', 'MATCH13_ACCOUNT': 'ci-account',
}
assert 'ports' not in eye
assert eye['volumes'][0]['source'] == 'hawk_eye_data'
assert eye['volumes'][0]['target'] == '/data'

for service, config in services.items():
    if service != "hawk-eye":
        assert not {"TBA_API_KEY", "MATCH13_API_KEY", "MATCH13_ACCOUNT"}.intersection(config.get("environment", {}))

# Each application can reach Caddy, but no sibling application shares its bridge.
for app in ('shop', 'mod', 'bot', 'eye'):
    service = services['hawk-' + app]
    assert set(service['networks']) == {app}
    assert not service.get('ports')
assert set(services['caddy']['networks']) == {'shop', 'mod', 'bot', 'eye'}
