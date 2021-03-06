﻿#Embedded file name: freestream\Core\Overlay\permid.pyo
import sys
from base64 import encodestring
from copy import deepcopy
import traceback, os
from freestream.Core.BitTornado.bencode import bencode, bdecode
from freestream.Core.BitTornado.BT1.MessageID import *
DEBUG = False
num_random_bits = 8192
STATE_INITIAL = 0
STATE_AWAIT_R1 = 1
STATE_AWAIT_R2 = 2
STATE_AUTHENTICATED = 3
STATE_FAILED = 4

def init():
    Rand.rand_seed(os.urandom(num_random_bits / 8))


def exit():
    pass

def save_keypair(keypair, keypairfilename):
    keypair.save_key(keypairfilename, None)


def save_pub_key(keypair, pubkeyfilename):
    keypair.save_pub_key(pubkeyfilename)


def permid_for_user(permid):
    return encodestring(permid).replace('\n', '')


def sign_data(plaintext, ec_keypair):
    digest = sha(plaintext).digest()
    return ec_keypair.sign_dsa_asn1(digest)


def verify_data_pubkeyobj(plaintext, pubkey, blob):
    digest = sha(plaintext).digest()
    return pubkey.verify_dsa_asn1(digest, blob)


def generate_challenge():
    randomB = Rand.rand_bytes(num_random_bits / 8)
    return [randomB, bencode(randomB)]


def check_challenge(cdata):
    try:
        randomB = bdecode(cdata)
    except:
        return None

    if len(randomB) != num_random_bits / 8:
        return None
    else:
        return randomB


def generate_response1(randomB, peeridB, keypairA):
    randomA = Rand.rand_bytes(num_random_bits / 8)
    response1 = {}
    response1['certA'] = str(keypairA.pub().get_der())
    response1['rA'] = randomA
    response1['B'] = peeridB
    response1['SA'] = sign_response(randomA, randomB, peeridB, keypairA)
    return [randomA, bencode(response1)]


def check_response1(rdata1, randomB, peeridB):
    try:
        response1 = bdecode(rdata1)
    except:
        return [None, None]

    if response1['B'] != peeridB:
        return [None, None]
    pubA_der = response1['certA']
    pubA = EC.pub_key_from_der(pubA_der)
    sigA = response1['SA']
    randomA = response1['rA']
    if verify_response(randomA, randomB, peeridB, pubA, sigA):
        return [randomA, pubA]
    else:
        return [None, None]


def generate_response2(randomA, peeridA, randomB, keypairB):
    response2 = {}
    response2['certB'] = str(keypairB.pub().get_der())
    response2['A'] = peeridA
    response2['SB'] = sign_response(randomB, randomA, peeridA, keypairB)
    return bencode(response2)


def check_response2(rdata2, randomA, peeridA, randomB, peeridB):
    try:
        response2 = bdecode(rdata2)
    except:
        return None

    if response2['A'] != peeridA:
        return None
    pubB_der = response2['certB']
    pubB = EC.pub_key_from_der(pubB_der)
    sigB = response2['SB']
    if verify_response(randomB, randomA, peeridA, pubB, sigB):
        return pubB
    else:
        return None


def sign_response(randomA, randomB, peeridB, keypairA):
    list = [randomA, randomB, peeridB]
    blist = bencode(list)
    digest = sha(blist).digest()
    blob = keypairA.sign_dsa_asn1(digest)
    return blob


def verify_response(randomA, randomB, peeridB, pubA, sigA):
    list = [randomA, randomB, peeridB]
    blist = bencode(list)
    digest = sha(blist).digest()
    return pubA.verify_dsa_asn1(digest, sigA)


def create_torrent_signature(metainfo, keypairfilename):
    keypair = EC.load_key(keypairfilename)
    bmetainfo = bencode(metainfo)
    digester = sha(bmetainfo[:])
    digest = digester.digest()
    sigstr = keypair.sign_dsa_asn1(digest)
    metainfo['signature'] = sigstr
    metainfo['signer'] = str(keypair.pub().get_der())


def verify_torrent_signature(metainfo):
    r = deepcopy(metainfo)
    signature = r['signature']
    signer = r['signer']
    del r['signature']
    del r['signer']
    bmetainfo = bencode(r)
    digester = sha(bmetainfo[:])
    digest = digester.digest()
    return do_verify_torrent_signature(digest, signature, signer)


def do_verify_torrent_signature(digest, sigstr, permid):
    if permid is None:
        return False
    try:
        ecpub = EC.pub_key_from_der(permid)
        if ecpub is None:
            return False
        intret = ecpub.verify_dsa_asn1(digest, sigstr)
        return intret == 1
    except Exception as e:
        print >> sys.stderr, 'permid: Exception in verify_torrent_signature:', str(e)
        return False


class PermIDException(Exception):
    pass


class ChallengeResponse:

    def __init__(self, my_keypair, my_id, secure_overlay):
        self.my_keypair = my_keypair
        self.permid = str(my_keypair.pub().get_der())
        self.my_id = my_id
        self.secure_overlay = secure_overlay
        self.my_random = None
        self.peer_id = None
        self.peer_random = None
        self.peer_pub = None
        self.state = STATE_INITIAL
        dummy_random, cdata = generate_challenge()
        dummy_random1, rdata1 = generate_response1(dummy_random, my_id, self.my_keypair)
        rdata2 = generate_response2(dummy_random, my_id, dummy_random, self.my_keypair)
        self.minchal = 1 + len(cdata)
        self.minr1 = 1 + len(rdata1) - 1
        self.minr2 = 1 + len(rdata2) - 1

    def starting_party(self, locally_initiated):
        if self.state == STATE_INITIAL and locally_initiated:
            self.state = STATE_AWAIT_R1
            return True
        else:
            return False

    def create_challenge(self):
        self.my_random, cdata = generate_challenge()
        return cdata

    def got_challenge_event(self, cdata, peer_id):
        if self.state != STATE_INITIAL:
            self.state = STATE_FAILED
            if DEBUG:
                print >> sys.stderr, 'Got unexpected CHALLENGE message'
            raise PermIDException
        self.peer_random = check_challenge(cdata)
        if self.peer_random is None:
            self.state = STATE_FAILED
            if DEBUG:
                print >> sys.stderr, 'Got bad CHALLENGE message'
            raise PermIDException
        self.peer_id = peer_id
        self.my_random, rdata1 = generate_response1(self.peer_random, peer_id, self.my_keypair)
        self.state = STATE_AWAIT_R2
        return rdata1

    def got_response1_event(self, rdata1, peer_id):
        if self.state != STATE_AWAIT_R1:
            self.state = STATE_FAILED
            if DEBUG:
                print >> sys.stderr, 'Got unexpected RESPONSE1 message'
            raise PermIDException
        randomA, peer_pub = check_response1(rdata1, self.my_random, self.my_id)
        if randomA is None or peer_pub is None:
            self.state = STATE_FAILED
            if DEBUG:
                print >> sys.stderr, 'Got bad RESPONSE1 message'
            raise PermIDException
        peer_permid = str(peer_pub.get_der())
        if self.permid == peer_permid:
            self.state = STATE_FAILED
            if DEBUG:
                print >> sys.stderr, 'Got the same Permid as myself'
            raise PermIDException
        self.peer_id = peer_id
        self.peer_random = randomA
        self.peer_pub = peer_pub
        self.set_peer_authenticated()
        rdata2 = generate_response2(self.peer_random, self.peer_id, self.my_random, self.my_keypair)
        return rdata2

    def got_response2_event(self, rdata2):
        if self.state != STATE_AWAIT_R2:
            self.state = STATE_FAILED
            if DEBUG:
                print >> sys.stderr, 'Got unexpected RESPONSE2 message'
            raise PermIDException
        self.peer_pub = check_response2(rdata2, self.my_random, self.my_id, self.peer_random, self.peer_id)
        if self.peer_pub is None:
            self.state = STATE_FAILED
            if DEBUG:
                print >> sys.stderr, 'Got bad RESPONSE2 message, authentication failed.'
            raise PermIDException
        else:
            peer_permid = str(self.peer_pub.get_der())
            if self.permid == peer_permid:
                self.state = STATE_FAILED
                if DEBUG:
                    print >> sys.stderr, 'Got the same Permid as myself'
                raise PermIDException
            else:
                self.set_peer_authenticated()

    def set_peer_authenticated(self):
        if DEBUG:
            print >> sys.stderr, 'permid: Challenge response succesful!'
        self.state = STATE_AUTHENTICATED

    def get_peer_authenticated(self):
        return self.state == STATE_AUTHENTICATED

    def get_peer_permid(self):
        if self.state != STATE_AUTHENTICATED:
            raise PermIDException
        return self.peer_pub.get_der()

    def get_auth_peer_id(self):
        if self.state != STATE_AUTHENTICATED:
            raise PermIDException
        return self.peer_id

    def get_challenge_minlen(self):
        return self.minchal

    def get_response1_minlen(self):
        return self.minr1

    def get_response2_minlen(self):
        return self.minr2

    def start_cr(self, conn):
        if not self.get_peer_authenticated() and self.starting_party(conn.is_locally_initiated()):
            self.send_challenge(conn)

    def send_challenge(self, conn):
        cdata = self.create_challenge()
        conn.send_message(CHALLENGE + str(cdata))

    def got_challenge(self, cdata, conn):
        rdata1 = self.got_challenge_event(cdata, conn.get_unauth_peer_id())
        conn.send_message(RESPONSE1 + rdata1)

    def got_response1(self, rdata1, conn):
        rdata2 = self.got_response1_event(rdata1, conn.get_unauth_peer_id())
        conn.send_message(RESPONSE2 + rdata2)
        self.secure_overlay.got_auth_connection(conn, self.get_peer_permid(), self.get_auth_peer_id())

    def got_response2(self, rdata2, conn):
        self.got_response2_event(rdata2)
        if self.get_peer_authenticated():
            self.secure_overlay.got_auth_connection(conn, self.get_peer_permid(), self.get_auth_peer_id())

    def got_message(self, conn, message):
        if not conn:
            return False
        t = message[0]
        if message[1:]:
            msg = message[1:]
        if t == CHALLENGE:
            if len(message) < self.get_challenge_minlen():
                if DEBUG:
                    print >> sys.stderr, 'permid: Close on bad CHALLENGE: msg len', len(message)
                self.state = STATE_FAILED
                return False
            try:
                self.got_challenge(msg, conn)
            except Exception as e:
                if DEBUG:
                    print >> sys.stderr, 'permid: Close on bad CHALLENGE: exception', str(e)
                    traceback.print_exc()
                return False

        elif t == RESPONSE1:
            if len(message) < self.get_response1_minlen():
                if DEBUG:
                    print >> sys.stderr, 'permid: Close on bad RESPONSE1: msg len', len(message)
                self.state = STATE_FAILED
                return False
            try:
                self.got_response1(msg, conn)
            except Exception as e:
                if DEBUG:
                    print >> sys.stderr, 'permid: Close on bad RESPONSE1: exception', str(e)
                    traceback.print_exc()
                return False

        elif t == RESPONSE2:
            if len(message) < self.get_response2_minlen():
                if DEBUG:
                    print >> sys.stderr, 'permid: Close on bad RESPONSE2: msg len', len(message)
                self.state = STATE_FAILED
                return False
            try:
                self.got_response2(msg, conn)
            except Exception as e:
                if DEBUG:
                    print >> sys.stderr, 'permid: Close on bad RESPONSE2: exception', str(e)
                    traceback.print_exc()
                return False

        else:
            return False
        return True


if __name__ == '__main__':
    init()
