"""Receipt-backed dispatch core for an optional private standalone-app proxy.

This module opens no listener, installs no certificate, and changes no app or
box settings. A transport must authenticate each original caller with the
platform and supply an independently authenticated native Computer gateway.
Only an explicit unavailable refusal for a uniquely owned BOX row can admit a
new local claim. Persist the journal across transport/process restarts.
"""
import hashlib
import json
import os
import pathlib
import re
import sqlite3
import stat
import time
import uuid
from typing import NamedTuple


class Result(NamedTuple):
    status: int
    body: object


class ReceiptUnavailable(RuntimeError):
    pass


class ReceiptConflict(RuntimeError):
    pass


DELIVERY = 'GROK_BOT_USER_MESSAGE_DELIVERY_'
STATUS = 'GROK_BOT_SEND_STATUS_'
ACCOUNT_SLOT = 'host'
SEND_FIELDS = frozenset(('agentId', 'messageId', 'text', 'sentAtMs', 'richText',
    'replyToId', 'isFork', 'attachmentPaths', 'attachmentNames', 'traceparent',
    'enterEpochMs', 'composedAtMs', 'source', 'sessionId',
    'onePasswordAppInstalled', 'initiator'))
# The inspected standalone sender uses these wire enums. The retained native
# gateway accepts the corresponding internal strings, not the wire names.
INITIATORS = {'GROK_BOT_USER_MESSAGE_INITIATOR_' + name.upper(): name
              for name in ('user', 'template_import', 'template_share',
                           'card_action', 'plugin_setup', 'teach_recording')}


def canonical_uuid(value):
    return isinstance(value, str) and str(uuid.UUID(value)) == value


def native_send(body, agent_id):
    """Map the inspected mobile JSON subset without rewriting prompt content."""
    if not isinstance(body, dict) or set(body) - SEND_FIELDS:
        raise ValueError('unsupported-send-shape')
    if not canonical_uuid(body.get('messageId')) or not isinstance(body.get('text'), str):
        raise ValueError('invalid-message')
    if body.get('source') != 'GROK_BOT_CLIENT_SURFACE_MOBILE':
        raise ValueError('unsupported-client-surface')
    # The retained native send port has not validated nonempty session routing.
    if body.get('sessionId', '') != '':
        raise ValueError('unsupported-session')
    sent_at = body.get('sentAtMs')
    if not isinstance(sent_at, str) or not re.fullmatch(r'[0-9]{1,16}', sent_at):
        raise ValueError('invalid-send-timestamp')
    if 'isFork' in body and type(body['isFork']) is not bool:
        raise ValueError('invalid-fork')
    request = {'agentId': agent_id, 'clientNonce': body['messageId'],
               'prompt': body['text'], 'source': 'mobile',
               'isFork': body.get('isFork', False),
               'directAddressedAcceptance': True}
    for name in ('attachmentPaths', 'attachmentNames'):
        value = body.get(name, [])
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise ValueError('invalid-attachments')
        request[name] = list(value)
    for name in ('richText', 'replyToId', 'traceparent'):
        if name in body:
            if not isinstance(body[name], str):
                raise ValueError('invalid-string-field')
            request[name] = body[name]
    for name in ('enterEpochMs', 'composedAtMs'):
        if name in body:
            value = body[name]
            if not isinstance(value, str) or not re.fullmatch(r'[0-9]{1,16}', value):
                raise ValueError('invalid-optional-timestamp')
            request[name] = int(value)
    if 'onePasswordAppInstalled' in body:
        if type(body['onePasswordAppInstalled']) is not bool:
            raise ValueError('invalid-mobile-capability')
        request['onePasswordAppInstalled'] = body['onePasswordAppInstalled']
    if 'initiator' in body:
        value = body['initiator']
        if not isinstance(value, str) or value not in INITIATORS:
            raise ValueError('unsupported-message-initiator')
        request['initiator'] = INITIATORS[value]
    return request


def input_digest(request):
    # Match the retained BOX canonicalSendInput. Timestamp/source are not part
    # of the native content digest; neither raw messages nor keys are journaled.
    value = [request['agentId'], request['prompt'], request.get('richText'),
             request.get('replyToId'), request.get('isFork') is True, None,
             request.get('attachmentPaths', []), request.get('attachmentNames', [])]
    raw = json.dumps(value, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


class Receipts:
    """Atomic durable claims shared by workers; records contain metadata only."""
    def __init__(self, path):
        self.path = pathlib.Path(path)
        self.path.parent.mkdir(mode=0o700, parents=False, exist_ok=True)
        parent = self.path.parent.lstat()
        if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.getuid() or parent.st_mode & 0o077:
            raise ValueError('journal-directory-must-be-private-and-owned')
        try:
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(fd)
        connection = self.connect()
        try:
            with connection:
                connection.execute('''CREATE TABLE IF NOT EXISTS claims (
                    nonce TEXT PRIMARY KEY, wire_id TEXT NOT NULL, agent_id TEXT NOT NULL,
                    digest TEXT NOT NULL, claimed_at_ms INTEGER NOT NULL,
                    status TEXT CHECK(status IN ('accepted','pending','rejected')),
                    accepted_at_ms INTEGER, echo_entry_id TEXT, rejection_code TEXT)''')
        finally:
            connection.close()

    def connect(self):
        info = self.path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError('journal-must-be-private-owned-regular-file')
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA synchronous=FULL')
        return connection

    def lookup(self, nonce):
        connection = self.connect()
        try:
            row = connection.execute('SELECT * FROM claims WHERE nonce=?', (nonce,)).fetchone()
            return dict(row) if row is not None else None
        finally:
            connection.close()

    def claim(self, nonce, wire_id, agent_id, digest, now_ms):
        connection = self.connect()
        try:
            with connection:
                result = connection.execute('''INSERT OR IGNORE INTO claims
                    (nonce,wire_id,agent_id,digest,claimed_at_ms) VALUES (?, ?, ?, ?, ?)''',
                                            (nonce, wire_id, agent_id, digest, now_ms))
                return result.rowcount == 1
        finally:
            connection.close()

    def observe(self, claim, record):
        connection = self.connect()
        try:
            with connection:
                connection.execute('''UPDATE claims SET status=?,accepted_at_ms=?,
                    echo_entry_id=?,rejection_code=? WHERE nonce=? AND agent_id=? AND
                    digest=? AND (status IS NULL OR status='pending')''',
                    (record['status'], record['acceptedAtMs'], record.get('echoEntryId'),
                     record.get('rejectionCode'), claim['nonce'], claim['agent_id'], claim['digest']))
        finally:
            connection.close()


def refusal(code='send/agent-unavailable'):
    return Result(200, {'dispatched': False, 'delivery': DELIVERY + 'REFUSED',
                       'refusal': {'failureCode': code, 'message': 'Local delivery unavailable'}})


def uncertain():
    return Result(503, {'code': 'unavailable', 'message': 'Local delivery has no confirmed receipt'})


class Dispatcher:
    """Transport supplies platform(method, body, headers) and gateway(method, body).

    Both return Result. Platform must preserve caller credentials, validate TLS,
    reject redirects, and bound responses. local_work_allowed is an explicit
    owner policy check; a forced local listAgents summary is not that authority.
    """
    def __init__(self, transport, receipts, local_work_allowed, now_ms=None):
        self.transport = transport
        self.receipts = receipts
        self.local_work_allowed = local_work_allowed
        self.now_ms = now_ms or (lambda: int(time.time() * 1000))

    def target(self, wire_id, headers):
        if not isinstance(wire_id, str) or not re.fullmatch(r'[1-9][0-9]*', wire_id):
            return None
        result = self.transport.platform('ListGrokBotAgents', {}, headers)
        if result.status != 200 or not isinstance(result.body, dict):
            return None
        rows = result.body.get('agents')
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            return None
        matches = [row for row in rows if row.get('id') == wire_id]
        if len(matches) != 1:
            return None
        row = matches[0]
        agent_id = row.get('agentId')
        try:
            valid = canonical_uuid(agent_id)
        except ValueError:
            valid = False
        if not valid or row.get('legacyAgentId') != agent_id or \
                row.get('viewerIsOwner') is not True or row.get('harness') != 'box' or \
                sum(candidate.get('agentId') == agent_id for candidate in rows) != 1:
            return None
        return agent_id

    def native_available(self, agent_id):
        if self.local_work_allowed() is not True:
            return False
        result = self.transport.gateway('listAgents', {})
        if result.status != 200 or not isinstance(result.body, list) or \
                any(not isinstance(row, dict) for row in result.body):
            return False
        return sum(isinstance(row, dict) and row.get('id') == agent_id
                   for row in result.body) == 1

    def lookup(self, claim):
        persisted = self.receipts.lookup(claim['nonce'])
        if persisted is not None and persisted.get('status') in ('accepted', 'rejected'):
            return self.snapshot_record(persisted, claim)
        result = self.transport.gateway('promptAcceptanceStatus', {
            'accountSlot': ACCOUNT_SLOT, 'clientNonce': claim['nonce'],
            'agentId': claim['agent_id']})
        if result.status != 200 or not isinstance(result.body, dict):
            raise ReceiptUnavailable('native-acceptance-status-unavailable')
        if result.body.get('outcome') in ('not-found', 'unknown-durability'):
            if set(result.body) != {'outcome'}:
                raise ReceiptUnavailable('contradictory-native-acceptance-status')
            return None
        record = result.body.get('record')
        if result.body.get('outcome') != 'found' or not isinstance(record, dict):
            raise ReceiptUnavailable('invalid-native-acceptance-status')
        self.checked_record(record, claim)
        self.receipts.observe(claim, record)
        persisted = self.receipts.lookup(claim['nonce'])
        if persisted is not None and persisted.get('status') in ('accepted', 'rejected'):
            return self.snapshot_record(persisted, claim)
        return record

    def snapshot_record(self, persisted, claim):
        record = {'accountSlot': ACCOUNT_SLOT, 'clientNonce': persisted['nonce'],
            'agentId': persisted['agent_id'], 'inputDigest': persisted['digest'],
            'status': persisted['status'], 'acceptedAtMs': persisted['accepted_at_ms'],
            'echoEntryId': persisted['echo_entry_id'], 'rejectionCode': persisted['rejection_code']}
        return self.checked_record(record, claim)

    def checked_record(self, record, claim):
        if any(record.get(field) != value for field, value in (
                ('accountSlot', ACCOUNT_SLOT), ('clientNonce', claim['nonce']),
                ('agentId', claim['agent_id']), ('inputDigest', claim['digest']))):
            raise ReceiptConflict('native-receipt-identity-or-digest-mismatch')
        if record.get('status') not in ('accepted', 'pending', 'rejected') or \
                type(record.get('acceptedAtMs')) is not int or record['acceptedAtMs'] < 0:
            raise ReceiptUnavailable('invalid-native-receipt')
        if record.get('echoEntryId') is not None and not isinstance(record['echoEntryId'], str):
            raise ReceiptUnavailable('invalid-native-receipt')
        if record.get('rejectionCode') is not None and not isinstance(record['rejectionCode'], str):
            raise ReceiptUnavailable('invalid-native-receipt')
        return record

    def send_result(self, claim, duplicate=False):
        try:
            record = self.lookup(claim)
        except ReceiptConflict:
            return refusal('send/nonce-digest-mismatch')
        except (ReceiptUnavailable, OSError, sqlite3.Error):
            return uncertain()
        if record is None or record['status'] == 'pending':
            return uncertain()
        if record['status'] == 'rejected':
            return refusal(record.get('rejectionCode') or 'send/agent-unavailable')
        return Result(200, {'dispatched': True,
                           'delivery': DELIVERY + ('DUPLICATE' if duplicate else 'ACCEPTED_BOX')})

    def send(self, body, headers):
        """A timeout or semantic quota/no-access refusal never triggers fallback."""
        wire_id = body.get('agentId') if isinstance(body, dict) else None
        nonce = body.get('messageId') if isinstance(body, dict) else None
        claim = self.receipts.lookup(nonce) if isinstance(nonce, str) else None
        if claim is not None:
            agent_id = self.target(wire_id, headers)
            if agent_id != claim['agent_id'] or wire_id != claim['wire_id']:
                return refusal()
            try:
                request = native_send(body, agent_id)
                digest = input_digest(request)
            except (ValueError, UnicodeError):
                return refusal('send/nonce-digest-mismatch')
            if digest != claim['digest']:
                return refusal('send/nonce-digest-mismatch')
            # Never call upstream send or native send for a claimed nonce,
            # including a restart/crash between claim and receipt creation.
            return self.send_result(claim, duplicate=True)

        original = self.transport.platform('SendGrokBotUserMessage', body, headers)
        response = original.body
        if original.status != 200 or not isinstance(response, dict) or \
                response.get('delivery') != DELIVERY + 'REFUSED' or \
                response.get('dispatched', False) is not False or response.get('workflowId') or \
                set(response) - {'delivery', 'dispatched', 'mode', 'workflowId', 'refusal'} or \
                not isinstance(response.get('refusal'), dict) or \
                response['refusal'].get('failureCode') != 'send/agent-unavailable':
            return original
        agent_id = self.target(wire_id, headers)
        if agent_id is None:
            return original
        try:
            request = native_send(body, agent_id)
            digest = input_digest(request)
        except (ValueError, UnicodeError):
            return original
        if not self.native_available(agent_id):
            return original
        if not self.receipts.claim(nonce, wire_id, agent_id, digest, self.now_ms()):
            claimed = self.receipts.lookup(nonce)
            if claimed is None or claimed['agent_id'] != agent_id or claimed['digest'] != digest:
                return refusal('send/nonce-digest-mismatch')
            return self.send_result(claimed, duplicate=True)
        claim = self.receipts.lookup(nonce)
        # A matching native receipt can predate this bridge (e.g. a native
        # desktop send). It is still a duplicate; don't dispatch it again.
        try:
            existing = self.lookup(claim)
        except ReceiptConflict:
            return refusal('send/nonce-digest-mismatch')
        except (ReceiptUnavailable, OSError, sqlite3.Error):
            return uncertain()
        if existing is not None:
            return self.send_result(claim, duplicate=True)
        try:
            self.transport.gateway('sendPrompt', request)
        except (OSError, TimeoutError):
            pass  # Inspect receipt once; uncertain transport is never replayed.
        return self.send_result(claim)

    def status(self, body, headers):
        nonce = body.get('messageId') if isinstance(body, dict) else None
        claim = self.receipts.lookup(nonce) if isinstance(nonce, str) else None
        if claim is None:
            return self.transport.platform('GetGrokBotSendStatus', body, headers)
        wire_id = body.get('agentId')
        if set(body) - {'agentId', 'messageId', 'sessionId'} or body.get('sessionId', '') != '' or \
                wire_id != claim['wire_id'] or self.target(wire_id, headers) != claim['agent_id']:
            return Result(200, {'status': STATUS + 'UNKNOWN_DURABILITY'})
        try:
            record = self.lookup(claim)
        except (ReceiptConflict, ReceiptUnavailable, OSError, sqlite3.Error):
            record = None
        if record is None:
            return Result(200, {'status': STATUS + 'UNKNOWN_DURABILITY'})
        response = {'status': STATUS + record['status'].upper(),
                    'acceptedAtMs': str(record['acceptedAtMs'])}
        for local_name, wire_name in (('echoEntryId', 'echoEntryId'), ('rejectionCode', 'rejectionCode')):
            if record.get(local_name) is not None:
                response[wire_name] = record[local_name]
        return Result(200, response)
