# Written by Jie Yang
# see LICENSE.txt for license information

from threading import Event,currentThread

from sha import sha
from time import time
from struct import pack

from BitTornado.__init__ import createPeerID
from BitTornado.bencode import bencode, bdecode
from BitTornado.BT1.MessageID import *

#from Tribler.BuddyCast.buddycast import BuddyCast
#from Tribler.toofastbt.bthelper import Helper

from Tribler.__init__ import GLOBAL
from permid import ChallengeResponse
from MetadataHandler import MetadataHandler
from OverlayEncrypter import OverlayEncoder
from OverlayConnecter import OverlayConnecter

protocol_name = 'BitTorrent protocol'    #TODO: 'BitTorrent+ protocol'
overlay_infohash = '\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00'

from __init__ import CurrentVersion, LowestVersion, SupportedVersions

DEBUG = True

TEST = False

def show(s):
    text = []
    for i in xrange(len(s)): 
        text.append(ord(s[i]))
    return text

def tobinary(i):
    return (chr(i >> 24) + chr((i >> 16) & 0xFF) + 
        chr((i >> 8) & 0xFF) + chr(i & 0xFF))
        
def toint(s):
    return long(b2a_hex(s), 16)        
        
def wrap_message(message_id, payload=None):
    if payload is not None:
        ben_payload = bencode(payload)
        message = tobinary(1+len(ben_payload)) + message_id + ben_payload
    else:
        message = tobinary(1) + message_id
    return message
        

class OverlaySwarm:
    # Code to make this a singleton
    __single = None
    infohash = overlay_infohash

    def __init__(self):
        if OverlaySwarm.__single:
            raise RuntimeError, "OverlaySwarm is singleton"
        OverlaySwarm.__single = self 
        self.myid = createPeerID()
        self.myid = self.myid[:16] + pack('H', LowestVersion) + pack('H', CurrentVersion)
        self.protocol = protocol_name
        self.crs = {}
        self.registered = False
                    
    def getInstance(*args, **kw):
        if OverlaySwarm.__single is None:
            OverlaySwarm(*args, **kw)
        return OverlaySwarm.__single
    getInstance = staticmethod(getInstance)

    def register(self, listen_port, secure_overlay, multihandler, config, errorfunc):
        # Register overlay_infohash as known swarm with MultiHandler
        
        if self.registered:
            return
        
        self.myid = self.myid[:14] + pack('H', listen_port) + self.myid[16:]
        self.secure_overlay = secure_overlay
        self.config = config
        self.doneflag = Event()
        self.rawserver = multihandler.newRawServer(self.infohash, 
                                              self.doneflag,
                                              self.protocol)
        self.errorfunc = errorfunc
        
        # Create Connecter and Encoder for the swarm. TODO: ratelimiter
        self.connecter = OverlayConnecter(self, self.config)
        self.encoder = OverlayEncoder(self, self.connecter, self.rawserver, 
            self.myid, self.config['max_message_length'], self.rawserver.add_task, 
            self.config['keepalive_interval'], self.infohash, 
            lambda x: None, self.config)
        self.registered = True

    def start_listening(self):
        self.rawserver.start_listening(self.encoder)
            
    def connectPeer(self, dns):
        """ Connect to Overlay Socket given peer's ip and port """
        
        if DEBUG:
            print "overlay: Start overlay swarm connection to", dns
        if TEST:
            class Conn:
                def __init__(self, dns):
                    self.dns = dns
                    self.permid = 'permid1'
                    self.closed = False
                def close(self):
                    print "connection closed"
                    self.closed = True
                    
            conn = Conn(dns)
            from time import sleep
            print "    waiting connection ..."
            sleep(3)
            self.permidSocketMade(conn)
        else:
            self.encoder.start_connection(dns, 0)
            
    def sendMessage(self, connection, message):
        if DEBUG:
            print "overlay: send message", getMessageName(message[0]), "to", connection
        connection.send_message(message)

    def connectionMade(self, connection):
        """ phase 1: Connecter.Connection is created but permid has not been verified """

        if DEBUG:
            print "overlay: Bare connection",connection.get_myip(),connection.get_myport(),"to",connection.get_ip(),connection.get_port(),"reported by thread",currentThread().getName()
        

        def c(conn = connection):
            """ Start permid exchange and challenge/response validation """
            if not connection or self.crs.has_key(connection) and self.crs[connection]:
                return    # don't start c/r if connection is invalid or permid was exchanged
            cr = ChallengeResponse(self.myid, self, self.errorfunc)
            self.crs[connection] = cr
            cr.start_cr(connection)
        self.rawserver.add_task(c, 0)
            
    def permidSocketMade(self, connection):    # Connecter.Connection. 
        """ phase 2: notify that the connection has been made """
        
        if self.crs.has_key(connection):
            self.crs.pop(connection)
        def notify(connection=connection):
            self.secure_overlay.connectionMade(connection)
        self.rawserver.add_task(notify, 0)
                
    def connectionLost(self,connection):
        if DEBUG:
            print "overlay: connectionLost: connection is",connection
        if connection.permid is None:
            # No permid, so it was never reported to the SecureOverlay
            return
        def notify(connection=connection):
            self.secure_overlay.connectionLost(connection)
        self.rawserver.add_task(notify, 0)

    def got_message(self, conn, message):    # Connecter.Connection
        """ Handle message for overlay swarm and return if the message is valid """

        if DEBUG:
            print "overlay: Got",getMessageName(message[0]),len(message)
        
        if not conn:
            return False
        t = message[0]
        
        if t in PermIDMessages:
            if not self.crs.has_key(conn) or not self.crs[conn]:    # incoming permid exchange
                cr = ChallengeResponse(self.myid,self,self.errorfunc)
                self.crs[conn] = cr
            if self.crs[conn].got_message(conn, message) == False:
                self.crs.pop(conn)
                conn.close()
        elif conn.permid:    # Do not go ahead without permid
            self.secure_overlay.gotMessage(conn.permid, message)
