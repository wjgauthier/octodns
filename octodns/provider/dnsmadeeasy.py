#
#
#

from __future__ import absolute_import, division, print_function, \
    unicode_literals

from collections import defaultdict
from requests import Session
from time import strftime, gmtime
import hashlib
import hmac
import logging

from ..record import Record
from .base import BaseProvider


class DnsMadeEasyClientException(Exception):
    pass


class DnsMadeEasyClientBadRequest(DnsMadeEasyClientException):

    @classmethod
    def build_message(self, errors):
        return '\n  - {}'.format('\n  - '.join(errors))

    def __init__(self, resp):
        errors = resp.json()['error']
        super(DnsMadeEasyClientBadRequest, self).__init__(
            self.build_message(errors))


class DnsMadeEasyClientUnauthorized(DnsMadeEasyClientException):

    def __init__(self):
        super(DnsMadeEasyClientUnauthorized, self).__init__('Unauthorized')


class DnsMadeEasyClientForbidden(DnsMadeEasyClientException):

    def __init__(self):
        super(DnsMadeEasyClientNotFound, self).__init__('Forbidden')


class DnsMadeEasyClientNotFound(DnsMadeEasyClientException):

    def __init__(self):
        super(DnsMadeEasyClientNotFound, self).__init__('Not Found')


class DnsMadeEasyClient(object):
    BASE = 'https://api.dnsmadeeasy.com/V2.0/dns/managed'

    def __init__(self, api_key, secret_key):
        self.api_key = api_key
        self.secret_key = secret_key
        self._sess = Session()
        self._domains = None

    def _current_time(self):
        return strftime("%a, %d %b %Y %H:%M:%S +0000", gmtime())

    def _hmac_hash(self, now):
        return hmac.new(self.secret_key.encode(), now.encode(),
                        hashlib.sha1).hexdigest()

    def _request(self, method, path, params=None, data=None):
        now = self._current_time()
        hmac_hash = self._hmac_hash(now)

        headers = {
            'x-dnsme-apiKey': self.api_key,
            'x-dnsme-hmac': hmac_hash,
            'x-dnsme-requestDate': now
        }
        self._sess.headers.update(headers)

        url = '{}{}'.format(self.BASE, path)
        resp = self._sess.request(method, url, params=params, json=data)
        if resp.status_code == 400:
            raise DnsMadeEasyClientBadRequest(resp)
        if resp.status_code == 401:
            raise DnsMadeEasyClientUnauthorized()
        if resp.status_code == 403:
            raise DnsMadeEasyClientForbidden()
        if resp.status_code == 404:
            raise DnsMadeEasyClientNotFound()
        resp.raise_for_status()
        return resp

    @property
    def domains(self):
        if self._domains is None:
            zones = []

            # has pages in resp, do we need paging?
            resp = self._request('GET', '/').json()
            zones += resp['data']

            self._domains = {'{}.'.format(z['name']): z['id'] for z in zones}

        return self._domains

    def domain(self, name):
        path = '/id/{}'.format(name)
        return self._request('GET', path).json()

    def domain_create(self, name):
        self._request('POST', '/', data={'name': name})

    def records(self, zone_name):
        zone_id = self.domains.get(zone_name, False)
        path = '/{}/records'.format(zone_id)
        ret = []

        # has pages in resp, do we need paging?
        resp = self._request('GET', path).json()
        ret += resp['data']

        # change relative values to absolute
        for record in ret:
            value = record['value']
            if record['type'] in ['CNAME', 'MX', 'NS', 'SRV']:
                if value == '':
                    record['value'] = zone_name
                elif not value.endswith('.'):
                    record['value'] = '{}.{}'.format(value, zone_name)

        return ret

    def record_create(self, zone_name, params):
        zone_id = self.domains.get(zone_name, False)
        path = '/{}/records'.format(zone_id)

        self._request('POST', path, data=params)

    def record_delete(self, zone_name, record_id):
        zone_id = self.domains.get(zone_name, False)
        path = '/{}/records/{}'.format(zone_id, record_id)
        self._request('DELETE', path)


class DnsMadeEasyProvider(BaseProvider):
    '''
    DNSMadeEasy DNS provider using v2.0 API

    dnsmadeeasy:
        class: octodns.provider.dnsmadeeasy.DnsMadeEasyProvider
        api_key: env/DNSMADEEASY_API_KEY
        secret_key: env/DNSMADEEASY_SECRET_KEY
    '''
    SUPPORTS_GEO = False
    SUPPORTS = set(('A', 'AAAA', 'CAA', 'CNAME', 'MX',
                    'NS', 'PTR', 'SPF', 'SRV', 'TXT'))

    def __init__(self, id, api_key, secret_key, *args, **kwargs):
        self.log = logging.getLogger('DnsMadeEasyProvider[{}]'.format(id))
        self.log.debug('__init__: id=%s, api_key=***, secret_key=***', id)
        super(DnsMadeEasyProvider, self).__init__(id, *args, **kwargs)
        self._client = DnsMadeEasyClient(api_key, secret_key)

        self._zone_records = {}

    def _data_for_multiple(self, _type, records):
        return {
            'ttl': records[0]['ttl'],
            'type': _type,
            'values': [r['value'] for r in records]
        }

    _data_for_A = _data_for_multiple
    _data_for_AAAA = _data_for_multiple
    _data_for_NS = _data_for_multiple

    def _data_for_CAA(self, _type, records):
        values = []
        for record in records:
            values.append({
                'flags': record['issuerCritical'],
                'tag': record['caaType'],
                'value': record['value'][1:-1]
            })
        return {
            'ttl': records[0]['ttl'],
            'type': _type,
            'values': values
        }

    def _data_for_TXT(self, _type, records):
        values = [value['value'].replace(';', '\;') for value in records]
        return {
            'ttl': records[0]['ttl'],
            'type': _type,
            'values': values
        }

    _data_for_SPF = _data_for_TXT

    def _data_for_MX(self, _type, records):
        values = []
        for record in records:
            values.append({
                'preference': record['mxLevel'],
                'exchange': record['value']
            })
        return {
            'ttl': records[0]['ttl'],
            'type': _type,
            'values': values
        }

    def _data_for_single(self, _type, records):
        record = records[0]
        return {
            'ttl': record['ttl'],
            'type': _type,
            'value': record['value']
        }

    _data_for_CNAME = _data_for_single
    _data_for_PTR = _data_for_single

    def _data_for_SRV(self, _type, records):
        values = []
        for record in records:
            values.append({
                'port': record['port'],
                'priority': record['priority'],
                'target': record['value'],
                'weight': record['weight']
            })
        return {
            'type': _type,
            'ttl': records[0]['ttl'],
            'values': values
        }

    def zone_records(self, zone):
        if zone.name not in self._zone_records:
            try:
                self._zone_records[zone.name] = \
                    self._client.records(zone.name)
            except DnsMadeEasyClientNotFound:
                return []

        return self._zone_records[zone.name]

    def populate(self, zone, target=False, lenient=False):
        self.log.debug('populate: name=%s, target=%s, lenient=%s', zone.name,
                       target, lenient)

        values = defaultdict(lambda: defaultdict(list))
        for record in self.zone_records(zone):
            _type = record['type']
            values[record['name']][record['type']].append(record)

        before = len(zone.records)
        for name, types in values.items():
            for _type, records in types.items():
                data_for = getattr(self, '_data_for_{}'.format(_type))
                record = Record.new(zone, name, data_for(_type, records),
                                    source=self, lenient=lenient)
                zone.add_record(record)

        self.log.info('populate:   found %s records',
                      len(zone.records) - before)

    def _params_for_multiple(self, record):
        for value in record.values:
            yield {
                'value': value,
                'name': record.name,
                'ttl': record.ttl,
                'type': record._type
            }

    _params_for_A = _params_for_multiple
    _params_for_AAAA = _params_for_multiple

    # An A record with this name must exist in this domain for
    # this NS record to be valid. Need to handle checking if
    # there is an A record before creating NS
    _params_for_NS = _params_for_multiple

    def _params_for_single(self, record):
        yield {
            'value': record.value,
            'name': record.name,
            'ttl': record.ttl,
            'type': record._type
        }

    _params_for_CNAME = _params_for_single
    _params_for_PTR = _params_for_single

    def _params_for_MX(self, record):
        for value in record.values:
            yield {
                'value': value.exchange,
                'name': record.name,
                'mxLevel': value.preference,
                'ttl': record.ttl,
                'type': record._type
            }

    def _params_for_SRV(self, record):
        for value in record.values:
            yield {
                'value': value.target,
                'name': record.name,
                'port': value.port,
                'priority': value.priority,
                'ttl': record.ttl,
                'type': record._type,
                'weight': value.weight
            }

    def _params_for_TXT(self, record):
        # DNSMadeEasy does not want values escaped
        for value in record.chunked_values:
            yield {
                'value': value.replace('\;', ';'),
                'name': record.name,
                'ttl': record.ttl,
                'type': record._type
            }

    _params_for_SPF = _params_for_TXT

    def _params_for_CAA(self, record):
        for value in record.values:
            yield {
                'value': value.value,
                'issuerCritical': value.flags,
                'name': record.name,
                'caaType': value.tag,
                'ttl': record.ttl,
                'type': record._type
            }

    def _apply_Create(self, change):
        new = change.new
        params_for = getattr(self, '_params_for_{}'.format(new._type))
        for params in params_for(new):
            self._client.record_create(new.zone.name, params)

    def _apply_Update(self, change):
        self._apply_Delete(change)
        self._apply_Create(change)

    def _apply_Delete(self, change):
        existing = change.existing
        zone = existing.zone
        for record in self.zone_records(zone):
            if existing.name == record['name'] and \
               existing._type == record['type']:
                    self._client.record_delete(zone.name, record['id'])

    def _apply(self, plan):
        desired = plan.desired
        changes = plan.changes
        self.log.debug('_apply: zone=%s, len(changes)=%d', desired.name,
                       len(changes))

        domain_name = desired.name[:-1]
        try:
            self._client.domain(domain_name)
        except DnsMadeEasyClientNotFound:
            self.log.debug('_apply:   no matching zone, creating domain')
            self._client.domain_create(domain_name)

        for change in changes:
            class_name = change.__class__.__name__
            getattr(self, '_apply_{}'.format(class_name))(change)

        # Clear out the cache if any
        self._zone_records.pop(desired.name, None)
