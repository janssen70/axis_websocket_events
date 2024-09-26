# axis_websocket_events
Receive events from Axis devices over websocket api

This script performs an authenticated connect over websocket to Axis devices and subscribes to events. It supports multiple devices and multiple events at the same time. So, suppose you have n devices sharing the same credentials, and m events to listen to, you can monitor these n * m events at ease.

Example invocation, tracks two events on two devices:

```
python3 get_events.py -c 169.254.205.195 -c 169.254.200.32 -u root -p pass -t axis:Device/tnsaxis:IO/Port -t axis:CameraApplicationPlatform/VMD/Camera1ProfileANY
```

Tested with Python 3.10, 3.11 and 3.12

Credits to @vivekatoffice for providing the websocket auth part, although I changed some bits
