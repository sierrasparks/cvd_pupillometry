# -*- coding: utf-8 -*-
'''
Created on Wed Jun 17 15:21:46 2020

@author: JTM
'''
from time import time

import numpy as np
import msgpack
import zmq

class PupilCore():
    '''
    A simple class for Pupil Core and the remote helper.
    '''
    def __init__(self, address='127.0.0.1', request_port='50020'):
        self.context = zmq.Context()
        self.address = address
        self.request_port = request_port
        self.remote = zmq.Socket(self.context, zmq.REQ)
        self.remote.connect('tcp://{}:{}'.format(self.address, 
                                                 self.request_port))

        # Request 'SUB_PORT' for reading data
        self.remote.send_string('SUB_PORT')
        self.sub_port = self.remote.recv_string()
        
        # Request 'PUB_PORT' for writing data
        self.remote.send_string('PUB_PORT')
        self.pub_port = self.remote.recv_string()

    def command(self, cmd):
        '''
        Send a command via Pupil Remote. 

        Parameters
        ----------
        cmd : string
            Must be one of the following:
                
            'R'          - start recording with auto generated session name
            'R rec_name' - start recording named "rec_name" 
            'r'          - stop recording
            'C'          - start currently selected calibration
            'c'          - stop currently selected calibration
            'T 1234.56'  - resets current Pupil time to given timestamp
            't'          - get current Pupil time; returns a float as string
            'v'          - get the Pupil Core software version string
            'PUB_PORT'   - return the current pub port of the IPC Backbone 
            'SUB_PORT'   - return the current sub port of the IPC Backbone

        Returns
        -------
        string
            the result of the command. If the command was not acceptable, this
            will be 'Unknown command.'

        '''
        self.remote.send_string(cmd)
        return self.remote.recv_string()
    
    def notify(self, notification):
        '''
        Send a notification to Pupil Remote. Every notification has a topic 
        and can contain potential payload data. The payload data has to be 
        serializable, so not every Python object will work. To find out which
        plugins send and receive notifications, open the codebase and search 
        for `.notify_all(` and `def on_notify(`. 
    
        Parameters
        ----------
        pupil_remote : zmq.sugar.socket.Socket
            the pupil remote helper.
        notification : dict
            the notification dict. Some examples:
                
            - {'subject':'start_plugin', 'name':'Annotation_Capture', 'args':{}}) 
            - {'subject':'recording.should_start', 'session_name':'my session'}
            - {'subject':'recording.should_stop'}
            
        Returns
        -------
        string
            the response.

        '''
        topic = 'notify.' + notification['subject']
        payload = msgpack.dumps(notification, use_bin_type=True)
        self.remote.send_string(topic, flags=zmq.SNDMORE)
        self.remote.send(payload)
        return self.remote.recv_string()

def notify(pupil_remote, notification):
    '''
    Send a notification to Pupil Remote.

    Parameters
    ----------
    pupil_remote : zmq.sugar.socket.Socket
        the pupil remote helper.
    notification : dict
        the notification dict. 
        e.g. {'subject':'start_plugin', 'name':'Annotation_Capture'}
    Returns
    -------
    string
        the response.

    '''
    topic = 'notify.' + notification['subject']
    payload = msgpack.dumps(notification, use_bin_type=True)
    pupil_remote.send_string(topic, flags=zmq.SNDMORE)
    pupil_remote.send(payload)
    return pupil_remote.recv_string()

def send_trigger(pub_socket, trigger):
    '''
    Send an annotation (a.k.a 'trigger') to Pupil Capture. Use to mark the 
    timing of events.
    
    Parameters
    ----------
    pub_socket : zmq.sugar.socket.Socket
        a socket to publish the trigger.
    trigger : dict
        customiseable - see the new_trigger(...) function.

    Returns
    -------
    None.

    '''
    payload = msgpack.dumps(trigger, use_bin_type=True)
    pub_socket.send_string(trigger['topic'], flags=zmq.SNDMORE)
    pub_socket.send(payload)
    
def new_trigger(label, custom_fields={}):
    '''
    Create a new trigger / annotation / message / event marker / whatever 
    you want to call it. Send it to Pupil Capture with the send_trigger(...) 
    function.

    Parameters
    ----------
    label : string
        A label for the event.
    custom_fields : dict, optional
        Any additional information to add (e.g. {'duration':2, 'color':'blue'}). 
        The default is {}.

    Returns
    -------
    trigger : dict
        The trigger dictionary, ready to be sent.

    '''
    trigger = {
        'topic' : 'annotation',
        'label' : label,
        'timestamp' : time()
        }
    for k, v in custom_fields.items():
        trigger[k] = v
    return trigger

def recv_from_subscriber(subscriber):
    '''
    Receive a message with topic and payload.
    
    Parameters
    ----------
    subscriber : zmq.sugar.socket.Socket
        a subscriber to any valid topic.

    Returns
    -------
    topic : str
        A utf-8 encoded string, returned as a unicode object.
    payload : dict
        A msgpack serialized dictionary, returned as a python dictionary.
        Any addional message frames will be added as a list in the payload 
        dictionary with key: '__raw_data__'. To use frame data, say: 
        np.frombuffer(msg['__raw_data__'][0], dtype=np.uint8).reshape(
                msg['height'], msg['width'], 3)
        
    '''
    topic = subscriber.recv_string()
    payload = msgpack.unpackb(subscriber.recv(), encoding='utf-8')
    extra_frames = []
    while subscriber.get(zmq.RCVMORE):
        extra_frames.append(subscriber.recv())
    if extra_frames:
        payload['__raw_data__'] = extra_frames
    return topic, payload

def detect_light_onset(subscriber, 
                       pub_socket, 
                       trigger, 
                       threshold,
                       wait_time=None):
    '''
    Use the Pupil Core World Camera to detect the onset of a light and send 
    an annotation (a.k.a 'trigger') to Pupil Capture with the associated 
    Pupil timestamp. Useful for extracting PLRs and calculating time-critical
    measures such as latency and time-to-peak consctriction. Start this function 
    before administering a light stimulus, and use a separate thread if controlling
    the light programmatically from the same script. Tested with the following 
    settings in Pupil Capture:
        
    1. Resolution (320, 240) for eye and world
    2. Frame rate 120 for eye and world
    3. Auto Exposure mode - Manual Exposure - eye and wold
    4. Absolute exposure time 60 for world, 63 for eye
    5. Frame publisher format - BGR
    
    Parameters
    ----------
    subscriber : zmq.sugar.socket.Socket
        a socket subscribed to 'frame.world' 
    pub_socket : zmq.sugar.socket.Socket
        a socket to publish the trigger using send_trigger(...)
    trigger : dict
        a dictionary with at least the following:
            
        {'topic': 'annotation',
         'label': 'our_label',
         'timestamp': None}
        
        timestamp will be overwritten with the new pupil timestamp for the 
        detected light. See new_trigger(...) for more info.
    threshold : int
        detection threshold for luminance increase. The right value depends on
        the nature of the light stimulus and the ambient lighting conditions. 
        Requires some guesswork right now, but it would be good to have a 
        function that works it out for us. 
    wait_time : float, optional
        time to wait in seconds before giving up (will run indefinitely if
        no value is passed). Use when controlling a light source programmatically 
        / running the function in its own thread. For STLAB, use 6.0 s, 
        because on rare occasions it can take about 5 seconds to process a
        request. The default in None.
        
    Returns
    -------
    None

    '''
    recent_world = None
    recent_world_minus_one = None
    recent_world_ts = None
    detected = False
    if wait_time is None:
        wait_time, t1, t2 = 0, -1, -2 # dummy values
    else:
        t1 = time()
        t2 = time()
    print('Waiting for the light...')
    while not detected and (t2 - t1) < wait_time:
        topic, msg = recv_from_subscriber(subscriber)
        if topic == 'frame.world':
            recent_world = np.frombuffer(
                msg['__raw_data__'][0], dtype=np.uint8).reshape(
                    msg['height'], msg['width'], 3)
            recent_world_ts = msg['timestamp']
        if recent_world is not None and recent_world_minus_one is not None:
            diff = recent_world.mean() - recent_world_minus_one.mean()
            if diff > threshold:
                print('Light detected at {}'.format(recent_world_ts))
                trigger['timestamp'] = recent_world_ts # change trigger timestamp
                send_trigger(pub_socket, trigger)
                detected = True
                break # not sure if this is required
        recent_world_minus_one = recent_world
        if wait_time > 0:
            t2 = time()
    if detected == False:
        print('Failed to detect a light.')
        
# def find_threshold(subscriber):
#     world_data = []
#     print('Shine a light...')
#     while True:
#         topic, msg = recv_from_subscriber(subscriber)
#         recent_world = np.frombuffer(
#             msg['__raw_data__'][0], dtype=np.uint8).reshape(
#                 msg['height'], msg['width'], 3)
#         world_data.append(recent_world.mean())
