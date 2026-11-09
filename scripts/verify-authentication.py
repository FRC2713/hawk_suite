#!/usr/bin/env python3
"""Read-only public denial checks. Never sends credentials or follows redirects."""
import http.client
import sys
from urllib.parse import urlsplit


def verify(domain):
    for app in ('shop', 'mod', 'bot', 'scout'):
        for headers in ({}, {'Cookie': 'AUTHP_ACCESS_TOKEN=invalid',
                             'X-Hawk-Identity': 'admin'}):
            connection = http.client.HTTPSConnection(f'{app}.{domain}', timeout=15)
            try:
                connection.request('GET', '/', headers=headers)
                response = connection.getresponse()
                location = urlsplit(response.getheader('Location', ''))
                if response.status not in (302, 303, 307, 401, 403):
                    raise ValueError(f'{app}: anonymous or forged access was not denied')
                if response.status < 400 and (location.scheme != 'https' or
                        location.netloc != f'apps.{domain}' or not location.path.startswith('/auth')):
                    raise ValueError(f'{app}: unexpected authentication redirect')
                response.read()
            finally:
                connection.close()
        print(f'  ok   {app}: anonymous and forged access denied')


if __name__ == '__main__':
    verify(sys.argv[1])
