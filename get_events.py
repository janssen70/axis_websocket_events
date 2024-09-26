"""
Connect to Axis devices over websocket connection and subscribe to event data
topics

Example invocations:

python3 get_events.py -c 169.254.205.195 -c 169.254.200.32 -u root -p pass -t axis:Device/tnsaxis:IO/Port -t axis:CameraApplicationPlatform/VMD/Camera1ProfileANY
python3 get_events.py -c 192.168.200.18 -u root -p pass -t onvif:AudioSource/axis:TriggerLevel
python3 get_events.py -c 192.168.200.18 -u root -p pass -t onvif:VideoAnalytics/axis:TrackEnded

# If you get this:
#
#   As of 3.10, the *loop* parameter was removed from Lock() since it is no longer necessary
#
# I solved as follows, following https://stackoverflow.com/questions/71535250/
#   
#   python3 -m pip install --upgrade websockets
"""

import sys
import json
import requests, re
import websockets
import asyncio
import hashlib
import base64
import uuid
import logging
import argparse
from datetime import datetime
import urllib3
import ssl

urllib3.disable_warnings()

logger = logging.getLogger('websockets')
logger.setLevel(logging.ERROR)
logger.addHandler(logging.StreamHandler())

def StandardSSLContext():
   """
   Return a SSL context that tells to ignore certificate validity. Maybe not a
   good idea in general but it serves the testing purpose of this script
   """
   ctx = ssl.create_default_context()
   ctx.check_hostname = False
   ctx.verify_mode = ssl.CERT_NONE
   return ctx

#-------------------------------------------------------------------------------
#
#  Metadata parsers                                                         {{{1
#
#-------------------------------------------------------------------------------

class NotificationHandler:
   """
   Baseclass to offer some structure in handling the various event
   responses from the device 

   This is not complete at all
   """
   def __init__(self):
      pass

   def _parse_ts(self, ts: int) -> str:
      """
      Create string representation of timestamp
      """
      return datetime.fromtimestamp(ts / 1000).strftime('%Y-%m-%d %H:%M:%S')

   def dump(self, device, notification):
      """
      Basic default notification parameter dump
      """
      ts = self._parse_ts(notification['timestamp'])
      source = ' '.join([f'{a}={b}' for a,b in notification['message']['source'].items()])
      data = ' '.join([f'{a}={b}' for a,b in notification['message']['data'].items()])
      print(f'{ts} {device}: {notification["topic"]} {source} {data}')


class TrackEndedHandler(NotificationHandler):
   """
   Handles a track. To get these events you need to apply some configuration first:

   - Set feature flag selected_metadata_events 
     https://www.axis.com/vapix-library/subjects/t10175981/section/t10177668/display

   - Enable analyics metadata producer
     https://www.axis.com/vapix-library/subjects/t10175981/section/t10178746/display
   """
   def dump(self, device, notification):
      ts = self._parse_ts(notification['timestamp'])
      print('Track:   bottom left   right  top    timestamp')  
      print('         ------ ------ ------ ------ ---------------------------')  
      data = notification['message']['data']
      for obs in json.loads(data['observations']):
         print('         {:>6} {:>6} {:>6} {:>6} {}'.format(
             obs['bounding_box']['bottom'],
             obs['bounding_box']['left'],
             obs['bounding_box']['right'],
             obs['bounding_box']['top'],
             obs['timestamp']
         ))
      if len(data['classes']):
         classes = json.loads(data['classes'])
         for _cls in classes:
            print(f'Class: {_cls["type"]} Score: {_cls["score"]}')
         # Todo: add the other parts like upper_clothing_colors
      print('')

handler_db = {
   'tns1:VideoAnalytics/tnsaxis:TrackEnded': TrackEndedHandler(),
   '*': NotificationHandler()
}

def handle_notification(device, notification):
   """
   Find correct handler or pass to the generic one
   """
   t = notification['topic']
   handler_db[t if t in handler_db else '*'].dump(device, notification)

#-------------------------------------------------------------------------------
#
#  Communication                                                            {{{1
#
#-------------------------------------------------------------------------------

def calculate_digest_response(username, realm, password, uri, method, nonce, nc, cnonce):
    ha1 = hashlib.md5(f'{username}:{realm}:{password}'.encode()).hexdigest()
    ha2 = hashlib.md5(f'{method}:{uri}'.encode()).hexdigest()
    response = hashlib.md5(
        f'{ha1}:{nonce}:{nc}:{cnonce}:auth:{ha2}'.encode()).hexdigest()
    return response


async def metadata_websocket_connection(device, topics, username, password, nonce, realm, secure = False):
   """
   """
   uri = '/vapix/ws-data-stream?sources=events'
   method = 'GET'

   nc = '00000001'
   cnonce = str(uuid.uuid4())
   response = calculate_digest_response(username, realm, password, uri, method, nonce, nc, cnonce)
   websocket_uri = f'ws{"s" if secure else ""}://{device}{uri}'
   websocket_headers = {
      'Authorization': f'Digest username="{username}", realm="{realm}", nonce="{nonce}", uri="{uri}", response="{response}", qop=auth, nc={nc}, cnonce="{cnonce}"'
   }

   try:
      kwargs = {
        'extra_headers': websocket_headers,
      }
      if secure:
         kwargs['ssl'] = StandardSSLContext()

      async with websockets.connect(websocket_uri, **kwargs) as websocket:
         payload = json.dumps({
            'apiVersion': '1.0',
            'method': 'events:configure',
            'params': {
               'eventFilterList': [{'topicFilter': p} for p in topics]
            }
         })
         await websocket.send(payload)

         while True:
            message = await websocket.recv()
            m = json.loads(message)
            # This is draft code that only deals with some events
            if m['method'] == 'events:notify':
               handle_notification(device, m['params']['notification'])
            else:
               print(device, m)

            # Flush stdout to help running the script in a systemd service, using
            # StandardOutput=append:/path_to_file

            sys.stdout.flush()

   except Exception as e:
      print(e)

async def event_listener_task(device, topics, username, password, secure = False):
   """
   Enforce a http login so that we get to know digest nonce and realm, which
   we can then use in the websocket connection. Then connect to the websocket
   """
   # url = f'http://{device}/axis-cgi/login.cgi'
   url = f'http{"s" if secure else ""}://{device}/axis-cgi/param.cgi?action=list&group=Brand&usergroup=viewer'

   response = requests.get(url, verify = False)
   nonce = None
   realm = None
   auth_info = response.headers.get('WWW-Authenticate')
   match = re.search(r'nonce="([^"]+)"', auth_info)
   if match:
      nonce = match.group(1)
   match = re.search(r'realm="([^"]+)"', auth_info)
   if match:
      realm = match.group(1)
   if nonce and realm:
      response = requests.get(url, auth=requests.auth.HTTPDigestAuth(username, password), verify = False)
      await metadata_websocket_connection(device, topics, username, password, nonce, realm, secure)
   else:
      print('Failed to obtain authentication prerequisites')

async def main(args):
   """
   Create a task for each specified device, then run and wait for them
   """
   tasks = []
   for c in args.camera:
      tasks.append(
         asyncio.create_task(
             event_listener_task(c, args.topic, args.user, args.password, args.secure)
         )
      )
   await asyncio.gather(*tasks)

#-------------------------------------------------------------------------------
#
#  Main program                                                             {{{1
#
#-------------------------------------------------------------------------------

if __name__ == '__main__':

    parser = argparse.ArgumentParser(
            description='Websocket test'
            )

    parser.add_argument(
            '-c', '--camera', type=str, action = 'append',
            help = 'Hostname/IP address of the device(s) to interact with')
    parser.add_argument(
            '-u', '--user', type=str, default='root',
            help = 'username to login with (root)')
    parser.add_argument(
            '-p', '--password', type=str, default='pass',
            help = 'password to login with (pass)')
    parser.add_argument(
            '-t', '--topic', type = str, action = 'append',
            help = 'Event topic to listen to')
    parser.add_argument(
            '-s', '--secure', action='store_true',
            help = 'Use secure protocols')

    args = parser.parse_args()
    if args.camera is None:
       parser.print_usage()
       exit(1)

    asyncio.run(main(args))

#  vim: set nowrap sw=3 sts=3 et fdm=marker:
