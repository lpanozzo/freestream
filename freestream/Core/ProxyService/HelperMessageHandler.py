﻿#Embedded file name: freestream\Core\ProxyService\HelperMessageHandler.pyo
import sys, os
import binascii
from threading import Lock
from time import sleep
from freestream.Core.TorrentDef import *
from freestream.Core.Session import *
from freestream.Core.simpledefs import *
from freestream.Core.DownloadConfig import DownloadStartupConfig
from freestream.Core.Utilities.utilities import show_permid_short
from freestream.Core.BitTornado.bencode import bencode, bdecode
from freestream.Core.BitTornado.BT1.MessageID import *
from freestream.Core.CacheDB.CacheDBHandler import PeerDBHandler, TorrentDBHandler
from freestream.Core.Overlay.OverlayThreadingBridge import OverlayThreadingBridge
DEBUG = False

class HelperMessageHandler:

    def __init__(self):
        self.metadata_queue = {}
        self.metadata_queue_lock = Lock()
        self.overlay_bridge = OverlayThreadingBridge.getInstance()
        self.received_challenges = {}

    def register(self, session, metadata_handler, helpdir, dlconfig):
        self.session = session
        self.helpdir = helpdir
        self.dlconfig = dlconfig
        self.metadata_handler = metadata_handler
        self.torrent_db = TorrentDBHandler.getInstance()

    def handleMessage(self, permid, selversion, message):
        t = message[0]
        if DEBUG:
            print >> sys.stderr, 'helper: received the message', getMessageName(t), 'from', show_permid_short(permid)
        session_config = self.session.get_current_startup_config_copy()
        if session_config.get_proxyservice_status() == PROXYSERVICE_OFF:
            if DEBUG:
                print >> sys.stderr, 'helper: ProxyService not active, ignoring message'
            return
        if t == ASK_FOR_HELP:
            return self.got_ask_for_help(permid, message, selversion)
        if t == STOP_HELPING:
            return self.got_stop_helping(permid, message, selversion)
        if t == REQUEST_PIECES:
            return self.got_request_pieces(permid, message, selversion)

    def got_ask_for_help(self, permid, message, selversion):
        try:
            infohash = message[1:21]
            challenge = bdecode(message[21:])
        except:
            if DEBUG:
                print >> sys.stderr, 'helper: got_ask_for_help: bad data in ask_for_help'
            return False

        if len(infohash) != 20:
            if DEBUG:
                print >> sys.stderr, 'helper: got_ask_for_help: bad infohash in ask_for_help'
            return False
        if DEBUG:
            print >> sys.stderr, 'helper: got_ask_for_help: received a help request from', show_permid_short(permid), 'with challenge', challenge
        self.received_challenges[permid] = challenge
        helper_obj = self.session.lm.get_coopdl_role_object(infohash, COOPDL_ROLE_HELPER)
        if helper_obj is None:
            if DEBUG:
                print >> sys.stderr, 'helper: got_ask_for_help: There is no current download for this infohash. A new download must be started.'
            self.start_helper_download(permid, infohash, selversion)
            return
        network_got_ask_for_help_lambda = lambda : self.network_got_ask_for_help(permid, infohash)
        self.session.lm.rawserver.add_task(network_got_ask_for_help_lambda, 0)
        return True

    def network_got_ask_for_help(self, permid, infohash):
        helper_obj = self.session.lm.get_coopdl_role_object(infohash, COOPDL_ROLE_HELPER)
        if helper_obj is None:
            if DEBUG:
                print >> sys.stderr, 'helper: network_got_ask_for_help: There is no current download for this infohash. Try again later...'
            return
        if not helper_obj.is_coordinator(permid):
            if DEBUG:
                print >> sys.stderr, 'helper: network_got_ask_for_help: The node asking for help is not the current coordinator'
        challenge = self.received_challenges[permid]
        helper_obj.got_ask_for_help(permid, infohash, challenge)
        helper_obj.notify()

    def start_helper_download(self, permid, infohash, selversion):
        torrent_data = self.find_torrent(infohash)
        if torrent_data:
            self.new_download(infohash, torrent_data, permid)
        else:
            self.get_torrent_metadata(permid, infohash, selversion)

    def new_download(self, infohash, torrent_data, permid):
        basename = binascii.hexlify(infohash) + '.torrent'
        torrentfilename = os.path.join(self.helpdir, basename)
        tfile = open(torrentfilename, 'wb')
        tfile.write(torrent_data)
        tfile.close()
        if DEBUG:
            print >> sys.stderr, 'helper: new_download: Got metadata required for helping', show_permid_short(permid)
            print >> sys.stderr, 'helper: new_download: torrent: ', torrentfilename
        tdef = TorrentDef.load(torrentfilename)
        if self.dlconfig is None:
            dscfg = DownloadStartupConfig()
        else:
            dscfg = DownloadStartupConfig(self.dlconfig)
        dscfg.set_coopdl_coordinator_permid(permid)
        dscfg.set_dest_dir(self.helpdir)
        dscfg.set_proxy_mode(PROXY_MODE_OFF)
        if DEBUG:
            print >> sys.stderr, 'helper: new_download: Starting a new download'
        d = self.session.start_download(tdef, dscfg)
        d.set_state_callback(self.state_callback, getpeerlist=False)
        network_got_ask_for_help_lambda = lambda : self.network_got_ask_for_help(permid, infohash)
        self.session.lm.rawserver.add_task(network_got_ask_for_help_lambda, 0)

    def state_callback(self, ds):
        d = ds.get_download()
        print >> sys.stderr, '%s %s %5.2f%% %s up %8.2fKB/s down %8.2fKB/s' % (d.get_def().get_name(),
         dlstatus_strings[ds.get_status()],
         ds.get_progress() * 100,
         ds.get_error(),
         ds.get_current_speed(UPLOAD),
         ds.get_current_speed(DOWNLOAD))
        return (1.0, False)

    def get_torrent_metadata(self, permid, infohash, selversion):
        if DEBUG:
            print >> sys.stderr, 'helper: get_torrent_metadata: Asking coordinator for the .torrent'
        self.metadata_queue_lock.acquire()
        try:
            if not self.metadata_queue.has_key(infohash):
                self.metadata_queue[infohash] = []
            self.metadata_queue[infohash].append(permid)
        finally:
            self.metadata_queue_lock.release()

        self.metadata_handler.send_metadata_request(permid, infohash, selversion, caller='dlhelp')

    def metadatahandler_received_torrent(self, infohash, torrent_data):
        if DEBUG:
            print >> sys.stderr, 'helper: metadatahandler_received_torrent: the .torrent is in.'
        self.metadata_queue_lock.acquire()
        try:
            if not self.metadata_queue.has_key(infohash) or not self.metadata_queue[infohash]:
                if DEBUG:
                    print >> sys.stderr, 'helper: metadatahandler_received_torrent: a .torrent was received that we are not waiting for.'
                return
            infohash_queue = self.metadata_queue[infohash]
            del self.metadata_queue[infohash]
            for permid in infohash_queue:
                self.new_download(infohash, torrent_data, permid)

        finally:
            self.metadata_queue_lock.release()

    def find_torrent(self, infohash):
        torrent = self.torrent_db.getTorrent(infohash)
        if torrent is None:
            if DEBUG:
                print >> sys.stderr, 'helper: find_torrent: The .torrent file is not in the local cache'
            return
        if 'torrent_dir' in torrent:
            fn = torrent['torrent_dir']
            if os.path.isfile(fn):
                f = open(fn, 'rb')
                data = f.read()
                f.close()
                return data
            else:
                if DEBUG:
                    print >> sys.stderr, 'helper: find_torrent: The .torrent file path does not exist or the path is not for a file'
                return
        else:
            if DEBUG:
                print >> sys.stderr, 'helper: find_torrent: The torrent dictionary does not contain a torrent_dir field'
            return

    def got_stop_helping(self, permid, message, selversion):
        try:
            infohash = message[1:]
        except:
            if DEBUG:
                print >> sys.stderr, 'helper: got_stop_helping: bad data in STOP_HELPING'
            return False

        if len(infohash) != 20:
            if DEBUG:
                print >> sys.stderr, 'helper: got_stop_helping: bad infohash in STOP_HELPING'
            return False
        network_got_stop_helping_lambda = lambda : self.network_got_stop_helping(permid, infohash, selversion)
        self.session.lm.rawserver.add_task(network_got_stop_helping_lambda, 0)
        return False

    def network_got_stop_helping(self, permid, infohash, selversion):
        helper_obj = self.session.lm.get_coopdl_role_object(infohash, COOPDL_ROLE_HELPER)
        if helper_obj is None:
            if DEBUG:
                print >> sys.stderr, 'helper: network_got_stop_helping: There is no helper object associated with this infohash'
            return
        if not helper_obj.is_coordinator(permid):
            if DEBUG:
                print >> sys.stderr, 'helper: network_got_stop_helping: The node asking for help is not the current coordinator'
            return
        dlist = self.session.get_downloads()
        for d in dlist:
            if d.get_def().get_infohash() == infohash:
                self.session.remove_download(d)
                break

    def got_request_pieces(self, permid, message, selversion):
        try:
            infohash = message[1:21]
            pieces = bdecode(message[21:])
        except:
            print >> sys.stderr, 'helper: got_request_pieces: bad data in REQUEST_PIECES'
            return False

        network_got_request_pieces_lambda = lambda : self.network_got_request_pieces(permid, message, selversion, infohash, pieces)
        self.session.lm.rawserver.add_task(network_got_request_pieces_lambda, 0)
        return True

    def network_got_request_pieces(self, permid, message, selversion, infohash, pieces):
        helper_obj = self.session.lm.get_coopdl_role_object(infohash, COOPDL_ROLE_HELPER)
        if helper_obj is None:
            if DEBUG:
                print >> sys.stderr, 'helper: network_got_request_pieces: There is no helper object associated with this infohash'
            return
        if not helper_obj.is_coordinator(permid):
            if DEBUG:
                print >> sys.stderr, 'helper: network_got_request_pieces: The node asking for help is not the current coordinator'
            return
        helper_obj.got_request_pieces(permid, pieces)
        helper_obj.notify()
