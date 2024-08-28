"""
Connect to Axis devices over websocket connection and subscribe to event data
topics

Example invocation:
python3 get_events.py -c 169.254.205.195 -c 169.254.200.32 -u root -p pass -t axis:Device/tnsaxis:IO/Port -t axis:CameraApplicationPlatform/VMD/Camera1ProfileANY
python3 get_events.py -c 192.168.200.18 -u root -p pass -t onvif:AudioSource/axis:TriggerLevel

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

logger = logging.getLogger('websockets')
logger.setLevel(logging.ERROR)
logger.addHandler(logging.StreamHandler())

def calculate_digest_response(username, realm, password, uri, method, nonce, nc, cnonce):
    ha1 = hashlib.md5(f"{username}:{realm}:{password}".encode()).hexdigest()
    ha2 = hashlib.md5(f"{method}:{uri}".encode()).hexdigest()
    response = hashlib.md5(
        f"{ha1}:{nonce}:{nc}:{cnonce}:auth:{ha2}".encode()).hexdigest()
    return response


async def metadata_websocket_connection(device, topics, username, password, nonce, realm):
   """
   """
   uri = '/vapix/ws-data-stream?sources=events'
   method = 'GET'

   nc = '00000001'
   cnonce = str(uuid.uuid4())
   response = calculate_digest_response(username, realm, password, uri, method, nonce, nc, cnonce)
   websocket_uri = f'ws://{device}/vapix/ws-data-stream?sources=events'
   websocket_headers = {
      "Authorization": f'Digest username="{username}", realm="{realm}", nonce="{nonce}", uri="{uri}", response="{response}", qop=auth, nc={nc}, cnonce="{cnonce}"'
   }

   try:
      async with websockets.connect(websocket_uri, extra_headers=websocket_headers) as websocket:
         payload = json.dumps({
            "apiVersion": "1.0",
            "method": "events:configure",
            "params": {
               "eventFilterList": [{"topicFilter": p} for p in topics]
            }
         })
         await websocket.send(payload)

         while True:
            message = await websocket.recv()
            m = json.loads(message)
            if m['method'] == 'events:notify':
               n = m['params']['notification']
               # This is draft code that only deals with some events
               ts = datetime.fromtimestamp(n['timestamp'] / 1000).strftime("%Y-%m-%d %H:%M:%S")
               source = ' '.join([f'{a}={b}' for a,b in n['message']['source'].items()])
               data = ' '.join([f'{a}={b}' for a,b in n['message']['data'].items()])
               print(f'{ts} {device}: {n["topic"]} {source} {data}')
            else:
               print(device, m)

            # Flush stdout to help running the script in a systemd service, using
            # StandardOutput=append:/path_to_file

            sys.stdout.flush()

   except Exception as e:
      print(e)

async def event_listener_task(device, topics, username, password):
   """
   Enforce a http login so that we get to know digest nonce and realm, which
   we can then use in the websocket connection. Then connect to the websocket
   """
   # url = f'http://{device}/axis-cgi/login.cgi'
   url = f'http://{device}/axis-cgi/param.cgi?action=list&group=Brand&usergroup=viewer'

   response = requests.get(url)
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
      response = requests.get(url, auth=requests.auth.HTTPDigestAuth(username, password))
      await metadata_websocket_connection(device, topics, username, password, nonce, realm)
   else:
      print('Failed to obtain authentication prerequisites')

async def main(args):
   """
   Create a task for each specified device, then run and wait for them
   """
   tasks = []
   for c in args.camera:
      tasks.append(asyncio.create_task(event_listener_task(c, args.topic, args.user, args.password)))
   await asyncio.gather(*tasks)

if __name__ == "__main__":

    parser = argparse.ArgumentParser(
            description='Websocket test'
            )

    parser.add_argument(
            '-c', '--camera', type=str, action = 'append',
            help='Hostname/IP address of the device(s) to interact with')
    parser.add_argument(
            '-u', '--user', type=str, default='root',
            help='username to login with (root)')
    parser.add_argument(
            '-p', '--password', type=str, default='pass',
            help='password to login with (pass)')
    parser.add_argument(
            '-t', '--topic', type = str, action = 'append',
            help='Event topic to listen to')

    args = parser.parse_args()
    if args.camera is None:
       parser.print_usage()
       exit(1)

    asyncio.run(main(args))


#  vim: set nowrap sw=3 sts=3 et fdm=marker:
