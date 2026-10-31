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
for app in ('hawk-shop', 'hawk-mod', 'hawk-bot'):
    assert '@sha256:' in services[app]['image']
