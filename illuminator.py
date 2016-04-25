#dependencies
# --cython-hid
# --OSC

import hid
import math
import random
import time
from time import sleep
from noise import pnoise1
import socket, OSC, threading
import ctypes

#IP Adress and outgoing port to listen on
receive_address = ('0.0.0.0', 7000)

header_byte = 0xa9
footer_byte = 0x5c

#specific to the lamp used
vendor_id = 0x24c2
product_id = 0x1306

#time variables for timed transition
current_time = 0.0
previous_time = 0.0
delta_time = 0.0

beating = False
is_blinking = False

#-------------------------HELPER METHODS--------------------------------
#compute the 2's compliment of int value val
def twos_comp(val):
    if (val & (1 << (7))) != 0: # if sign bit is set e.g., 8bit: 128-255
        val = val - (1 << 8)        # compute negative value
    return val                         # return positive value as is

#convert from int to byte
def tobyte(data):
    return bytes(bytearray([data]))

#sum a given array of bytes
def sum_data_bytes(message):
    total = sum(message)
    mod = total % 256

    return mod

#clamp a vale between 0 and 255 and returns as integer
def clamp(val):
    return max(min(255, int(val)), 0)

#ramp a value from a to b over t seconds
def ramp(a, b, t, delta):
    i = max (a, b)

    if t < 1:
        t = t*t

    if a < b:
        a = a + 1
    else:
        a = a - 1

    return a

def terminate_thread(thread):
    """Terminates a python thread from another thread.

    :param thread: a threading.Thread instance
    """
    if not thread.isAlive():
        return

    exc = ctypes.py_object(SystemExit)
    res = ctypes.pythonapi.PyThreadState_SetAsyncExc(
        ctypes.c_long(thread.ident), exc)
    if res == 0:
        raise ValueError("nonexistent thread id")
    elif res > 1:
        # """if it returns a number greater than one, you're in trouble,
        # and you should call it again with exc=NULL to revert the effect"""
        ctypes.pythonapi.PyThreadState_SetAsyncExc(thread.ident, None)
        raise SystemError("PyThreadState_SetAsyncExc failed")

class Color:
    def __init__(self, r, g, b):
        self.r = clamp(r)
        self.g = clamp(g)
        self.b = clamp(b)

    def __str__(self):
        return "color(%i, %i, %i)" % (self.r, self.g, self.b)

    def lerp(self, origin, target, lerp_val):
        self.r = origin.r + lerp_val*(target.r-origin.r)
        self.g = origin.g + lerp_val*(target.g-origin.g)
        self.b = origin.b + lerp_val*(target.b-origin.b)
        return Color(self.r, self.g, self.b)

    def distance(self, other):
        return max(max(abs(self.r - other.r), abs(self.g - other.g)), abs(self.b - other.b))


BLACK = Color(0, 0, 0)
WHITE = Color(255, 255, 255)

color_thread = None

class Illuminator:
    def __init__(self, path):
        self.path = path
        self.color = BLACK
        self.blink = 0
        self.connection = hid.device(None, None, path)
        self.connection.set_nonblocking(1)
        self.turn_off()

    def close(self):
        self.connection.close()

    def turn_off(self):
        self.set(Color(0, 0, 0))

    def set(self, color):
        try:
            data = [0x6, 0x1, color.r, color.g, color.b, self.blink]
            checksum = -(twos_comp(sum_data_bytes(data))) % 256
            message  = [header_byte] + data + [checksum, footer_byte]
            self.connection.write(message)
            self.color = color

        except IOError, ex:
            print ex
            self.connection.close()

    def set_blinking(self, color, blink):
        try:
            data = [0x6, 0x1, color.r, color.g, color.b, blink]
            checksum = -(twos_comp(sum_data_bytes(data))) % 256
            message  = [header_byte] + data + [checksum, footer_byte]
            self.connection.write(message)
            self.color = color

        except IOError, ex:
            print ex
            self.connection.close()





#-------------------------COLOR METHODS--------------------------------
#heartbeat ramps from the current color to the target color over t seconds, then ramps back down to the starting color
def heartbeat(illuminators, target, duration):
    start_time = time.clock()
    previous_color = Color(illuminators[0].color.r, illuminators[0].color.g, illuminators[0].color.b)
    thresh = 1

    current_time = 0
    previous_time = 0
    delta_time = 0
    lerp_val = 0

    #ramp value up
    for illuminator in illuminators:
        while illuminator.color.distance(target) > thresh:
            previous_time = current_time
            current_time = time.clock()
            delta_time = current_time - previous_time

            illuminator.set(illuminator.color.lerp(previous_color, target, lerp_val))
            lerp_val = lerp_val + (0.001/duration)

    lerp_val = 0

    #ramp value down
    for illuminator in illuminators:
        while illuminator.color.distance(previous_color) > thresh:
            previous_time = current_time
            current_time = time.clock()
            delta_time = current_time - previous_time

            illuminator.set(illuminator.color.lerp(target, previous_color, lerp_val))
            lerp_val = lerp_val + (0.001/duration)
    print 'heartbeat set over %r second(s) back to %s -- DONE' % ((current_time - start_time), previous_color)


def transition(illuminators, target, duration):
    start_time = time.clock()

    lerp_val = 0
    thresh = 2
    previous_color = Color(illuminators[0].color.r, illuminators[0].color.g, illuminators[0].color.b)

    print "changing pair color to %s over %r seconds..." % (target, duration)

    current_time = 0
    previous_time = 0
    delta_time = 0
    for illuminator in illuminators: #TODO define function that checks for distance without having a for loop on 198
        while illuminator.color.distance(target) > thresh:
            previous_time = current_time
            current_time = time.clock()
            delta_time = current_time - previous_time

            illuminator.set(illuminator.color.lerp(previous_color, target, lerp_val))
            lerp_val = lerp_val + (0.2 / duration);

    illuminator.set(target)

    print "...changed color over %r seconds -- DONE" % ((current_time - start_time))

def flicker(illuminators, color, blink_rate):
    for illuminator in illuminators:
        illuminator.set_blinking(color, blink_rate)

def random_flicker(illuminators, color, threshold, start_time, duration):
    while True:
        for illuminator in illuminators:
            if random.random() > threshold:
                illuminator.set(color)
                time.sleep(0.1)
            else:
                illuminator.set(Color(0, 0, 0))
                time.sleep(0.05)
        if time.clock() > (start_time + duration*0.000001):
            is_blinking = False
            illuminator.set(color)
            print "done blinking"


def noise_color(illuminator, color, duration):
    frame_count = 0
    r = color[0]
    g = color[1]
    b = color[2]

    while frame_count < 100:
        #do noise stuff


        frame_count = frame_count + 1
        illuminators[0].set(r, g, b)

    print "done with noise"



#-------------------------LIST USB DEVICES--------------------------------
print "available usb devices:"

illuminators = []

for d in hid.enumerate(0, 0):
    keys = d.keys()
    keys.sort()
    # for key in keys:
    #     print "%s : %s" % (key, d[key])
    if d["product_id"] == product_id: # and d["vendor_id"] is vendor_id:
        illuminators.append(Illuminator(d["path"]))

if len(illuminators) > 2 or len(illuminators) == 0:
    print "unexpected amount of light"
    exit(1)
else:
    print "succesfully lit illuminator(s): %r" % illuminators


##########################
#	OSC
##########################

# Initialize the OSC server and the client.
s = OSC.OSCServer(receive_address)
s.request_queue_size = 0
s.addDefaultHandlers()


#default handler prints out the message
def handle_root(addr, tags, data, source):
	print "---"
	print "received new osc msg from %s" % OSC.getUrlStr(source)
	print "with addr : %s" % addr
	print "typetags %s" % tags
	print "data %s" % data
	print "---"

def handle_change(addr, tags, data, source):
    global color_thread
    if color_thread is not None:
        terminate_thread(color_thread)
        color_thread = None

    color = Color(data[0], data[1], data[2])
    duration = data[3]
    if (duration < 10):
        for illuminator in illuminators:
            illuminator.set(color)
    else:
        print "handling change color %s over %rms" % (color, duration)
        color_thread = threading.Thread(target=transition, args=(illuminators, color, duration))
        color_thread.start()

prev_color = None
def handle_color(addr, tags, data, source):
    global prev_color
    global color_thread

    if color_thread is not None:
        terminate_thread(color_thread)
        color_thread = None
        color = Color(data[0], data[1], data[2])
        if str(color) ==  prev_color:
            return
        print "setting color to %s" % color
        prev_color = str(color)
        for illuminator in illuminators:
            illuminator.set(color)

def handle_heartbeat(addr, tags, data, source):
    global beating
    color = Color(data[0], data[1], data[2])
    duration = data[3]*0.00000001
    print "received message with beating is %r" % beating
    beating = True
    heartbeat(illuminators, color, duration)

def handle_black(addr, tags, data, source):
    for illuminator in illuminators:
        illuminator.turn_off()

def start_blink(addr, tags, data, source):
    color = Color(data[0], data[1], data[2])
    rate = data[3]
    flicker(illuminators, color, rate)

def enable_blink(addr, tags, data, source):
    print "enable blink"
    # global is_blinking
    # is_blinking = True
    start_time = time.clock()
    color = Color(data[0], data[1], data[2])
    threshold = data[3]
    duration = data[4]
    # random_flicker(illuminators, color, threshold, start_time, duration)
    global color_thread
    color_thread = threading.Thread(target=random_flicker, args=(illuminators, color, threshold, start_time, duration))
    color_thread.start()

def disable_blink(addr, tags, data, source):
    global is_blinking
    is_blinking = False

def stop_blink(addr, tags, data, source):
    for illuminator in illuminators:
        illuminator.set(Color(0, 0, 0))


def handle_noise(addr, tags, data, source):
    noise_color(data)


print "here they are %r" % illuminators


def handle_endless(addr, tags, data, source):
    global color_thread
    color_thread = threading.Thread(target=endless)
    color_thread.start()

def handle_break(addr, tags, data, source):
    global color_thread
    terminate_thread(color_thread)
    color_thread = None


print "endpoints:"
print "---"
print "/change r g b t ---- changes the color to the specified rgb values over t ms"
print "/color r g b ---- changes the color immediately"
print "/heartbeat r g b t --- pulsates to the target color over t ms"
print "/black --- turns off the lamp"
print "---"

s.addMsgHandler('/', handle_root)
s.addMsgHandler('/change', handle_change)
s.addMsgHandler('/color', handle_color)
s.addMsgHandler('/heartbeat', handle_heartbeat)
s.addMsgHandler('/black', handle_black)
s.addMsgHandler('/noise', handle_noise)

s.addMsgHandler('/start_blink', start_blink)
s.addMsgHandler('/stop_blink', stop_blink)
s.addMsgHandler('/enable_blink', enable_blink)
s.addMsgHandler('/disable_blink', disable_blink)

s.addMsgHandler('/endless', handle_endless)
s.addMsgHandler('/break', handle_break)

#Start OSCServer
print "\nStarting OSCServer. Use ctrl-C to quit."
# st = threading.Thread( target = s.serve_forever )
# st.start()
while True:
    if beating is False:
        s.handle_request()

#Threads
try :
	while 1 :
		time.sleep(10)

except KeyboardInterrupt :
    for illuminator in illuminators:
        illuminator.turn_off()
    print "\nClosing OSCServer."
    s.close()
    print "Waiting for Server-thread to finish"
    st.join()
    print "Done"


print "...lights off!"
